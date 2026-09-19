import hashlib
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.schemas.form_understanding import Attestation, FieldRevision
from app.schemas.models import SlotCorrection
from app.services import audit_store
from app.services.capture import new_run_dir, preprocess, save_upload
from app.services.fhir import to_questionnaire_response
from app.services.form_analyzer import FormAnalyzer
from app.services.regeneration import (
    loose_values,
    regenerate,
    ticks_from_analysis,
    unplaced,
    values_from_analysis,
)
from app.services.template_builder import derive_template, learn_corrections
from app.services.templates import registry

app = FastAPI(title="PrelimMD", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3002",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3002",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Correction(BaseModel):
    value: object
    reviewed: bool = True
    # Who is replacing the value. Recorded against the reading being superseded, because
    # "who last touched this field" is the question a chart review asks first.
    corrected_by: str = "reviewer"


def run_dir(run_id: str) -> Path:
    path = settings.artifact_root.resolve() / run_id
    if not path.is_dir():
        raise HTTPException(404, "Run not found")
    return path


@app.get("/api/v1/health")
def health():
    return {
        "status": "ok",
        "schema_version": "1.0",
        "vision_provider": settings.vision_provider,
        # Which reader is live decides every image number this project reports, and there
        # was no way to ask. A benchmark run against a server started with a different
        # engine -- or a stale one -- looks exactly like a real result.
        "ocr_engine": settings.ocr_engine,
        "templates": [t.template_id for t in registry.all()],
        # A silent audit trail is worse than none, because it looks like one.
        "audit": audit_store.status(settings.artifact_root.resolve()),
    }


@app.post("/api/v1/form-analyses", status_code=202)
async def create_form_analysis(
    file: UploadFile | None = File(None),
    files: list[UploadFile] | None = File(None),
    template_id: str | None = Form(None),
):
    """Analyse an uploaded form.

    ``files`` accepts a form photographed a page at a time, which is how a patient with a
    phone sends a two-page packet; ``file`` remains for the single-page and PDF case that
    every existing client uses. A PDF already carries its own pages, so only the first is
    read when both arrive.
    """
    pages = [f for f in (files or []) if f is not None and f.filename] or (
        [file] if file is not None else []
    )
    if not pages:
        raise HTTPException(422, "Upload a file as `file`, or one per page as `files`")
    analysis_id, folder = new_run_dir()
    source, kind = await save_upload(pages[0], folder)
    if kind != "pdf":
        await run_in_threadpool(preprocess, source, kind, folder)
        for index, extra in enumerate(pages[1:], start=1):
            if index >= settings.max_pdf_pages:
                break
            more, more_kind = await save_upload(extra, folder, index)
            if more_kind == "pdf":
                raise HTTPException(
                    422, "Pages sent together must all be images; send a PDF on its own"
                )
            await run_in_threadpool(preprocess, more, more_kind, folder, index)
    # Off the event loop: reading a page is seconds of OCR, and on the loop it held every
    # other request -- the health check included -- and serialised concurrent uploads.
    analysis = await run_in_threadpool(FormAnalyzer().analyze, analysis_id, folder, template_id)
    (folder / "analysis.json").write_text(analysis.model_dump_json(indent=2))
    from app.services.history import patient_key

    audit_store.record(
        settings.artifact_root.resolve(),
        analysis_id,
        "extracted",
        f"Read {len(analysis.detected_fields)} fields, "
        f"{sum(f.needs_review for f in analysis.detected_fields)} flagged for review",
        patient_key=patient_key(analysis),
    )
    return analysis


@app.get("/api/v1/form-analyses")
def list_form_analyses(limit: int = 50):
    """Stored forms, newest first, for the browser."""
    from app.schemas.form_understanding import FormAnalysis

    root = settings.artifact_root.resolve()
    if not root.is_dir():
        return {"analyses": []}

    stored = []
    for folder in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        record = folder / "analysis.json"
        if not record.is_file():
            continue
        try:
            analysis = FormAnalysis.model_validate_json(record.read_text())
        except ValueError:
            continue  # a partial or older record shouldn't break the listing
        stored.append(
            {
                "analysis_id": analysis.analysis_id,
                "source_type": analysis.source_type,
                "field_count": len(analysis.detected_fields),
                "review_count": sum(f.needs_review for f in analysis.detected_fields),
                "matched_known_template": analysis.matched_known_template,
                "stored_at": datetime.fromtimestamp(record.stat().st_mtime, tz=UTC).isoformat(),
            }
        )
        if len(stored) >= limit:
            break
    return {"analyses": stored}


@app.get("/api/v1/form-analyses/{analysis_id}")
def get_form_analysis(analysis_id: str):
    from app.schemas.form_understanding import FormAnalysis

    return FormAnalysis.model_validate_json((run_dir(analysis_id) / "analysis.json").read_text())


@app.get("/api/v1/form-analyses/{analysis_id}/history")
def form_history(analysis_id: str):
    """Everything that has happened to one form, oldest first.

    Read from the run folder while it exists, because that record carries the detail --
    every field's own revisions -- and the audit store deliberately keeps only the events.
    When the folder is gone the store answers instead: an audit trail that disappeared
    with the cache it was filed in would not have been one. ``durable_events`` says how
    much of what is shown is backed by the store, so a thin trail is visible as thin
    rather than looking complete.
    """
    from app.schemas.form_understanding import FormAnalysis
    from app.services.history import form_timeline, patient_key, read_at

    root = settings.artifact_root.resolve()
    filed = audit_store.events_for(root, analysis_id)
    folder = run_dir(analysis_id)
    record = folder / "analysis.json"
    if not record.is_file():
        if not filed:
            raise HTTPException(404, f"No analysis {analysis_id}")
        return {
            "analysis_id": analysis_id,
            "patient_key": next((e["patient_key"] for e in filed if e["patient_key"]), None),
            "source": "audit-store",
            "durable_events": len(filed),
            "events": [
                {"at": e["at"], "kind": e["kind"], "summary": e["summary"],
                 "field_id": e["field_id"], "label": e["label"], "was": e["was"],
                 "now": e["now"], "by": e["by_whom"]}
                for e in filed
            ],
        }
    analysis = FormAnalysis.model_validate_json(record.read_text())
    stored = read_at(folder)
    return {
        "analysis_id": analysis_id,
        "patient_key": patient_key(analysis),
        "source": "run-folder",
        "durable_events": len(filed),
        "events": [e._asdict() for e in form_timeline(analysis, stored)],
    }


@app.get("/api/v1/patients")
def list_patients():
    """Patients this system can name, and how many forms it holds for each.

    A form whose name or date of birth was never read has no patient key and is counted
    separately as unidentified. That count is worth showing: it is the share of the
    archive that cannot be put in front of the right chart.
    """
    from app.services.history import patient_key, stored_analyses

    people: dict[str, dict] = {}
    unidentified = 0
    for analysis, stored in stored_analyses(settings.artifact_root.resolve()):
        key = patient_key(analysis)
        if key is None:
            unidentified += 1
            continue
        entry = people.setdefault(key, {"patient_key": key, "forms": 0, "first_seen": stored})
        entry["forms"] += 1
        entry["last_seen"] = stored
    return {
        "patients": sorted(people.values(), key=lambda p: p["last_seen"], reverse=True),
        "unidentified_forms": unidentified,
    }


@app.get("/api/v1/patients/{patient_key}/history")
def patient_history(patient_key: str):
    """One patient's forms over time, and what changed between consecutive ones.

    Only fields *both* forms answered are compared -- see ``history.diff``. A key one form
    asks for and the other does not is a difference between questionnaires, not a change
    in the patient, and reporting it as one would be a fabrication dressed as an audit.
    """
    from app.services.history import diff, stored_analyses
    from app.services.history import patient_key as key_for

    theirs = [
        (a, when)
        for a, when in stored_analyses(settings.artifact_root.resolve())
        if key_for(a) == patient_key
    ]
    if not theirs:
        raise HTTPException(404, f"No forms for patient {patient_key}")
    forms = []
    for index, (analysis, when) in enumerate(theirs):
        changes = diff(theirs[index - 1][0], analysis) if index else []
        forms.append(
            {
                "analysis_id": analysis.analysis_id,
                "stored_at": when.isoformat(),
                "field_count": len(analysis.detected_fields),
                "signed_off": bool(analysis.attestations),
                "changed_since_previous": [c._asdict() for c in changes],
            }
        )
    return {"patient_key": patient_key, "forms": list(reversed(forms))}


@app.get("/api/v1/form-analyses/{analysis_id}/pages/{page_index}/image")
def analysis_image(analysis_id: str, page_index: int):
    folder = run_dir(analysis_id)
    page = folder / ("normalized_color.png" if page_index == 0 else f"page_{page_index}.png")
    if not page.exists():
        raise HTTPException(404, f"No image for page {page_index}")
    return FileResponse(page, media_type="image/png")


@app.get("/api/v1/form-analyses/{analysis_id}/pages/{page_index}/overlay")
def analysis_overlay(analysis_id: str, page_index: int):
    analysis = get_form_analysis(analysis_id)
    boxes = "".join(
        f'<rect class="field {"unmapped" if not f.canonical_key else "mapped"}" data-field-id="{f.field_id}" x="{f.answer_bbox.x * 1000}" y="{f.answer_bbox.y * 1000}" width="{f.answer_bbox.width * 1000}" height="{f.answer_bbox.height * 1000}"/>'
        for f in analysis.detected_fields
    )
    return Response(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1000"><style>.field{{fill:none;stroke:#d17200;stroke-width:2}}.mapped{{stroke:#1d9a61}}</style>{boxes}</svg>',
        media_type="image/svg+xml",
    )


@app.get("/api/v1/form-analyses/{analysis_id}/debug")
def analysis_debug(analysis_id: str):
    folder = run_dir(analysis_id)
    return {
        "analysis_id": analysis_id,
        "artifacts": {
            "analysis": f"/api/v1/form-analyses/{analysis_id}",
            "image": f"/api/v1/form-analyses/{analysis_id}/pages/0/image",
            "overlay": f"/api/v1/form-analyses/{analysis_id}/pages/0/overlay",
        },
        "files": [p.name for p in folder.iterdir() if p.is_file()],
    }


@app.patch("/api/v1/form-analyses/{analysis_id}/fields/{field_id}")
def correct_analysis_field(analysis_id: str, field_id: str, correction: Correction):
    folder = run_dir(analysis_id)
    analysis = get_form_analysis(analysis_id)
    for field in analysis.detected_fields:
        if field.field_id == field_id:
            # File what is being replaced before replacing it. A correction that silently
            # overwrote the machine's reading would leave a chart review unable to tell an
            # extraction error from a patient changing their mind.
            field.history.append(
                FieldRevision(
                    value=field.normalized_value,
                    source=field.source,
                    confidence=field.confidence,
                    superseded_at=datetime.now(UTC),
                    superseded_by=correction.corrected_by,
                )
            )
            audit_store.record(
                settings.artifact_root.resolve(),
                analysis_id,
                "corrected",
                f"{field.label_text} corrected",
                field_id=field_id,
                label=field.label_text,
                was=field.normalized_value,
                now=correction.value,
                by=correction.corrected_by,
            )
            field.raw_value = correction.value
            field.normalized_value = correction.value
            field.is_blank = False
            field.confidence = 1
            field.needs_review = False
            field.source = "manual_correction"
            (folder / "analysis.json").write_text(analysis.model_dump_json(indent=2))
            return field
    raise HTTPException(404, "Field not found")


class SignOff(BaseModel):
    signed_by: str = "reviewer"
    scope: str = "form"
    # Sign off only part of a form when only part was checked. Empty means everything.
    field_ids: list[str] = Field(default_factory=list)
    note: str | None = None


def _attested_digest(analysis, field_ids: list[str]) -> str:
    """A digest of the values being attested to.

    An attestation has to name *what* was approved, not just that approval happened.
    Hashing the values means a later edit cannot inherit somebody's sign-off: the
    attestation stays on file and simply stops matching what the form now says, which is
    what a chart review needs to see.
    """
    wanted = set(field_ids) if field_ids else None
    material = [
        f"{f.field_id}={f.normalized_value!r}"
        for f in analysis.detected_fields
        if wanted is None or f.field_id in wanted
    ]
    return hashlib.sha256("\n".join(sorted(material)).encode()).hexdigest()[:16]


@app.post("/api/v1/form-analyses/{analysis_id}/sign-off", status_code=201)
def sign_off_analysis(analysis_id: str, signoff: SignOff):
    """Record that a person checked these values and stands behind them."""
    folder = run_dir(analysis_id)
    analysis = get_form_analysis(analysis_id)
    known = {f.field_id for f in analysis.detected_fields}
    unknown = [f for f in signoff.field_ids if f not in known]
    if unknown:
        raise HTTPException(404, f"No such field(s): {', '.join(unknown[:5])}")

    covered = signoff.field_ids or sorted(known)
    # Approving a form while fields are still flagged is a decision, not an error -- a
    # reviewer may well have looked at a low-confidence value and judged it right -- but
    # it is worth handing back so the caller can say so rather than discover it later.
    still_flagged = [
        f.field_id for f in analysis.detected_fields if f.field_id in set(covered) and f.needs_review
    ]
    attestation = Attestation(
        scope=signoff.scope,
        signed_by=signoff.signed_by,
        signed_at=datetime.now(UTC),
        covers=_attested_digest(analysis, signoff.field_ids),
        field_ids=signoff.field_ids,
        note=signoff.note,
    )
    analysis.attestations.append(attestation)
    (folder / "analysis.json").write_text(analysis.model_dump_json(indent=2))
    audit_store.record(
        settings.artifact_root.resolve(),
        analysis_id,
        "signed-off",
        f"Signed off by {attestation.signed_by}",
        at=attestation.signed_at,
        by=attestation.signed_by,
    )
    return {
        "attestation": attestation,
        "fields_covered": len(covered),
        "still_flagged": still_flagged,
    }


@app.get("/api/v1/form-analyses/{analysis_id}/sign-off")
def sign_off_status(analysis_id: str):
    """Whether this form's attestations still describe what it currently says."""
    analysis = get_form_analysis(analysis_id)
    out = []
    for att in analysis.attestations:
        out.append(
            {
                **att.model_dump(),
                # Stale means the values moved after the signature. The signature is not
                # wrong and is not removed; it simply no longer covers this form.
                "still_current": att.covers == _attested_digest(analysis, att.field_ids),
            }
        )
    return {
        "attestations": out,
        "signed": any(a["still_current"] for a in out),
        "fields_needing_review": sum(1 for f in analysis.detected_fields if f.needs_review),
    }


@app.post("/api/v1/templates/{template_id}/fill")
def fill_template(template_id: str, values: dict[str, object]):
    """Fill a blank template with supplied content and return the PDF.

    This is the product's main path: a caller holds a blank form and the content a patient
    gave, and wants the form back looking filled in. Reading a filled form is the other
    direction, and only exists to learn a template when no blank one is available.

    Values are keyed by canonical field, which is how a template names its slots, so the
    same dictionary works whether it came from a portal, a prior record, or an extraction.
    """
    try:
        template = registry.get(template_id)
    except KeyError as exc:
        raise HTTPException(404, f"Unknown template {template_id}") from exc
    try:
        pdf = regenerate(template, values)
    except FileNotFoundError as exc:
        raise HTTPException(422, str(exc)) from exc
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{template_id}-filled.pdf"'},
    )


