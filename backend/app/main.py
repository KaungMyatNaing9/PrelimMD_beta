from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app.config import settings
from app.schemas.models import ExtractionSource
from app.services.capture import new_run_dir, preprocess, save_upload
from app.services.extraction import extract_run, read_run, write_run
from app.services.form_analyzer import FormAnalyzer
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
        "templates": [t.template_id for t in registry.all()],
    }


@app.post("/api/v1/form-analyses", status_code=202)
async def create_form_analysis(file: UploadFile = File(...), template_id: str | None = Form(None)):
    analysis_id, folder = new_run_dir()
    source, kind = await save_upload(file, folder)
    if kind != "pdf":
        preprocess(source, kind, folder)
    analysis = FormAnalyzer().analyze(analysis_id, folder, template_id)
    (folder / "analysis.json").write_text(analysis.model_dump_json(indent=2))
    return analysis


@app.get("/api/v1/form-analyses/{analysis_id}")
def get_form_analysis(analysis_id: str):
    from app.schemas.form_understanding import FormAnalysis

    return FormAnalysis.model_validate_json((run_dir(analysis_id) / "analysis.json").read_text())


@app.get("/api/v1/form-analyses/{analysis_id}/pages/{page_index}/image")
def analysis_image(analysis_id: str, page_index: int):
    return FileResponse(run_dir(analysis_id) / "normalized_color.png", media_type="image/png")


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
            field.raw_value = correction.value
            field.normalized_value = correction.value
            field.is_blank = False
            field.confidence = 1
            field.needs_review = False
            field.source = "manual_correction"
            (folder / "analysis.json").write_text(analysis.model_dump_json(indent=2))
            return field
    raise HTTPException(404, "Field not found")


@app.post("/api/v1/captures", status_code=202)
@app.post("/api/v1/capture", status_code=202, include_in_schema=False)
async def create_capture(
    file: UploadFile = File(...),
    template_id: str | None = Form(None),
    sample_id: str | None = Form(None),
):
    run_id, folder = new_run_dir()
    source, kind = await save_upload(file, folder)
    result = preprocess(source, kind, folder)
    try:
        import cv2
    except ImportError as exc:
        raise HTTPException(503, "OpenCV template matching dependency is unavailable") from exc
    image = cv2.imread(str(folder / "normalized_color.png"))
    template, score = registry.match(image, template_id)
    if template is None or score < 0.12:
        raise HTTPException(
            422,
            detail={
                "code": "TEMPLATE_ALIGNMENT_FAILED",
                "message": "No approved template matched this page.",
            },
        )
    (folder / "capture.json").write_text(result.model_dump_json(indent=2))
    (folder / "template_id.txt").write_text(template.template_id)
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "status": "captured",
        "template_id": template.template_id,
        "template_match_confidence": score,
        "pages": [result.model_dump()],
    }


@app.post("/api/v1/runs/{run_id}/extract")
def extract(run_id: str):
    folder = run_dir(run_id)
    template = registry.get((folder / "template_id.txt").read_text().strip())
    run = extract_run(run_id, folder, template)
    write_run(folder, run)
    return run


@app.get("/api/v1/runs/{run_id}")
def get_run(run_id: str):
    return read_run(run_dir(run_id))


@app.get("/api/v1/runs/{run_id}/pages/{page_index}/image")
def page_image(run_id: str, page_index: int):
    if page_index != 0:
        raise HTTPException(404, "Page not found")
    path = run_dir(run_id) / "aligned.png"
    if not path.exists():
        path = run_dir(run_id) / "normalized_color.png"
    return FileResponse(path, media_type="image/png")


@app.get("/api/v1/runs/{run_id}/pages/{page_index}/overlay")
def page_overlay(run_id: str, page_index: int):
    """Lightweight SVG debug artifact for API smoke tests and overlay inspection."""
    if page_index != 0:
        raise HTTPException(404, "Page not found")
    page = read_run(run_dir(run_id)).pages[0]
    rectangles = "".join(
        f'<rect x="{field.bbox.x * page.width:.2f}" y="{field.bbox.y * page.height:.2f}" '
        f'width="{field.bbox.width * page.width:.2f}" height="{field.bbox.height * page.height:.2f}" '
        f'fill="none" stroke="{"#d17200" if field.needs_review else "#1d9a61"}" stroke-width="2">'
        f"<title>{field.label}</title></rect>"
        for field in page.fields
    )
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{page.width}" height="{page.height}" viewBox="0 0 {page.width} {page.height}">{rectangles}</svg>'
    return Response(svg, media_type="image/svg+xml")


@app.patch("/api/v1/runs/{run_id}/fields/{field_id}")
def correct(run_id: str, field_id: str, correction: Correction):
    folder = run_dir(run_id)
    run = read_run(folder)
    for field in run.pages[0].fields:
        if field.field_id == field_id:
            field.alternatives.append(
                {
                    "raw_value": field.raw_value,
                    "normalized_value": field.normalized_value,
                    "source": field.source,
                }
            )
            field.raw_value = correction.value
            field.normalized_value = correction.value
            field.source = ExtractionSource.HUMAN_CORRECTED
            field.needs_review = not correction.reviewed
            field.confidence = 1.0
            field.validation_errors = []
            run.summary.review_count = sum(f.needs_review for f in run.pages[0].fields)
            run.status = "completed" if not run.summary.review_count else "completed_with_review"
            write_run(folder, run)
            return field
    raise HTTPException(404, "Field not found")