@app.get("/api/v1/form-analyses/{analysis_id}/fhir")
def analysis_as_fhir(analysis_id: str, questionnaire: str | None = None,
                     subject: str | None = None):
    """The analysis as a FHIR QuestionnaireResponse, the shape other systems can read."""
    return to_questionnaire_response(
        get_form_analysis(analysis_id), questionnaire=questionnaire, subject=subject
    )


@app.post("/api/v1/templates/{template_id}/corrections", status_code=200)
def teach_template(template_id: str, corrections: list[SlotCorrection]):
    """Teach a template where a reviewer says its answers live.

    The blank taught where the form offers space; a reviewer looking at a filled copy says
    where an answer is. Each correction names a caption, a page and a region, and becomes
    (or replaces) that caption's slot, so every later copy of the form is read there.
    """
    try:
        definition = learn_corrections(template_id, corrections)
    except KeyError as exc:
        raise HTTPException(404, f"Unknown template {template_id}") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "template_id": definition.template_id,
        "page_count": definition.page_count,
        "field_count": sum(len(page.fields) for page in definition.pages),
    }


@app.delete("/api/v1/templates/{template_id}", status_code=200)
def release_template(template_id: str):
    """Remove a template that was learned at runtime.

    A registered template is matched against every later upload, so one learned to look at
    -- or one learned badly -- keeps changing what the extractor reads until it is removed.
    Only the derived range can be deleted; the corpus masters are evaluation ground truth.
    """
    from app.services.templates import release

    if not release(template_id):
        raise HTTPException(404, f"No derived template {template_id} to remove")
    return {"template_id": template_id, "released": True,
            "templates": [t.template_id for t in registry.all()]}


@app.post("/api/v1/form-analyses/{analysis_id}/derive-template", status_code=201)
def derive_template_from_analysis(
    analysis_id: str, template_name: str | None = None, blank: bool = False
):
    """Learn a reusable blank template from a form no registered template matched.

    ``blank=true`` says the upload is a blank form, kept exactly as uploaded for its master;
    otherwise the answers read on it are lifted off the page to make one.
    """
    folder = run_dir(analysis_id)
    analysis = get_form_analysis(analysis_id)
    page_image = folder / "normalized_color.png"
    if not page_image.exists():
        raise HTTPException(422, "No normalized page image for this analysis")
    try:
        definition = derive_template(analysis, page_image, template_name, blank=blank)
    except ValueError as exc:
        raise HTTPException(
            422, detail={"code": "TEMPLATE_NOT_DERIVABLE", "message": str(exc)}
        ) from exc

    analysis.matched_known_template = definition.template_id
    (folder / "analysis.json").write_text(analysis.model_dump_json(indent=2))
    return {
        "template_id": definition.template_id,
        "template_name": definition.template_name,
        "field_count": sum(len(page.fields) for page in definition.pages),
        "fields": [f.field_id for page in definition.pages for f in page.fields],
    }


def _derive_for(analysis_id: str, analysis) -> str:
    """Learn this form's own blank master, persist it, and return its template id."""
    folder = run_dir(analysis_id)
    page_image = folder / "normalized_color.png"
    if not page_image.exists():
        raise HTTPException(
            422,
            detail={
                "code": "NO_TEMPLATE_FOR_REGENERATION",
                "message": (
                    "No known template matched this upload and no page image remains to "
                    "learn one from. Pass ?template_id= to choose a template explicitly."
                ),
            },
        )
    try:
        definition = derive_template(analysis, page_image)
    except ValueError as exc:
        raise HTTPException(
            422, detail={"code": "TEMPLATE_NOT_DERIVABLE", "message": str(exc)}
        ) from exc
    analysis.matched_known_template = definition.template_id
    (folder / "analysis.json").write_text(analysis.model_dump_json(indent=2))
    return definition.template_id


@app.get("/api/v1/form-analyses/{analysis_id}/regenerate")
def regenerate_form(analysis_id: str, template_id: str | None = None):
    """Fill this analysis's stored values onto its blank template, as a PDF."""
    analysis = get_form_analysis(analysis_id)
    chosen = template_id or analysis.matched_known_template
    # A form nobody has seen before has no blank master, and requiring a separate call to
    # learn one meant an unseen form could not be regenerated without a person driving the
    # API. The form itself carries everything the master needs, so derive it here: the
    # values are erased out of the page and what is left is the blank.
    #
    # A form that names a template the registry no longer has takes the same road. An
    # analysis remembers the id it matched when it was read, and that template can be gone
    # -- learned into a folder that was cleared, or by a run pointed at its own registry --
    # and the copy was then refused outright, which is a dead end for a form that carries
    # its own blank. Only an id the *caller* named is an error worth reporting, because
    # nothing else can have meant it.
    if chosen and template_id is None and not registry.has(chosen):
        chosen = None
    if not chosen:
        chosen = _derive_for(analysis_id, analysis)
    try:
        template = registry.get(chosen)
    except KeyError as exc:
        raise HTTPException(404, f"Unknown template {chosen}") from exc

    resolved = values_from_analysis(analysis, template)
    # A template learned from this very upload shares its page, so a value no slot took is
    # drawn where it was read rather than left off the copy.
    indices = [page.page_index for page in template.pages]
    loose = (
        {index: loose_values(analysis, template, index) for index in indices}
        if template.derived_from == analysis.analysis_id
        else {}
    )
    ticks = {index: ticks_from_analysis(analysis, index) for index in indices}
    try:
        pdf = regenerate(template, resolved, ticks=ticks, loose=loose)
    except FileNotFoundError as exc:
        raise HTTPException(422, str(exc)) from exc

    # A template only carries the fields its form was seen to have, so a value can be read
    # correctly and still have nowhere to go -- a form captioning one region "City/State"
    # leaves a correctly read city unplaceable. Saying which values did not make it turns a
    # silently shorter form into a known one.
    drawn = {key for page in loose.values() for _, _, keys in page for key in keys}
    missing = [key for key in unplaced(template, resolved) if key not in drawn]
    return Response(
        pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{analysis_id}_{chosen}.pdf"',
            "X-Template-Id": chosen,
            # Values read from the form that this template has no slot for. Empty is the
            # normal case; anything listed here is on the original and not on the copy.
            "X-Unplaced-Fields": ",".join(missing),
        },
    )
