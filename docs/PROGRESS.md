# Progress

## 2026-07-28

### Repository state

The repository started with the approved `Prelim.md` build specification only. No application, corpus, or runtime configuration existed.

### Sprint 1

- [x] Repository scaffold, versioned schemas, API skeleton, and local artifacts.
- [x] Synthetic three-template corpus generator and manifest verifier.
- [x] Upload validation, image/PDF rasterization boundary, orientation, deskew, and canonical template alignment path.
- [x] Template-aware crops, local OCR adapter boundary, capture/extraction API, and overlay UI.

### Sprint 2

- [x] Type-specific normalization, confidence/review rules, correction persistence, and evaluation CLI.
- [x] Review UI with editable fields and JSON output.
- [x] Generated and validated the synthetic 36-sample corpus; backend schema/normalization tests and frontend production build pass.

### Current work

Runtime validation completed on Python 3.12.9: health, upload, preprocessing, alignment, extraction, PNG/SVG artifacts, correction persistence, backend tests, lint/type check, and frontend production build.

### Blockers

No approved real blank forms are present; the committed workflow uses deterministic synthetic forms only. Tesseract/Poppler are absent in this environment, so PDF and printed-text OCR need those optional local tools for full quality.

### Next concrete step

Run `make smoke` before a demo; start PostgreSQL with `make db-up` only when needed.

---

## 2026-09-11

Six weeks of entries are missing from this file; this one restates where the build actually
stands rather than pretending to reconstruct them. Every number below is measured, and each
says which corpus it came from — that distinction is the whole point.

### Which number counts

| Set | What it is | Result |
|---|---|---|
| `data/corpus/` | synthetic renders, tuned against | not reported — regression check only |
| dev forms | real layouts, tuned against, measured ~8× | 34/34 = **100%** (meaningless as evidence) |
| `data/real-forms/NEW/` | real, but scored 8× and fixed against 5× | 72.5% geometry / **80.0%** with Groq |
| `data/real-forms/TEST2/` | **fresh, never tuned on** | 66.7% geometry / **72.2%** with Groq |
| full PDFs, every page | harder: pages the first-page scores skip | 33/51 = **64.7%** geometry only |
| `data/real-forms/HANDWRITTEN/` | pen on paper, phone photos | 2.0% OCR / 24.5% local / **28.6%** mixed |

**72.2% on `TEST2/` is the honest printed-form number.** Gate 2 asks for >= 95%.

### Regeneration

- Placement **16/19 within 2mm = 84.2%**, median 1.11mm, worst 34.19mm
- Fill accuracy **12/12 = 100%**
- Round-trip **19/23 values survived = 82.6%**, 3 real layouts retained at least half

Gate 3 asks for ~2mm placement and >= 3 layouts round-tripping. Both are met; the 3 misses
are one form whose template coordinates are wrong, not a systematic placement error.

### Review load

**Silent errors: 0.** No value was both wrong and unflagged across the scored set. For a
human-in-the-loop tool that matters more than the headline accuracy: everything wrong was
put in front of a reviewer.

### Handwriting, and a target that finally has a scale

Five blanks were printed, filled by hand with a different invented patient each, and
photographed. First honest handwriting measurement in the project's life: **28.6%**.

The work plan asks for >= 80% but names no reader, so it could not say whether that gap was
an engineering failure or an impossible ask. Claude Opus 5 read the same five photographs
blind and scored **45/49 = 91.8%** — two genuine character misreads, two ground-truth
notation differences. **Revised target (user): 80% of that ceiling, ~73.5%.**

What fails is *recognition*, not understanding: labels bind and values land in the right
fields, they are simply unreadable. Digits fail worst. Handwritten tick boxes read zero of
five — though a diagnostic since shows detection and the ink signal both work (26 boxes
found; empty read 0.0, marked read 0.125 and 0.262), so that failure is downstream and
cheap to fix.

### Built this period

Sign-off and attestation (`POST /sign-off`, `Attestation`, corroborated/disputed/unread
agreement), vision-provider fallback chain (Groq → local Ollama) with budget throttling,
canonical registry rebuilt 19 → 39 fields, template learning from a filled form,
`sweep_constants.py` for auditing whether a threshold sits on a plateau or a spike.

### Discipline notes worth keeping

- A vocabulary added to `reads()` dropped layout selection 33/34 → 24/34. Scoring a layout
  by *vocabulary* hits instead of *bound field count* lets a form with unusual labels lose
  its own reader. Decoupled.
- Fusion keyed values by label and wrote patient data into a blank Subscriber section.
  Fixed by binding on position. This is the failure mode to watch for: fabrication that
  looks like a correct answer.
- Ground truth was wrong twice (name order on Last/First/Middle rows) and the model was
  right. A benchmark is a thing somebody typed.

### Gates

`make test` 73 passed, `make lint` clean, both typechecks clean. Nothing committed.

### Blockers

- Gate 2's >= 95% printed accuracy is unmet at 72.2% on unseen forms.
- Groq free tier: 200k tokens/day rolling, ~75 pages. Multi-page PDFs time out under it.
- Sign-off records who *claimed* to check; there is no authentication behind it.
- PostgreSQL is defined in docker-compose and not connected.
- PyMuPDF AGPL vs. the Apache 2.0 deliverable is still undecided with LOF.

### Handwritten tick boxes — fixed, and what it was worth

Three defects, found by tracing one photographed page stage by stage:

1. **The OCR veto removed the marked boxes.** A pen X inside a real tick box is
   unreadable to Tesseract too, so it comes back as a garbage run — `Wee`, `ely` — sized
   to the box it was drawn in. The veto's own comment said it only applied to runs
   "clearly longer than a tick box"; the code never tested that. On the Sex row it deleted
   the one box the patient had ticked and kept Male and Other. Now the run must be
   `VETO_WIDTH_RATIO` times wider than the box.
2. **Two questions merged into one row.** "Marital Status: [x] Married ... Please Address
   As: [ ] Mr [ ] Mrs" — the gap between them measured 7.8 box-widths against a threshold
   of 8, so nine boxes formed one row, no caption set named all nine, and the row was
   discarded along with the tick. Lowering the threshold would be tuning to this form; a
   colon-terminated label lying *between* two boxes now marks the break, which reads the
   form's own punctuation. The label must overlap the row vertically — allowing reach
   above let `Sex:` from the row above split Marital Status in half.
3. **A wrong value shadowed the right one.** The inline reader runs before anything looks
   for tick boxes, so it read the neighbouring question's captions as an answer:
   `Marital Status -> "Mrs Miss"`, sitting in the field list ahead of the correct
   `Marital Status -> "Married"`. `_blank_option_captions` now clears a value made
   *entirely* of words that name tick boxes on its own row. Every word must be a caption,
   so a real written answer survives.

**Measured, both on freshly started servers:**

| Set | Before | After |
|---|---|---|
| dev | 34/34 = 100% | 34/34 = 100% |
| `NEW/` | 64/90 = 71.1% | 64/90 = 71.1% |
| `TEST2/` | 45/69 = 65.2% | 45/69 = 65.2% |
| handwritten, geometry only | 1/49 = 2.0% | **2/49 = 4.1%** |

Zero regression, one field gained. Three regression tests added (76 pass).

**A methodology error worth recording:** the first before/after was measured against a
uvicorn started *without* `--reload`, so both arms ran the same stale code and the
"identical" numbers proved nothing. Restart the server, or check it reloads, before
believing any before/after through the API.

Two follow-ups that did *not* pan out, and were dropped rather than pursued:

- The Sex row still fails, but not for a structural reason: OCR simply never read the word
  `Sex:` on the preprocessed page, so the row has no label. Nothing downstream can fix it.
- "Preprocessing hurts phone photos" looked likely — `ocr_gray.png` read 168 words where
  the raw JPG read 194. Across all five forms it is +58, +4, -2, -4, -26: net positive.
  The hypothesis was wrong.

### Crop-and-ask: tried, measured, rejected

The plan was to exploit the system's demonstrated strength -- geometry binds labels and
values correctly even on handwriting -- by cropping each value region at full resolution,
enlarging it, stacking the crops into one numbered montage, and reading them in one call.

**It scored 6/49 = 12.2%, against 16.3% for sending the whole page.** Worse, not better.

Looking at the montage itself said why: on `agworker` only 4 of its 14 strips actually
contained handwriting. The rest framed an empty rule or a printed caption. The premise --
"geometry already knows where every value sits" -- is true of `stclair`, which is the form
that observation was made on, and false elsewhere. Crop-and-ask inherits every region
error and has no page context left to recover from one. Two of the five forms also failed
outright, the model returning malformed JSON or HTTP 400 on a very tall image.

Recorded because the idea is attractive enough to be proposed again. It needs accurate
regions first, and region accuracy on handwriting is the thing that is not established.

### The pipeline was destroying two thirds of what the model read

Measured with the same local model and the same prompt, on the same five photographs:

| Reader | Score |
|---|---|
| Production pipeline (geometry + vision + fusion) | 8/49 = 16.3% |
| The model alone, whole page | 25/49 = 51.0% |
| The model alone, 2x2 tiled | 31/49 = 63.3% |

A 35-point loss inside our own code. The cause was one line in `fuse()`:

    if box is None:
        continue

A reading the model has and geometry does not becomes a field only if `locate_values` can
match its text back onto the OCR words. On handwriting OCR returns `Vv \ \\ ANAC VA` where
a person wrote `Villanueva`, so a *correct* model reading matches nothing on the page and
was deleted for want of coordinates. `locate_values`' own docstring said so plainly --
"values OCR never saw simply get no box" -- without anyone noticing what that cost.

**A handwritten form is only handwritten in its answers.** The captions are printed, and
OCR reads them as well as on any other form. `locate_by_label` therefore positions such a
value from its label instead: from the end of the label to the start of the next label on
that line. The box is an estimate and says so -- `source="ocr_vision_estimated"`,
confidence 0.6 -- because it cannot serve regeneration, which places text from template
coordinates anyway. It exists so a value that was read correctly reaches a human instead
of being thrown away.

| Set | Before | After |
|---|---|---|
| dev, vision on | 34/34 = 100% | 34/34 = 100% |
| `TEST2/`, vision on | 45/69 = 65.2% | 45/69 = 65.2% |
| **handwritten, end to end** | **8/49 = 16.3%** | **17/49 = 34.7%** |

A second failure was hiding behind the first. `agworker`, the densest of the five forms,
scored 0/13 on every run. It was not a bad read -- the model returned an *empty field
list*, which raises nothing, so the tiling fallback never fired. The prompt is explicit
that an empty list means the page is completely unfilled, so an empty list from a page
geometry found answers on is a failure by the prompt's own definition. Retried in pieces
it reads 8/13. Handwriting end to end:

| Stage | Score |
|---|---|
| Session start | 8/49 = 16.3% |
| + label-anchored positions | 17/49 = 34.7% |
| + tiling on failure, empty list treated as failure | **25/49 = 51.0%** |

Three times the starting figure, on the free local model, against a ~73.5% target.

Printed forms are untouched, which is the expected result and worth stating as a check
rather than a relief: there OCR reads the values, `locate_values` succeeds, and the
fallback never fires. It acts only where it is needed.

34.7% also passes the previous best of 28.6%, which had needed Groq for two of the five
forms. This was free and local.

### Tiling: a robustness fallback, not an accuracy trick

The 63.3% tiled figure above looks like an argument for always tiling. It is not. Per
form, tiling is equal or slightly *worse* -- stclair 5 vs 7, patientpop 6 vs 7 -- because
a tile has lost the context of the page around it. Its entire advantage came from one
form, `agworker`, where the whole-page call returned unparseable JSON and scored 0 of 13
while tiling read 9.

So tiling is wired in where that reasoning actually leads: `_read_in_pieces` runs **only
after a whole-page read has already failed**. A page the model can answer about is never
re-read and never costs a second call. Had the control not been run, this would have been
shipped as an always-on accuracy feature on evidence that does not support it.

### Which reader to use: a survey, and a licensing trap

Asked to find a better handwriting reader than local `qwen2.5vl:7b`. Four candidates, and
the licence decided as much as the benchmark did.

| Model | Size | Licence | Verdict |
|---|---|---|---|
| **PaddleOCR-VL 1.6** | 0.9B (1.8GB GGUF) | **Apache 2.0** | **works here, and can ship** |
| Chandra OCR 2 | 5B | Modified OpenRAIL-M | SOTA, but **cannot ship** |
| Qwen2.5-VL 32B | 32B | Apache 2.0 (7B/32B) | **will not fit** -- 16GB M1 |
| Qwen2.5-VL 7B | 7B | Apache 2.0 | current reader |

**Chandra 2 is the accuracy leader and is unusable.** 85.8% on olmOCR-bench, explicitly
built for handwriting and forms, and licensed "free for research, personal use, and
startups under $2M funding/revenue. Cannot be used competitively with our API." That is a
field-of-use restriction, incompatible with the Apache 2.0 the work plan requires as a
Gate 1 deliverable, and worse than the PyMuPDF problem because it would sit in the hot
path of the product's main claim. Recorded so nobody adopts it on the benchmark alone.

**Qwen2.5-VL 32B is Apache 2.0 but needs ~20GB quantized; this machine has 16GB.** The
local upgrade path by size is closed.

**PaddleOCR-VL is the find.** 0.9B parameters, Apache 2.0, and on the handwritten St.
Clair photograph it reads what Tesseract cannot:

    Tesseract      Full Legal Name -> "Vv \ \\ ANAC VA"
    PaddleOCR-VL   Full Legal Name: Villanueva ... Rosa M.
                   Date of Birth: 03/08/1974
                   Scranton PA 18503
                   Email Address: rosa.villanueva@example.com
                   Occupation: Dental Hygienist
                   Emergency Contact: Hector Villanueva
                   Marital Status: [x] Married

It read the date of birth correctly as `03/08/1974` -- which Claude Opus 5, reading the
same photograph, got wrong as `05/08/1974`. In 7-56s against ~74s for qwen2.5vl:7b, from
a model a seventh the size.

**Getting it running took one real diagnosis.** The obvious Ollama tag,
`MedAIBase/PaddleOCR-VL:0.9b`, returns HTTP 500 on every image at every size. The server
log says why: `image input is not supported - hint: you may need to provide the mmproj`.
That upload is the language model without its vision projector -- a packaging defect in
one community tag, not a limit of the model or of Ollama. The official
`hf.co/PaddlePaddle/PaddleOCR-VL-1.6-GGUF` ships both and works. Worth remembering: when a
vision model refuses images, check for the projector before blaming the model.

**What it is not.** PaddleOCR-VL is a document *transcriber*, not an instruction-following
extractor: it returns the page as text, without coordinates and without label/value JSON.
So it does not drop into `vision_extractor`, which asks for structured fields. Where it
belongs is the slot `CLAUDE.md` already names -- `word_source`, in place of Tesseract --
with the existing geometry doing the binding. Its transcription is close to `label: value`
per line already.

**Prompt sensitivity is real and is a risk.** `"OCR:"` transcribed faithfully. `"OCR this
document."` on the same image invented `Scranton CA` for `Scranton PA` and `Rosea` for
`Rosa`, while getting the date of birth right. A reader that changes its answers with the
wording of the request needs its prompt pinned and regression-tested like any constant.

### PaddleOCR-VL measured: it reads 77.6%, and structure is what is missing

Run over the five handwritten photographs and parsed with the same colon rule the inline
reader uses, scored on the same 49 fields:

| | |
|---|---|
| Parsed into the right field | 6/49 = 12.2% |
| **Present in the transcription, not recovered by the parser** | **+32** |
| **Values the model actually read** | **38/49 = 77.6%** |

The headline 12.2% is a parser result, not a reader result, and quoting it would be the
wrong conclusion drawn from the right experiment. What the measurement actually says is
that PaddleOCR-VL **recognises 77.6% of these handwritten values** where `qwen2.5vl:7b`
reads 51% -- from a model a seventh its size, Apache 2.0, in ~50s a page.

**Why the parser fails is not a parser bug.** PaddleOCR-VL returns a transcription, and a
transcription flattens a form's columns:

    Full Legal Name: Villanueva Last
    Rosa M.
    First Middle

    Scranton PA 18503 CITY STATE ZIP CODE

The values are all there and in order; what is gone is which caption each belongs to. On
paper the captions sit *under* their values, and the transcription preserves that order --
so a value row followed by a caption row can be aligned positionally. That is a property
of how forms are printed, not of any particular form, which is what makes it a legitimate
thing to build rather than a rule fitted to St. Clair.

**Recommended next step, with the reasoning.** Use PaddleOCR-VL for recognition and keep
structure where it already works. Two routes, and the second is preferred:

1. Align a caption row to the value row above it, inside a transcription parser. Cheap,
   free, no extra dependency; brittle wherever a form does not use that convention.
2. Give PaddleOCR-VL the `word_source` slot `CLAUDE.md` already names for it, and let the
   existing geometry do the binding -- the half of the problem that already survives
   handwriting. The obstacle is that it returns no coordinates, so its text has to be
   matched back onto Tesseract's word boxes. `locate_by_label` built this session is
   exactly that machinery, applied to values rather than labels.

Neither was started: the reading above is the evidence for doing it, not the doing of it.

### History, and an audit trail that outlives its cache

Asked for a history view: a patient's forms over time and the changes made to them. Field
level already existed -- `FieldRevision` files what a correction replaced, append-only --
so what was missing was shape, not data.

- `GET /api/v1/form-analyses/{id}/history` -- one form as a timeline
- `GET /api/v1/patients` -- who the archive can name, and how many it cannot
- `GET /api/v1/patients/{key}/history` -- their forms, with what changed between each
- A **History** tab on the review page, fetched when opened rather than on load

Three decisions worth keeping:

**A patient needs both a name and a date of birth.** Either alone would merge two people.
A form carrying neither returns `patient_key: null` and is counted as unidentified rather
than filed under a near-miss. On the current archive that is 25 patients and 224
unidentified forms -- mostly test uploads, and the honest number.

**A diff compares only fields both forms answered.** A key one form asks for and the other
does not is a difference between *questionnaires*; reporting it as a change in the patient
would be a fabrication dressed as an audit.

**The extraction is dated by the upload, not by the record.** `analysis.json` is rewritten
by every correction, so its mtime is the time of the latest change -- which put the
extraction *after* the corrections that followed it, an order that cannot happen. The
uploaded source is written once, so it dates the reading. Caught by looking at the output.

`audit_store.py` then makes the trail durable. `docker-compose.yml` has defined PostgreSQL
since the start and nothing has ever connected to it; `plan.md` calls an audit trail on
disposable files a weak audit trail, and it is right. The store is append-only by
construction -- no update, no delete, no column that could rewrite the past -- and a write
that fails can never fail a reading, so the failure is reported through `/health` instead
of swallowed. Verified end to end: an extraction, a correction preserving the value it
replaced, and a sign-off, all filed outside the run folder.

**Why SQLite and not PostgreSQL.** Docker is not running on this machine and no local
PostgreSQL exists, so anything written against it tonight would have been unverified. The
standard library gives a durable trail today; the SQL is deliberately plain -- no JSONB,
no dialect features -- so moving it is a connection string and a payload type, not a
rewrite. PostgreSQL remains the production target and this is the minimal version the
scope allowed, not a replacement for it.

### Where this stands, for whoever picks it up

Everything below is measured, and nothing is committed.

| | |
|---|---|
| handwriting, end to end | 25/49 = **51.0%** (was 16.3%) |
| dev set, vision on | 34/34 = 100% (unchanged) |
| `TEST2/`, vision on | 47/69 = **68.1%** (was 65.2%) |
| tests / lint | 90 pass, clean |
| typecheck | tsc clean; **mypy has 9 pre-existing errors** |

A correction to an earlier entry in this file: it claimed lint and both typechecks were
clean. `make lint` was in fact already failing on two `C408`s in committed test files
(fixed), and `mypy app` reports 9 errors that predate this session -- numpy operand types,
PIL `Image` vs `ImageFile`, a few `X | None` assignments. One further error *was* introduced
tonight, a tuple default for a list parameter, and is fixed. The 9 are not touched here
because clearing them is its own change with its own regression risk, but the claim that
they did not exist should not stand.

`TEST2/` rose by two fields as well. That was not the aim -- the label-anchored fallback
was built for handwriting -- but it is consistent: a printed form can also carry a value
OCR reads too poorly to match, and those were being dropped for the same reason.

One cost of the session's own making, found and fixed before it shipped: re-reading an
empty result in pieces charged five model calls for every *blank* page, and a multi-page
packet is mostly blank pages. An empty list from a blank page and one from a page that
defeated the model are identical from inside `extract`. The caller can tell them apart --
geometry has already found values, or it has not -- so `expect_answers` carries that in.
Handwriting stayed at 51.0% with the guard, which is the check that it kept the gain.

**Next, in order:**

1. **PaddleOCR-VL into the `word_source` slot.** It reads 77.6% of these handwritten
   values against 51% for the current reader, it is Apache 2.0, and it is 1.8GB. It
   returns no coordinates, so its text has to be matched onto Tesseract's word boxes --
   `locate_by_label` is that machinery and already exists. This is the largest known win
   and it is free.
2. **The rest of the fusion gap.** The model reads more than the pipeline reports; the
   remaining loss is in which readings fusion will accept.
3. **PostgreSQL**, once Docker is available: same rows, different connection.
4. **Sign-off has no authentication.** It records who *claimed* to check.
5. **PyMuPDF AGPL vs the Apache 2.0 deliverable** is still undecided with LOF, and the
   model survey found the same trap waiting in Chandra OCR 2. Worth raising together.

### Binding PaddleOCR-VL's text by label: built, measured, rejected

The plan was to let PaddleOCR-VL supply recognition and keep binding where it already
works: geometry knows the labels -- they are printed, and Tesseract reads printed text
well even on a photographed handwritten form -- so each label could be found in the
transcription and the text after it taken as the value. No vocabulary, no form-specific
rule: the labels are whatever the form prints, and one value ends where the next label
begins, which is the geometric reader's own rule with text in place of distance.

**Discipline first.** Five forms is far too few to iterate a parser against, so the five
were split before any code was written: `stclair` and `westpark` to build on, and
`patientpop`, `uhmf` and `agworker` left unopened. The transcriptions were cached so that
re-running cost nothing and there was no incentive to iterate against the answers. The
held-out three were never scored, because the result on the development pair settled it.
(Partial leakage worth stating: per-form *scores* for all five had already been seen from
the ceiling run. Their text had not.)

**The result, on the development pair:**

| | |
|---|---|
| Pipeline alone | 8/17 = 47.1% |
| Pipeline + transcription, filling gaps and flagged fields | 7/17 = 41.2% |
| Pipeline + transcription, filling **only blanks** | 7/17 = 41.2% |
| Transcript binding alone | 6/17 = 35.3% |

**It makes the system worse, and the conservative version makes it worse by the same
amount.** That is the end of this approach, not an invitation to tune it.

**Why it fails is structural, and worth keeping.** A transcription is one-dimensional and a
form is not. The information a registration form carries in its *layout* -- captions
printed beneath a rule, columns printed beside each other -- is exactly the information
that says which caption owns which value, and flattening the page destroys it:

    Full Legal Name: Villanueva Last
    Rosa M.
    First Middle

    Scranton PA 18503 CITY STATE ZIP CODE

Every value is present and in order. Which of `Scranton`, `PA`, `18503` belongs to which
caption is recoverable only by knowing they sit above `CITY STATE ZIP CODE` -- and the
transcription no longer says that. Worse, the sub-captions are not in geometry's label
list at all (`Last`, `First`, `CITY`, `ZIP CODE` are captions, handled by a different
structure), so the binder cannot even see the boundaries it needs. Its values come back
carrying them: `Villanueva Last`, `M F [ ]`, `Hector Villanueva Name Relation`.

One collision is worth noting on its own, because the project has now hit it twice by
different routes: the label `Address` matches inside `Email Address`, takes the email's
value, and leaves the email empty. Fixed here the same way it was fixed in the template
filler -- longest match wins, and no label may match inside another.

**The finding that survives.** PaddleOCR-VL reads 77.6% of these handwritten values where
`qwen2.5vl:7b` reads 51%. That is not in doubt and is not what failed. What failed is
recovering structure from text, and the fix is not to try harder at it: **PaddleOCR-VL's
Python pipeline returns bounding boxes.** Layout detection (PP-DocLayoutV3) localises each
region, the VLM recognises it, and the JSON carries `bbox` per block. With coordinates the
binding problem disappears -- it becomes the `word_source` substitution `CLAUDE.md` has
named all along, with the existing geometry doing what it already does well.

That needs the `paddleocr` and `paddlepaddle` packages, which is a real dependency
decision and not one to take unilaterally: it is a large install, and the faculty review
asks that the dependency and licence manifest stay current. PaddleOCR is Apache 2.0, so
unlike Chandra it is compatible with what this project must ship under.

### PaddleOCR-VL with coordinates: approved, installed, measured, not adopted

The dependency was approved and installed (`paddlepaddle`, `paddleocr`, `paddlex[ocr]`,
all Apache-2.0). The pipeline does everything the documentation says. It is still not the
right reader for this product **on this hardware**, and the reason is cost, not accuracy.

**Recognition is the best seen anywhere in this project.** Where the current pipeline reads
nothing useful, PaddleOCR-VL reads:

    Full Legal Name: Villanueva Rosa M          <- exactly the ground truth
    Date of Birth: 03/08/1974                   <- correct; Claude Opus 5 misread this as 05
    Email Address: rosa.villanueva@example.com
    Occupation: Dental Hygienist
    Last Name: Rogers First Name: Morgan        <- the GGUF route said "Roger S."

It also annotates what a person *wrote*, as LaTeX underline -- `$\underline{\text{Rogers}}$`
-- which marks an answer apart from the form's own print. No other reader here does that.

**Two settings mattered, and both are worth recording.** By default the layout stage
classified St. Clair's entire demographic section as an `image` and returned it empty: the
whole patient block, silently missing. `use_ocr_for_image_block=True` recovers it in full.
And `max_pixels` reduced to speed things up made accuracy *worse* -- `Villanueva` alone
instead of `Villanueva Rosa M`, and `05/08/1974` for the correct `03/08/1974` -- while
barely helping the clock.

**The clock is the problem.** 190s a page by default, 385s with image-block OCR on, 250s
at reduced resolution. On the same machine the current reader takes ~74s and the GGUF
route ~20s. Gate 3 is a live investor demo; nothing that costs six minutes a page can be
in that path, however well it reads.

**And text alone still does not bind.** Scored on the development pair, with the same
harness as before:

| | |
|---|---|
| Pipeline alone | 8/17 = 47.1% |
| Pipeline text + label-anchored binding | 7/17 = 41.2% |
| GGUF text + label-anchored binding | 6/17 = 35.3% |

Better than the GGUF transcription, still below doing nothing. The pipeline's coordinates
are **block-level**, and St. Clair's whole demographic section is one block -- so the
per-value positions that would have let geometry do the binding are not there either. The
held-out three were never scored; the development pair settled it twice.

**What was kept.** The dependency stays as an optional extra, `pip install -e
backend[handwriting]`, and nothing in the shipped path imports it. That is worth the disk:
it is the most accurate reader available for this task, it is Apache-2.0 so it *can* ship,
and on a GPU the timing objection disappears entirely. It is the right answer for reading a
backlog offline, and the right answer for this pipeline on better hardware.

Installing it upgrades Pillow past the `<12` pin. The suite and the geometric accuracy of
all three real-form sets were re-measured on Pillow 12.3 and are unchanged (34/34, 45/69,
2/49), so the pin is widened to `<13` -- it was a guess, and now it is a measurement.
`THIRD_PARTY_LICENSES.md` carries the three new entries, as the faculty review asks.

### Printed forms: one real fix, and a benchmark that was lying

Asked to turn back to machine-printed forms, since those are the product's core claim and
68% is far from Gate 2's 95%. Every failure across both real-form sets was listed and
sorted before anything was changed: 50 failures, 36 "not read" and 14 "read wrong".

**The read-wrong cases had one dominant cause.** A value ran past its own end into the
next field:

    Race      -> 'Black/African American Preferred Language English'
    Ethnicity -> 'Non-Hispanic Hispanic Non-Hispanic or Latin'

`_value_run_end` already stops a value where the next label begins, but only if that label
is printed in capitals -- which is how the case it was written for is printed. A form that
title-cases its captions ran straight on, and the damage was double: the tail was reported
as the race *and* preferred language was left unread, two fields lost to one boundary.
Capitalised is now enough to consider; what decides it is still `_known_label_ends`, which
demands an exact vocabulary alias, so an ordinary title-case value cuts nothing.
Dev held at 34/34; `TEST2/` 65.2% -> 66.7%.

**Then the not-read cases turned out to be mostly a benchmark defect.** `Emergency Contact`
was the largest single failure class -- eight entries across both sets, never read once.
Before touching the reader, the pages were checked directly by word coordinates. The value
is on the page. It is in the wrong place:

    coastalcardio  "...emergency contact. This permission may be revoked at any time. Ngozi Okafor"
    lihc           "6. Emergency Contact Who Ngozi should Okafor we call in case of an emergency?"
    patientpop     "EMERGENCY CONTACT Ngozi Okafor RELATIONSHIP PHONE NUMBER"
    westmichigan   "Emergency Contacts Ngozi Okafor"  /  "Name______ Relationship to patient______"
    dallashipknee  "Emergency Contact Ngozi Okafor"   /  "Full Name:  Relationship:"

`fill_forms.py` matches a label's words wherever they are printed, and those words also
occur in consent prose, in a numbered question, in a table's column headers, and in
section headings. So the benchmark asserted that a reader should find a patient's answer
inside a sentence.

**This was the real risk of the whole exercise.** Emergency Contact was the biggest thing
on the failure list, and making the reader score it would have meant teaching it to pull
values out of paragraphs -- a change that looks like a 9-point gain and is a defect.

Nine entries are recorded under `_placement_defects` in the two ground-truth files, with
what is wrong with each, **kept rather than deleted** so every figure ever reported against
them stays reproducible. `evaluate_real_forms.py --skip-defects` measures without them, and
the default stays the pessimistic number.

| | as measured | excluding defects |
|---|---|---|
| `TEST2/` (fresh) | 46/69 = 66.7% | **45/64 = 70.3%** |
| `NEW/` | 64/90 = 71.1% | **62/86 = 72.1%** |

Removing them *lowers* the raw count by three, because a few were scored correct by
accident: `EMERGENCY CONTACT Ngozi Okafor RELATIONSHIP` parses as an inline label/value
pair whether or not it is one. A benchmark error can flatter as easily as it can penalise.

`fill_forms.py` now refuses a caption that reads as a sentence (`_is_caption`), so new
benchmarks cannot acquire this. A share-of-the-span test was tried first and was wrong --
PDFs disagree about how much text belongs in a span, and on a form whose whole row is one
span it refused all seventeen fields. Widening `free_space` to catch the interleaved case
was also tried and reverted: every tolerance large enough pulled in the line above and
reported "no room" for most fields on most forms. Both are recorded in the file.

The existing filled PDFs were **not** regenerated. Only two forms would change, for a net
of about one field, and regenerating would break comparability with every number in this
document for no real gain. That is a decision to take deliberately, not as a side effect.

### Working the printed-form failure list

`plan.md` now carries a working log -- kept-changes, reverted-changes with the measured
reason, and what is next -- so the same idea is not tried twice across a compaction. Four
changes were kept and four reverted in this pass. Every figure is geometry only, with the
dev set as the regression guard.

**Kept.** `match()` tries a caption's *tail* when what precedes it is phrasing rather than a
qualifier, so "What is the family's preferred language?" and "Please check the box(es) that
describe your race" finally map -- both were being read correctly and discarded for want of
a canonical key, which is the worst way to lose a value. `_known_label_ends` may take a
whole line, so a line that is only a caption and its rule stops inventing an answer out of
its own tail. A leading parenthetical carrying no digits is dropped as the instruction it
is, while `(412) 555-0184` is left alone.

**And the largest single fix was in OCR.** The image path upscales a page 2x to reach about
300dpi. Across fourteen real pages that is a wash -- within five words either way -- and on
one it was a disaster: **288 confident words at native size against 209 upscaled**, and the
two it lost were the patient's first and last name, which the same page's PDF read
perfectly. Interpolation does not only add detail; it can smear a glyph into its neighbour.
The page is now read at both sizes and the better reading kept, the same way `_best_layout`
already runs both readers rather than guessing.

Taking whichever read *more* was the obvious rule and the wrong one: it optimises quantity
and cost a dev field, rendering "Daniel" as "Danie!". The upscale keeps its place unless
native reads decisively more (`NATIVE_MARGIN`), and that constant was swept -- **every
value from 1.05 to 1.30 selects the same single page**, so it sits mid-plateau rather than
on a spike.

**Reverted, and recorded so they are not retried:** scanning a line for every caption it
contains (-5 fields: without a colon nothing says where each value ends, so every extra
caption splits a row already read correctly), and the narrower version that only splits
where a value was already cut short (-1, same cause).

| Set | Session start | Now |
|---|---|---|
| dev | 34/34 = 100% | 34/34 = 100% |
| `TEST3/` (clean benchmark) | — | **49/57 = 86.0%** |
| `TEST2/` | 45/64 = 70.3% | **50/64 = 78.1%** |
| `NEW/` | 62/86 = 72.1% | 63/86 = 73.3% |

Four of the seven `TEST3/` forms now read **100%**.

**What is left is mostly not the reader.** Of the eight failures remaining on `TEST3/`,
five are values the filler wrote somewhere no patient would: a phone in the clinic's own
address footer, a state after "Zip", an employer on the "Employer Address" line, an email
inside the checklist item "HIPAA Email Consent". These were found *after* the benchmark was
frozen and are deliberately **not** added to `_placement_defects` -- the headline stays the
pessimistic number -- but they are listed in `plan.md` so nobody spends a day on them. Of
the other three, one is a Spanish caption ("Últimos 4 # de SSN") and one is its JPG twin
with a genuine OCR digit error.

### Capture conditions: three work-plan entries closed, one of them a real hole

With the printed failure list down to three genuine reader failures, the unbuilt capture
conditions were worth more than further tuning. All three were measured rather than
assumed, and one of them was badly wrong.

**HEIC.** The iPhone default, and so the likeliest thing a patient photographing a form
will send -- and it was refused outright. Now accepted whether or not the browser labels
it: an unlabelled upload is identified by its ISO base-media brand (`ftypheic`,
`ftypmif1`, and the rest), because browsers routinely send `application/octet-stream` for
one. Decoding is an optional extra; without it the upload is refused with a message naming
the fix rather than the old "corrupted image". `pillow-heif` is BSD-3-Clause and bundles
libheif (LGPL-3.0, dynamically linked) -- recorded in `THIRD_PARTY_LICENSES.md` rather
than left implicit, since the licence manifest is a faculty-review item.

**A form photographed one page at a time.** Only the first page was ever read. `files` now
takes one upload per page beside the original single `file`, and each page is preprocessed
under the naming the PDF path already established, so a photographed packet lands in the
same shape as the same packet scanned and every later reader, the page switcher and the
stored runs work unchanged. On a real two-page form: 48 fields and 18 values against 27
and 10 from page one alone.

**Deskew: the plan entry was wrong.** It said there was no deskew step; there is, and it
recovers the angle to within 0.01° (0.99° for 1°, 7.99° for 8°). OCR barely notices
rotation -- 403-410 words at every angle against 408 upright. Accuracy does fall, but to
character loss from resampling, not crookedness: the email reads
`amara.okafor@example.com` upright and `mara.okafor@example.com` at every rotation, one
leading letter gone. The test also rotates and then lets the deskew rotate back, resampling
twice where a real skewed photo resamples once, so it overstates the loss.

**Poor lighting: this one was a real hole.** Uniform dimming cost nothing. Glare cost
nothing. A soft shadow ramp across a third of the page took a real form from **7/8 to
1/8**, with 16 regions found where there had been 27. The darkness was never the problem
-- the *gradient* was, and no global threshold survives one. Dividing the page by a
heavily blurred copy of itself, which is standard flat-field correction and blind to what
the page says, restores **1/8 to 7/8**, and 2/8 to 5/8 at a brutal 65% ramp.

Swept, because a constant that works at one value is a fitted one: **every blur radius
from 0.02 to 0.25 of the page reads 396-400 words** on upright, shadowed and
heavily-shadowed copies alike, against 191 and 178 with no flattening at all. That is not a
tuned number; it is a plateau wide enough that the value hardly matters.

| Set | Before | After flattening |
|---|---|---|
| dev | 34/34 = 100% | 34/34 = 100% |
| `TEST3/` | 49/57 = 86.0% | 49/57 = 86.0% |
| `TEST2/` | 50/64 = 78.1% | 50/64 = 78.1% |
| `NEW/` | 63/86 = 73.3% | 61/86 = 70.9% |
| shadowed page | 1/8 | **7/8** |

Kept: two fields on the contaminated set against six on the commonest phone-photo defect,
with the guard set and both fresh sets untouched.

### Derived templates full of slots that could never be filled

Recorded in the plan as "one form yields eight slots all named `registration_form_2…_7`".
It is worse than that. One form yielded **ten slots and every one** was named
`please_complete_this_form_in_black_or_blue_ink_only_illegible_forms_will_be_returned` --
the form's printed instructions, which every detected region had bound to as its label --
and another six all carried its Spanish page title. A template asserting ten fields it
cannot fill is worse than one asserting none, because only the second is honest.

Two rules now apply, both about whether a slot could *ever* be refilled rather than about
what it says. A label longer than a caption is a sentence -- unless the vocabulary can name
it anyway, since a question-phrased caption is eight words long and is a real field. And a
repeat carrying no canonical key is dropped, on an argument `_slug`'s own docstring already
makes: regeneration resolves a value by `label_key`, which only ever produces the
un-suffixed form, so `x_2` and `x_3` can never be reached. They are not hard slots, they
are dead ones. A canonical key is exempt, because real forms genuinely do repeat "Name"
once per section and those resolve by key.

Unusable slots: coastalcardio **10 -> 2**, iuoptometry **6 -> 3**, dallashipknee 6 -> 5,
with placement, round-trip, fill, the dev set and `TEST3/` all unchanged.

### A correction: two regeneration figures in this file were inflated

Deriving a template *registers* it, so surveying template quality across seven forms left
**17 stray templates in `data/templates/`**, which then matched real forms and changed what
the extractor read. Every figure taken while they were present is suspect, and two in this
document were: placement was reported as **84.2%** and round-trip as **82.6%**, measured
against that polluted registry. With it cleaned the honest numbers are **78.9%** and
**73.9%**.

This nearly cost a good change. The template fix appeared to drop round-trip from 82.6% to
73.9%; reverting it and re-measuring on a clean registry gave 73.9% as well, so the fix had
in fact cost nothing and the "regression" was the measurement. A survey that writes to the
thing it is measuring is not a survey -- and the lesson generalises past templates.

| Gate 3 criterion | Reported before | Honest |
|---|---|---|
| placement within ~2mm | 84.2% | **78.9%** |
| round-trip values surviving | 82.6% | **73.9%** |
| layouts round-tripping | 3 | 3 |
| fill accuracy | 100% | 100% |

### A modular plan, and three of its items answered

`plan.md` now carries a modular work plan -- placement, round-trip, printed remainder,
handwriting, hygiene -- each item naming the measured failure it came from. Three were
worked, and two of them ended in "do not build this", which is the point of measuring
first.

**B1: what the round trip actually measures.** Rasterisation resolution is now a `--dpi`
flag, and the figure moves *non-monotonically*: **73.9% at 150 dpi, 52.2% at 300, 65.2% at
450**. At 300 the first form collapses -- city, state, zip all `None`, `Emergency Contact`
returning `'Name Relation Phone Number'`, its own caption row. The obvious conclusion, that
the reader is resolution-fragile, is wrong: the same real forms rendered at 150 and 300 dpi
score **identically, 93.8% both**. What is fragile is the regenerated page, which carries
the blank form as a raster captured at 150 dpi and the values as crisp vector text -- so
reading it finer blurs the form's furniture around sharp values. **150 dpi is the
meaningful figure**; the flag stays for diagnosis.

**A2: no systematic bias explains the placement misses.** The evaluator now reports signed
offsets. Across 19 fields the median is **dx=-0.00mm, dy=-0.67mm** -- no horizontal bias,
a small consistent upward one. Every failure is *horizontal* (dx of -2.49, -15.23, -34.17,
+2.07; dy never past 1.15mm), so the vertical bias is not the cause and zeroing it leaves
2.49mm and 2.07mm. Swept anyway: `DESCENDER_MARGIN` moves dy linearly and **`within 2mm`
stays 15/19 at every value**, with -0.48mm still there at zero margin. Changing a constant
for no measured gain is tuning, so it stays.

**A1: the obvious rule was checked and is wrong.** Splitting a multi-column row needs to
tell `Emergency Contact / Name Relation Phone Number` (three fields) from
`Address / STREET APT #` (one field, three parts). Asking whether the sub-captions name
distinct known fields gets it backwards -- both rows have exactly one exact alias. The row
that most needs splitting is indistinguishable from the row that must never be split, so a
rule built on the vocabulary would destroy an address to fix a contact. Recorded in the
plan as **do not build**.

**E1: evaluation can no longer leave templates behind.** `DELETE /api/v1/templates/{id}`
releases a derived template and `templates.release()` is the one shared implementation --
`evaluate_roundtrip` had a private copy and nothing else had any, which is exactly why a
survey could not clean up after itself and inflated two Gate 3 figures. Corpus ids are
refused. It is a product capability as well: a bad learned template can now be removed
instead of matching every later upload.

All unchanged after: dev 34/34, `TEST3/` 86.0%, `TEST2/` 78.1%, placement 78.9%,
round-trip 73.9%, fill 100%, and zero templates left registered.

### B2: two values lost in the round trip, and they are different bugs

`Last Name 'Okonkwo' -> 'ee'` and `City 'Greensboro' -> None` looked like one failure.
They are not. The regenerated PDF *contains* `Okonkwo`, at x=206 y=293 -- written
correctly and lost in the re-read, still open. `Greensboro` was never written at all.

The reader separates the city correctly (`patient.city = 'Greensboro'`). The form captions
a single region **"City/State"**, so the derived slot is named `city_state`, no canonical
value matches it, and the value is dropped. That is the same multi-column cause as A1,
which cannot be split safely -- but the **silence** was the real defect and is fixed.

`regeneration.unplaced()` names every value that was read and has nowhere to go, and the
regenerate endpoint returns them in `X-Unplaced-Fields`. On that form:
`patient.city,patient.gender,patient.marital_status`. Empty-rule placeholders like `( )`
are filtered, and each value is named once by its canonical key rather than twice, since
`values_from_analysis` files it under both a canonical and a label-derived key.

The review UI reports it as well. "Regenerate filled PDF" now fetches rather than links,
so it can read the header, and says either which values are missing from the copy or that
every one found a slot. Being handed a shorter form and told nothing was the defect; a
template that cannot carry a field is a limitation a reviewer can see and act on.

All unchanged: dev 34/34, `TEST3/` 86.0%, placement 78.9%, round-trip 73.9%, fill 100%,
101 tests, zero templates left registered.

### A third measurement was scoring itself

`Last Name 'Okonkwo' -> 'ee'` was the open half of B2. OCR reads `Okonkwo` off the
regenerated page at confidence 0.96, and re-analysing that exact file gives `Okonkwo` --
yet the round trip reported `ee`. The difference is *when the derived template is
released*. It was released **after** the re-read, so a template learned from the page being
tested was registered while that page's copy was re-read, and merged into the reading.

That is circular -- the round trip was scoring the template's influence on its own re-read
rather than the fidelity of the regeneration -- and it was making the reading *worse*:
`ee` with the template live, `Okonkwo` without. Released before the re-read now.

**Round-trip is therefore 16/23 = 69.6%, not 73.9%.** The lower number is the honest one,
and this is the third figure in this document that a measurement had inflated by writing
to the thing it was measuring. The pattern is worth naming: deriving a template registers
it, and a registered template changes every later reading.

Once honest, the losses have one dominant cause. Five values -- `City` twice, `State`,
`Zip Code`, `Emergency Contact` -- return `None` because the template has **no slot** for
them, which is the multi-column problem of A1; two are single OCR characters
(`amara.` -> `2Mara.`, `09/` -> `99/`). So round-trip is bounded by A1, whose
vocabulary-based fix measurement already ruled out, and `X-Unplaced-Fields` now names
exactly those values rather than dropping them in silence.

| Gate 3 criterion | Circular | Honest |
|---|---|---|
| round-trip values surviving | 73.9% | **69.6%** |
| layouts round-tripping | 3 | 3 |

### The modular plan, worked to the end

Eight of ten items are closed. The two left are blocked on things outside the code: D1
needs hardware or a paid API, E4 needs Docker. Of the eight, **four ended in "do not build
this"** -- which is the return on measuring before writing.

**A1, ruled out twice.** The cause is confirmed: layout is `boxed`, and the boxed reader
takes everything inside a rule that spans several columns, so `Emergency Contact` reads
`'Ngozi Okafor Sister 412-555-0199'` and lands 34mm from where it belongs. Asking whether
the sub-captions name distinct known fields was abandoned before code -- `Name 0.50 /
Relation none / Phone Number 1.00` against `STREET 1.00 / APT # none`, one exact alias
each, indistinguishable. Cutting at a column *gap* was then built and measured, with the
type size taken page-wide rather than from the few words in one box. It worked as designed:
`Address` became `'1420 Bellefield Avenue'`, its 15.26mm failure vanished, **placement rose
78.9% -> 84.2%**. It also took dev from 34/34 to 33/34, and the failure said why: dev
expects `Address = '1420 Bellefield Avenue Apt 3B'`, street *and* apartment as one value,
which is the documented decision -- `columns_beneath` states that a rule's sub-captions
describe *parts* of one field. Round-trip fell too. Reverted.

Two independent signals have now failed on the same discrimination, and that is the useful
result: **the information needed is not on the row.** `Address / STREET APT #` and
`Emergency Contact / Name Relation Phone Number` are alike by vocabulary and alike by
distance, and one must be split while the other must never be. Separating them means
modelling a rule's sub-captions as fields in their own right, which contradicts a
documented decision and is a deliberate choice, not another boundary rule.

**B3 fixed itself.** `Phone Number -> 'cTaATe 71D CANE 412-555-0184'` disappeared once the
derived template stopped being registered during the re-read. It was never a boundary
problem on the regenerated page; it was the template being merged back into the reading of
its own copy.

**E2, the disputed signal, now fires.** It read 0 disputes in 118 fields because the
corroboration test is only reached when geometry is *confident*, where the readers nearly
always agree. Every real conflict was being settled one branch earlier, in `_adopt`, where
geometry had read something, doubted it, and been overruled -- filed as `"unread"`, which
is what a field only one reader saw looks like. A replacement is now `disputed`; filling a
blank still is not, because nobody contradicted anybody. **4 disputed in 49 fields**, and
they are the right ones: `Gender -> 'OFOM'`, `Mailing Address -> 'O Same as above'`.

**E3 says what it cannot do.** Nothing authenticates a reviewer, and an attestation reading
"signed by M. Thiha" implied a check that never happened. `Attestation.identity` records
`unverified`, with `session`/`sso`/`signature` already in the type so authentication
arrives without migrating what is already signed, and the UI prints it beside the
signature. Authenticating anyone is still a decision.

**C1 and C2 were closed without building.** A Spanish function-word list for one field on
one form, and a third attempt at a colon-less multi-caption row after R8 and R9 both
over-split. Each is one field out of 57; both would have been aimed at a single example.

| | |
|---|---|
| dev | 34/34 = 100% |
| `TEST3/` | 49/57 = 86.0% |
| `TEST2/` | 50/64 = 78.1% |
| `NEW/` | 61/86 = 70.9% |
| placement | 15/19 = 78.9% |
| round-trip | 16/23 = 69.6% |
| fill | 12/12 = 100% |
| handwriting | 25/49 = 51.0% |

103 tests, lint and both typechecks clean, nothing committed.

### The last two items, unblocked and closed

**E4: PostgreSQL is connected.** `audit_store` now carries two backends and one set of
rows. With no `DATABASE_URL` it is a SQLite file beside the artifacts, needing nothing
installed; with a PostgreSQL URL the same rows go to the server `docker-compose.yml` has
defined since the start and nothing had ever connected to. The SQL was written plainly for
exactly this, so the whole difference is the placeholder style and how the primary key
counts. `/health` reports which is live -- `postgresql · localhost:5432/prelimmd` -- because
an audit trail whose location is a guess is not much of one. Verified end to end: an
extraction and a correction both landed in PostgreSQL with the replaced value preserved.

The SQLite tests now pin themselves to the file backend. Without that they quietly wrote to
whatever server happened to be running and ignored their own `tmp_path` -- a test that fails
for the right reason today and could pass for the wrong one tomorrow. The PostgreSQL test
skips where no server is up, so the suite still passes on a laptop.

**D1: Groq measured, and it does not move handwriting.** Four of five pages reached the
hosted model and it scores **25/49 = 51.0%** -- exactly the local model's total. The
distribution differs (Groq better on `patientpop`, 7/8 against 5/8; worse on `agworker` and
`stclair`), so best-of-both would give 27/49 = 55.1%: four points for twice the calls and a
daily quota, and still twenty short of the target.

**A configuration bug was hiding inside that.** `env_file` was the relative path
`"../.env"`, which resolves against the *working directory* -- so configuration loaded when
the process started from `backend/` and loaded nothing from the repository root. Silently:
`vision_api_key` came back empty and the hosted reader fell back to local with the same
message it prints when a quota is spent. Some of what was recorded earlier as "Groq's daily
allowance is spent" may have been this. Anchored to the repository now, and verified
identical from both directories. A setting that depends on where you were standing when you
ran it is worse than one that is missing.

| | |
|---|---|
| dev | 34/34 = 100% |
| `TEST3/` | 49/57 = 86.0% |
| placement · round-trip · fill | 78.9% · 69.6% · 100% |
| handwriting, Groq or local | 25/49 = 51.0% |
| audit trail | PostgreSQL, verified |

104 tests, lint and both typechecks clean. **The modular plan is complete: ten of ten.**

### The benchmark was flattering us, and now it is not

Asked for more forms, filled more thoroughly. Both, and the result is the most useful
measurement this project has taken.

**Filling more thoroughly.** Two things were throwing fields away. `find_label` refused any
label appearing more than once, on the reasoning that a form asking for "Name" in four
sections has no unambiguous answer -- measured across seven forms that refusal was the
single largest loss, **52 labels refused against 39 filled**, and the reasoning does not
hold: reading order settles it, because a registration form asks for the patient first and
the guarantor afterwards, and the extractor is scored the same way. And `free_space`
refused to write wherever a span already covered the spot, which is correct for words and
wrong for the *rule* -- many forms store it as text, `": _______________________"` in one
span across the answer space, and refusing those cost 18 of one form's 19 fields. A span
with no letter or digit in it is the space being offered, not something to protect.

`TEST3/` went from 44 filled fields to 64; the five new forms gave 36 more.

**Five new forms, and one thrown away before use.** `riccelli.pdf` from
`physicians.stclair.org` scored **0.93 Jaccard** against the St. Clair form already in the
development set -- the same layout under another practice's name. Putting a layout the
extractor has been tuned against into a set whose whole purpose is that it has not been
would have quietly destroyed the set. Deleted.

**The numbers, and they are worse than what was being reported:**

| set | fields | result |
|---|---|---|
| dev | 34 | 100% |
| `TEST3/` thin fill (57 fields) | 57 | 86.0% |
| **`TEST3/` filled thoroughly** | **90** | **73.3%** |
| **`TEST4/` — five unseen forms** | **63** | **44.4%** |

Nothing about the extractor changed between those two `TEST3/` rows. **Thirteen points were
an artefact of how little the benchmark was asking.** The thin fill had been covering the
fields that happen to be easy.

And on layouts nobody has looked at, the honest figure is **44.4%**, with a clean placement
audit behind it: 36 values on a form row, 0 in prose, 0 missing. `hoag`'s page-1 image
reads 93%, two forms read 0. On one of those the value is rendered perfectly legibly beside
`EMERGENCY CONTACT` -- a section heading rather than a field caption, the defect class
already recorded elsewhere and deliberately *not* corrected for here.

So the printed-form range is **44% on unseen layouts to 73% on layouts we have looked at**,
against Gate 2's 95%. The previously reported 86% was a thin benchmark measuring itself.
Everything else held: dev 34/34, placement 78.9%, round-trip 69.6%, 104 tests.

## Stepping back: a census, and what the field actually does

Asked to stop tweaking and look at the whole. Two things were done before touching code
again -- every printed failure was sorted into *causes* rather than fields, and the
published state of the art was checked. They agree, and they say the tweaking has been
aimed at the wrong layer.

### The census: 153 scored fields, 94 correct, 59 failures

| cause | n | share of failures |
|---|---|---|
| value read, **bound to another label** | 18 | **31%** |
| **bound but wrong value** | 14 | **24%** |
| label found, value not read | 11 | 19% |
| label not found at all | 10 | 17% |
| value read but truncated | 6 | 10% |

**Fifty-five per cent of failures are binding, not reading.** The text is on the page, OCR
usually has it, and it is attached to the wrong caption -- `State -> 'PA'` filed under
`City, State & Zip`; `City` answered with the word `State`; `Group #` answered with `Name`.
Recognition failures -- the thing a better OCR or a better model would fix -- are the
smaller half.

### What the field does about it

Form understanding splits into entity *labelling* (is this text a question or an answer)
and entity *linking* (which answer belongs to which question). Labelling is close to
solved: LayoutLMv3 BASE reaches 0.9029 F1 on FUNSD, StrucTexTv2 LARGE 0.9182. Linking is
the hard half and is exactly our 55%: KVPFormer reports **82.23% F1** on FUNSD key-value
pairing, rising to 90.86% when given gold labels, and FormNetV2 reaches 86.35% entity
extraction with a model 2.6x smaller than DocFormer.

Every one of those is a **layout-aware transformer trained on the pairing task**. None is a
geometric heuristic. This project binds a value to a label with distance rules -- which is
a reasonable thing to have built, and is why it reads 100% on the development forms it was
shaped against and 44.4% on forms nobody has looked at.

That gap has a name now. The comparison is indicative rather than exact -- FUNSD is not our
corpus and their F1 is not our exact-match -- but the direction is unambiguous: **the
binding layer is the architecture, and it is the thing that does not generalise.**

### The candidate worth evaluating

`SmolDocling-256M` (IBM Research and Hugging Face, ICCV 2025) is **256M parameters, Apache
2.0**, converts a whole page end to end, and emits *content, structure and spatial
location* together as DocTags. It reports 0.35s a page on a consumer GPU under 500MB of
VRAM, and it can OCR inside a supplied bounding region -- which is precisely the interface
this pipeline already wants, since geometry finds regions well and binds them badly.

It is a seventh the size of PaddleOCR-VL, whose accuracy was excellent here and whose
190-385s a page ruled it out. Nothing about it has been measured on our forms yet, and it
should be, on `TEST4/` last.

### What this means for the tweaking

Two independent signals -- vocabulary and distance -- have already failed to separate
`Emergency Contact / Name Relation Phone` from `Address / STREET APT #`, and both attempts
are logged. That is not two unlucky ideas; it is the shape of a heuristic binder meeting a
problem the field solves with a trained one. Further boundary rules are unlikely to pay,
and the evidence for that is now on the record rather than a hunch.

### SmolDocling evaluated: it does not solve the binding problem

The hypothesis was that a layout-aware model doing labelling and linking jointly would beat
a heuristic binder. It was tested properly and the answer is no -- for this model, on these
forms.

It runs well here: the MLX build is Apple-Silicon native, loads in a second and takes
**41s a page** on this M1, against 74s for `qwen2.5vl:7b` and 190-385s for PaddleOCR-VL.
And what it emits is the right *shape* -- DocTags carrying content and location together,
with the pairing already made:

    <text><loc_29><loc_75><loc_103><loc_82>Last Name: Okafor</text>
    <text><loc_29><loc_86><loc_124><loc_93>Date of Birth: 04/17/1986</text>

**Scored like for like on the same 26 fields: pipeline 18/26 = 69.2%, SmolDocling
13/26 = 50.0%.** It does not beat the pipeline on a single form, and ties on one.

Two corrections were needed along the way, both mine rather than the model's. The first
score was 23.1%, because a page laid out as a grid comes back as Docling's `<otsl>` table
rather than as `<text>` items -- one form had every answer in there (`<fcel>First Name:
Amara`) and scored 0 of 10 while the values sat untouched in the output. Reading tables and
captions-above-values took it to 50.0%. Quoting 23.1% would have been measuring a
thirty-line parser and calling it a model.

Where it still fails, it fails at *reading*, not binding: on one form it emitted the
captions and **not one value** -- `Amara` appears zero times in its output, `04/17/1986`
zero times.

**And the two readers fail on the same fields.** Of 26: both correct 12, pipeline only 6,
**SmolDocling only 1**, neither 7. A union would be 73.1% against the pipeline's 69.2% --
one field, for a second model and 41s a page.

| | |
|---|---|
| pipeline | 18/26 = 69.2% |
| SmolDocling-256M | 13/26 = 50.0% |
| union ceiling | 19/26 = 73.1% |

`TEST4/` was deliberately **not** spent on this. The model does not clear the pipeline on
the set it was allowed to see, so there was nothing to confirm on the set held back.

### What that leaves

The architectural hypothesis was worth testing and is now answered at this size. 256M
parameters is not enough for these forms; the published numbers that motivated it --
KVPFormer 82.23% F1 on FUNSD key-value pairing -- come from models trained on the pairing
task itself, not from a general document converter. The remaining routes are a *trained*
linker rather than a converter, or more real forms.

Given 7 of 26 fields are missed by **both** readers, and that the corpus is twelve forms,
the corpus is the better bet: a linker cannot be trained, or trusted, on this little.

### Zooming out: FUNSD was in the repository the whole time

Every architectural conclusion in this document came from twelve forms and 153 scored
fields, which is not enough to choose anything on. `data/dataset-scanned/` is **FUNSD**:
199 real scanned forms, entities labelled question/answer, and -- the part that matters --
**explicit links between them**. That is the exact thing the failure census says costs 55%
of printed failures, already annotated, on fifty times more data than we were arguing over.
149 forms in the training split, 50 in test, **812 question -> answer links** in test alone.

`scripts/evaluate_funsd.py` scores the binder against those links, and the result is
comparable to published work for the first time:

| | |
|---|---|
| **links reproduced** | **166/812 = 20.4%** |
| KVPFormer (trained on this task) | 82.23% F1 |
| KVPFormer given gold entity labels | 90.86% F1 |

**And the decomposition changes what to do about it.** Of those 812 pairs:

- the question was found as a label in **56.0%**
- the answer text reached us *at all* in **33.4%**
- both, correctly bound: **20.4%**

So the binder is not the first problem on this data. **Two thirds of the answers never
reach it** -- they are lost in detection and OCR before any binding decision is made. The
ceiling binding could possibly reach is 33.4%, and it achieves 20.4% of the 33.4% it is
offered, which is a 61% conversion rate rather than the catastrophe the headline suggests.

That contradicts the conclusion drawn from our own twelve forms, where 55% of failures were
binding. The difference is what the two corpora are: ours are clean digital renders, FUNSD
is real scans -- which this document already recorded as "Real scans: 6%" and never pursued.
**On clean forms our problem is binding; on scans it is reading.** Both numbers are true and
neither generalises to the other, which is precisely why twelve forms could not settle it.

### What this opens

1. **A real evaluation surface.** 812 links against 153 fields, on data nobody here
   constructed, with no placement defects to argue about and published numbers to stand
   beside.
2. **A training surface.** 149 annotated forms in the training split. Training or
   fine-tuning a linker was impossible on twelve forms; it is arguable on 149, and that is
   the route the field actually takes -- KVPFormer and FormNetV2 are trained on this task,
   not prompted.
3. **A sharper question.** Before touching the binder again: why do two thirds of answers
   never arrive? That is detection and OCR on scans, and it is measurable per stage.

### What the decomposition found: neither the reader nor the binder

Same 812 pairs measured at every stage (`funsd_read_ceiling.py`, `funsd_field_coverage.py`):

| | share of all pairs |
|---|---|
| answer never read by OCR | 41.4% |
| **read, but never became a field value** | **25.2%** |
| became a value, bound to the wrong question | 13.0% |
| correctly linked | 20.4% |

The middle row is our own code. Over 20 forms the builder proposes 200 fields, 150 carrying
a value, against 327 annotated pairs -- **45.9% coverage** -- and the shortfall does not
track difficulty. Both strategies top out near 6-15 fields a page whether the page holds 9
pairs or 77. On one page OCR recovered 191 of 234 words and the builder still found 7 of 41
pairs. There is no cap in the code; the ceiling is emergent.

**We express exactly two form shapes** -- `label: value` on a row, and a ruled box with a
label above or left. On the one page shaped like that assumption we score 9/9. So
`_best_layout` choosing the wrong strategy was never the problem: neither strategy can see
most of these pages. It is also why twelve clean intake forms looked like evidence -- they
are all the shape we model, and filling them more thoroughly would never have shown it.

**The caveat that decides what to do.** FUNSD is tobacco-industry memos, reports and dense
tables from RVL-CDIP. It is **not** the product's distribution; intake forms really are
mostly ruled boxes and label-value rows, and 20.4% there is not the intake pipeline's
accuracy. FUNSD is a shape-diversity stress test and a comparable yardstick, not a target
to climb.

What it does prove is a generalisation limit with a commercial edge: every new form shape
currently needs a new rule, which is what the commercialization constraint forbids.

### Then the same measure, on our own distribution -- and it inverted

| | TEST2 | TEST4 | both |
|---|---|---|---|
| real answers on the page | 69 | 63 | 132 |
| values the builder proposed | 193 | 186 | **379** |
| of those, matching a real answer | 73 | 55 | **128** |
| **precision** | 37.8% | 29.6% | **33.8%** |

**On intake forms the builder is not starved, it is noisy.** It proposes 2.9x more values
than the page holds, and two thirds are the form's own printed text: `Name (Last,` =
`'First, Ml)'`, `Sex` = `'M F'`, `PATIENT SIGNATURE` = `'(if minor, parent or guardian)'`.

**No number in this document measured that.** `evaluate_real_forms.py` scores only labels
the ground truth names, so an invented field cannot lower any figure we report -- every
accuracy number here is recall. It also qualifies **"Silent errors: 0"**: no *annotated*
field was wrong and unflagged, but we emit ~250 confident non-answers across 20 forms, each
landing in a reviewer's queue.

So the architecture decision is deferred. It was the wrong question: FUNSD says the builder
is blind on shapes we do not serve, our own forms say it is indiscriminate on shapes we do.

### Precision work: 33.8% -> 43.1%, recall held

| | before | after |
|---|---|---|
| values proposed for 132 real answers | 379 | **253** |
| **precision** | 33.8% | **43.1%** |
| TEST2 recall | 73.9% | 72.5% |
| TEST4 recall | 44.4% | **44.4%** |
| dev forms | 100% | 100% |

126 false values removed for one real field, via three general rules: a colonless line needs
**answer space** before its tail is a value; **blank is blank** whenever nothing
alphanumeric survives (a lone `/`, `_ _ / _ _`, a symbol-font tick box); and a value whose
**function-word share** reaches 30% is the form's own prose -- across 256 real answers that
share is 0.00 without a single exception.

One instructive failure, R12: truncating a value at an unfilled rule removes 23 false
values and looks free, but on a filled form the answer is written *on* the rule, so OCR
returns both interleaved. It cut `'1420 Bellefield Avenue'` to `'1420'` and dropped TEST4
to 33.3%. Reverted; do not retry.

### Next concrete step

The 144 remaining false values need page geometry, not text tests: letterhead read as
fields (`P` = the clinic's phone), tick-box option lists returned whole, checkbox-grid
column headers, and the caption-splitting only partly closed. Licenses verified meanwhile:
PaddleOCR-VL Apache 2.0, LiLT MIT, LayoutLMv3 out (CC BY-NC-SA).

### Deferred: keep extending rules, or learn the field builder Two
license-clean candidates, both to be verified first -- **PaddleOCR-VL** (Apache 2.0,
already the identified next lever for handwriting, so one bet serves both) in the *builder*
slot rather than the value slot, or **LiLT** trained on the 149-form split. LayoutLMv3 is
out regardless: CC BY-NC-SA, incompatible with shipping Apache 2.0. Do **not** tune against
FUNSD; it is the yardstick, and `TEST4/` remains untouched. Handwriting needs
a materially better reader -- PaddleOCR-VL reads 77.6% of these values, is installed and
Apache-2.0, and wants a GPU, being 190-385s a page on this CPU. Both Gate 3 figures are
bounded by one structural question: whether a rule's sub-captions should become fields in
their own right. Two independent signals have failed to separate those rows, so that is a
design decision rather than another boundary rule. And the printed benchmark is now the
limiting instrument at 57 fields across 7 forms, which argues for more real forms before
more tuning.
Tesseract will never read handwriting, so the model has to — and the whole page is
currently downsampled to `MAX_EDGE_PIXELS = 1600` before it is sent, which turns a 50px
handwritten value into a 28px one. Tiling the page into 2x2 pieces that each already fit
that budget sends every value at native size — 1.8x more pixels per value — and, unlike
crop-and-ask, cannot inherit a wrong region because it uses no regions at all.

Beyond that the honest read is that **the reader, not the framing, is the limit.** Local
`qwen2.5vl:7b` scores 16.3% whole-page; Claude Opus 5 scores 91.8% on the same five
photographs. No cropping scheme closes a gap that size. Reaching the ~73.5% target is
likely to require a frontier-class vision model, which is a cost and data-handling
decision for the team, not an engineering one.


## We have been benchmarking the rare case (2026-09-11)

Every accuracy number above is a *zero-shot* number: only three templates exist, so TEST2
and TEST4 are read as forms never seen before. In deployment that is the rare case -- a
clinic has a handful of intake forms and uses each one thousands of times.

The benchmark data already held the deployment situation. Every form exists as a blank PDF
*and* a filled copy, which is exactly what enrolment looks like.
`scripts/evaluate_enrolled.py` learns each blank, re-scores the filled copies, and releases
every template it created.

| | zero-shot | enrolled |
|---|---|---|
| **TEST4** (freshest set) | 44.4% | **57.1%** |
| TEST2 | 72.5% | 69.6% |

**+12.7 points on the freshest set with no model and no tuning**, and a loss on TEST2, so
this is a lead rather than a win. Template matching is not the cause -- every form matched
its own template at 0.977-1.0 confidence, and forms that could not be enrolled fell back
correctly. One real defect came out of it (K11): `_merge_template` let a template slot
outrank the page even when that slot had read *nothing*, silently discarding correct ad-hoc
values. Fixed; TEST4 enrolled 54.0% -> 57.1%. It did not move TEST2, so TEST2's loss is
still unexplained and is the next thing to chase.

Zero-shot held at TEST2 72.5% / TEST4 44.4% / dev 100%, and precision at 43.1%.


## Module N: the reader moved, the bottleneck moved with it (2026-09-11)

FUNSD said reading was the biggest bucket of loss (41.4% of answers never arrived).
`word_source.py` already dispatched on `OCR_ENGINE` and already supported **rapidocr** --
installed, unused, tesseract the default since the start.

| stage, of 812 pairs | tesseract | rapidocr |
|---|---|---|
| answers legible to OCR | 58.6% | **78.2%** |
| answers arriving as a field value | 32.3% | 38.4% |
| **correctly linked** | 19.0% | **22.0%** |

22.0% is the best FUNSD figure recorded, above the 20.4% from before the precision work.
**But 19.6 extra points of legible text yielded only 6.1 points of field values.** The field
builder throws away ~40 points of recovered text, up from 26.3. Reading was the largest
bucket; it is not any more.

**rapidocr stays opt-in.** Worse on every intake measure -- TEST2 72.5 -> 65.2, TEST4
44.4 -> 34.9, dev **100 -> 85.3**, precision 43.1 -> 42.3. It suits degraded scans, tesseract
suits clean renders. A quality-driven switch would rest on one corpus, and the handwriting
set could not arbitrate: both readers sit at the OCR floor there with vision disabled.

Baseline re-verified afterwards under tesseract: dev 100%, TEST2 72.5%, TEST4 44.4%,
precision 43.1%, registry clean.


## K15: precision 43.1% -> 45.6%, and the lesson is placement (2026-09-11)

Auditing `_looks_answered` against 144 known-false values showed it vouching for 60 of them
(41.7%) -- including `"How much alcohol do"` answered `"you drink?"`. Two general tests
followed: a value that is the question's own tail, and a row of symbol-font tick boxes read
as letters. Where they were applied mattered far more than what they tested.

| placement | precision | TEST2 | layout flips |
|---|---|---|---|
| baseline | 43.1% | **72.5%** | -- |
| inside `_looks_answered` | 43.1% | 68.1% | 1 |
| during the read | 45.5% | 68.1% | 1 |
| **after the competition (kept)** | **45.6%** | **72.5%** | **0** |

`_looks_answered` scores `_best_layout`, so it cannot remove a value -- only change which
reader wins a page. And blanking during the read lowers the answered-field count that the
same competition is scored on. Only suppression *after* the winner is chosen is free.

Final: precision **45.6%**, TEST2 72.5%, TEST4 44.4%, dev 100%, FUNSD 19.0%, zero layout
flips across 26 pages, zero of 166 real answers rejected.


## The architecture was switched off for every measurement (2026-09-11)

Six experiments today tried to break one ceiling: label detection is lexical, so a caption
the vocabulary cannot name is skipped, and 93.3% of legible answers are lost with it. Gap
labels, gated gap labels, column alignment, text height, and two reading experiments -- all
measured, all reverted or rejected.

`vision_extractor.py` has said why in its own docstring since it was written: "geometric
detection binds a value to a label by *position*, which fails on real scans... A model that
sees the page reads the association directly." **That measurement had never been taken on
FUNSD**, the corpus that demonstrates the problem. Every figure in this document, including
today's, is `VISION_PROVIDER=disabled`.

| stage, of 812 annotated pairs | geometry only | hybrid, vision on |
|---|---|---|
| answers reaching the pipeline | 32.5% | **60.8%** |
| questions found as labels | 55.0% | **72.0%** |
| **links reproduced** | **19.0%** | **47.5%** |

On a matched ten-form sample the gap is wider still: 20.0% against 61.0%.

**36 of 48 pages ran on the local `qwen2.5vl:7b`**, not Groq, because the free tier drained
partway through -- so most of this gain requires no hosted service and no page leaving the
machine. That is the better outcome for patient data, and it is also why the ten-form
(mostly Groq, 61.0%) and fifty-form (mostly local, 47.5%) numbers differ: harder forms or a
weaker model, and this run cannot separate the two.

**The design stays hybrid.** Vision supplies comprehension, OCR supplies geometry, `fusion`
merges them; regeneration needs exact boxes and vision models are unreliable at pixel
coordinates. The finding is not "replace the geometric path" -- it is that the geometric
path has been benchmarked alone and reported as if it were the product.

### Measurement hygiene this exposed

- Groq rate-limits and the pipeline **silently** falls back to the local model. Count it:
  `grep -c 'falling back to the local model'` in the server log, and report it beside any
  vision figure.
- Never run two benchmarks against a vision-enabled server at once -- concurrency forces the
  rate limit, so both runs mix readers and neither measures what it claims.


## The handwriting benchmark has a 10-point spread (2026-09-11)

Found by accident. A prompting change scored 42.9% before and 42.9% after -- a clean null
result -- until the *reverted* code, identical to the baseline, scored 53.1%.

Five runs, same code, same five photographs, same local model:
**42.9%, 42.9%, 53.1%, 53.1%, 53.1%** (21 vs 26 of 49 fields).

- The **51.0%** this document has been quoting is one sample from a noisy distribution.
- No experiment on 49 fields can resolve an effect smaller than the spread.
- **Gate 3's >= 80% cannot be demonstrated on this set**, even by a run that reaches it.

The model is non-deterministic at `temperature: 0`, and the census is consistent with that:
11 of 23 failures are fields never returned at all, which is what a tiling or retry path
branching differently between runs looks like.

Also closed: structured-output enforcement was already implemented -- every provider
constrains decoding to the JSON schema -- which is why moving the prompt to a system role
could not have helped for the reason it was tried.


## The product is twenty points better than the docs said (2026-09-12)

Every accuracy figure this project published measured the geometry-only fallback. With the
hybrid switched on, same scorer, same ground truth, same forms:

| set | geometry only | hybrid |
|---|---|---|
| **TEST4** (freshest, never tuned against) | 44.4% | **65.1%** |
| TEST2 | 72.5% | **76.8%** |

## Self-templating: learn the unseen form, then read it

The system analyses a page, derives a template from that page's *printed geometry* -- rules,
boxes and captions, never the values the first pass read -- and reads the page again through
it. No human enrols anything.

| | TEST4 |
|---|---|
| geometry, cold | 60.3% |
| geometry + self-templating | 66.7% |
| hybrid, cold | 81.0% |
| **hybrid + self-templating** | **84.1%** |

+6.4 on the geometric path, +3.1 on top of vision, no form regressed in twenty
measurements -- **reproduced, and smaller than that.**

TEST2, an independent 69-field set sharing no forms with TEST4: 79.7% -> 81.2%, one field.
Across both sets, **5 fields gained of 132, none lost across twenty forms**. Consistent
direction, modest size.

On FUNSD it is *negative*: +3.0 on ten forms but **-1.0 on all fifty** (41.5% -> 40.5%), with
answer arrival falling from ~60% to 55.3%. Ten forms manufactured a false positive of the
same size and sign as the intake result, which is why the intake number needed a second set
before it could be believed.

Under self-templating FUNSD's answer-arrival rate *fell* (~60% -> 55.3%), so on noisy
scans the derived template is actively worse than no template. Intake forms are clean
digital renders whose printed rules survive into a usable template, which would explain a
genuine split -- but that is a hypothesis, not a finding. Two forms carry most of it (`hoag` 7/16 -> 10/16 cold, 15/16 -> 16/16 with
vision), so the effect size wants more forms before it is trusted to the point -- but the
direction is clean and the mechanism is sound: structure and content are independent, so
learning where the answer regions are does not presuppose what is written in them.

Scores above come from `evaluate_self_template.py`'s own scorer, which matches values
without requiring a canonical key. Compare them with each other, not with the table above.

## Three of my own ideas were worth nothing

Measured against a FUNSD baseline run twice (45.3%, 45.3% -- identical, so 812 pairs is a
stable sample unlike the 49-field handwriting set):

| | linking | vs baseline |
|---|---|---|
| fusion gate relaxed | 45.8% | +0.5 |
| model label adopted with its value | 45.7% | +0.4 |
| rapidocr reader | 44.8% | -0.5 |

All neutral. Yesterday's 47.5% was not a lucky draw either -- it ran with a different
Groq/local fallback mix (75% local against ~90% today), which is a hidden variable that now
gets recorded with every vision figure.


## FUNSD's headline was measuring a feature we never built (2026-09-12)

Eight changes were measured against FUNSD and every one came back neutral: fusion gate
(+0.5), the model's label adopted with its value (+0.4), rapidocr (-0.5), self-templating
(-1.0 on fifty forms), enlarged 2x2 tiling (-1.0), and a local 7B model against a hosted 27B
(+0.5 -- four times the parameters buying half a point). Only switching vision on ever moved
it, 19.0% -> 41.5-45.3%.

Inspecting the pairs that failed showed why. **38.5% of this split's links hang off a
question that owns many answers** -- a table column header such as `VOLUME` linked to six
cells -- and this pipeline binds one caption to one answer. It cannot represent that shape
at all. Scored separately:

| link type | share | score |
|---|---|---|
| **one caption, one answer** | 45% | **70.7%** |
| table cell under a column header | 55% | **20.4%** |
| combined | -- | 42.9% |

So the product reads the shape it serves at about **71%**, and the 42.9% headline is a
blend of that with a feature nobody has built. It corroborates the intake numbers taken by
a different route on different data -- TEST2 76.8%, TEST4 65.1% -- which is the first time
two independent corpora have agreed about this pipeline.

**What this changes.** Eight neutral results were not eight bad ideas; none of them touched
table structure, which is where the remaining FUNSD loss lives. Raising the headline means
building table understanding -- a column header owning a row of cells -- and that is a
feature decision, not tuning. Intake forms do contain some (medication lists, family-history
grids), so it is not purely academic.

`evaluate_funsd.py` now always prints the split, so the two never get conflated again.


## Where the table gap actually is (2026-09-12)

Nine things measured against FUNSD in one session; one real finding, and the rest are
recorded so nobody repeats them.

**Ruled out.** Values discarded for want of a position: **zero** -- `locate_by_label` catches
them all, so the "correct readings thrown away" theory is dead. Enlarged 2x2 tiling: -1.0,
even though FUNSD pages are 1000px with up to 77 pairs and the size-based tiling returns a
single piece for them. A local 7B model against a hosted 27B: **+0.5** -- four times the
parameters for half a point, so raw model capacity is not the lever either.

**A latent bug, fixed, that moved nothing.** `locate_values` and `locate_by_label` both
returned dictionaries keyed by the caption. A table column repeats one caption down a row of
cells, so six readings of `VOLUME` collapsed into one entry and every cell after the first
found the same box, hit the same geometric field and was discarded as a disagreement. Both
are now keyed by the reading's position. FUNSD came back **identical to the digit** (42.9%,
70.7% non-tabular, 20.4% tabular) and intake is unchanged, so this removes a real collision
class without touching today's constraint. Kept because it is a correctness fix, not a
heuristic.

**Where the gap is.** The diagnostic that matters: gold question `VOLUME`, ours `# REPS`,
value read correctly as a cell of that row. The field exists, the model reads the value, and
the *label* comes from geometry -- which picks the wrong column header. Nothing in this
pipeline knows that a column header owns a whole column, so 55% of FUNSD's links are a shape
it cannot express. That is a feature with a clear shape and it is the first FUNSD work that
should be expected to move the number.

Research note: since 2023 the field has moved to emitting tables as HTML or Markdown
sequences from a single vision pass, rather than recovering cells and then pairing them
(Docling, UniTabNet, InstructTable). Our prompt asks for flat label/value pairs, which
cannot express a column header owning six cells however well the model reads them.


## Table support: +10.7 on FUNSD, harmful on intake (2026-09-13)

A FUNSD page with 41 annotated pairs returned **6 fields and no table cell at all**. The
prompt asked for a flat list of label/value pairs, which cannot say that one caption heads
six cells however well the model reads them -- the field has emitted tables as structured
sequences since 2023 for exactly this reason. Asking for `tables` as a grid and flattening
each filled cell into a field under its column caption took that page from 6 fields to 26,
and FUNSD from **41.5% to 52.2%** (table cells 20.4% -> 39.8%, answers arriving 61.0% ->
72.7%). Largest pipeline gain measured on FUNSD by an order of magnitude.

It does not transfer. Matched trio, same local model, same forms:

| configuration | TEST2 | TEST4 | precision |
|---|---|---|---|
| geometry only | 72.5% | 44.4% | 45.6% |
| **vision, tables off** | **75.4%** | **54.0%** | **47.9%** |
| vision, tables on | 73.9% | 54.0% | **38.4%** |

**Precision falls 9.5 points** -- about forty more false values -- for no recall gain. On a
form with no table, asking for tables invents one. `VISION_TABLES` is off by default and
should stay off until a clinic form with real grids says otherwise; medication lists and
family-history grids are the cases that might.

**Vision improves precision as well as recall** (45.6% -> 47.9%), measured here on the local
7B model, which independently confirms the earlier Groq result on a different model. It does
not merely find more values, it invents fewer.


## Censusing the hybrid: vision reintroduces a failure geometry already solved (2026-09-13)

Every false-value census before this was geometry-only, and precision is the worst number
in the project. With vision on, 137 of 265 proposed values are not answers -- **124 of them
at confidence 0.75 or above**, so they reach a reviewer looking like real readings.

Reading them, the dominant pattern is one the geometric path already defends against:

    City ______        = 'ST'              Referred by        = 'Address'
    Race ______        = 'Ethnicity ____'  Signature ______   = 'Date'
    Date of Birth ____ = 'Age ________'    Insurance Company  = 'Phone ______'

The value is the **next caption on the row**. `_has_answer_space` stops the geometric reader
doing this; the vision path had no equivalent, because nobody had looked at what it emits.

The rule: a value that, once its fill-in rules are stripped, **is exactly a caption the
vocabulary knows** is not an answer. Exact membership, never fuzzy -- token overlap also
condemns "Bellefield Pharmacy", a real answer on five benchmark forms, and measured across
256 real answers the exact test costs none.

| | before | after |
|---|---|---|
| precision, vision on | 47.9% | **52.7%** |
| precision, geometry only | 45.6% | **49.8%** |
| TEST4, vision on | 54.0% | **60.3%** |
| TEST2 / dev | unchanged | unchanged |

**Recall rose as well as precision**, which is a real mechanism rather than luck: the scorer
keeps the first non-empty value under each label, so a furniture value was *occupying the
slot* a real answer needed. "City ______ = State" was blocking the actual state.

The thread worth pulling next: this is a geometric protection with no vision counterpart,
and there may be more of them.


## TEST5: a fifth set, and a third confirmation (2026-09-13)

Every intake claim rested on 132 fields across TEST2 and TEST4, and that thinness has misled
twice -- self-templating measured +6.4 on 63 fields, and the same idea was +3.0 on ten FUNSD
forms but -1.0 on fifty. `TEST5/` adds four real clinic forms and **67 fields**, a 50%
increase in intake evidence, downloaded from public sites and filled beside their own printed
labels. Each was checked against all 31 existing blanks by word-set Jaccard; nearest
neighbours were 0.25-0.38, well short of the 0.6 that would mean the same layout under
another practice's name.

| set | geometry | hybrid | gain |
|---|---|---|---|
| TEST2 | 72.5% | 75.4% | +2.9 |
| TEST4 | 44.4% | 60.3% | +15.9 |
| **TEST5** (new) | **50.7%** | **59.7%** | **+9.0** |

Three unseen sets now agree that vision is worth a lot on intake. They also disagree about
the absolute number by thirty points -- 44%, 51%, 73% geometry-only -- which says
**form-to-form variation dwarfs most of the changes measured all week**. That is the
strongest argument yet for quoting a range across sets rather than a single headline, and
for why single-set results have been so unreliable.

`TEST5` is in `make bench` and in the precision measure, so it cannot be skipped.


## Vision is the default now (2026-09-13)

`vision_provider` shipped as `disabled`, so every install ran the geometric fallback and
every figure this project published measured it. Three unseen sets say what that cost:

| set | geometry | hybrid |
|---|---|---|
| TEST2 | 72.5% | 75.4% |
| TEST4 | 44.4% | 60.3% |
| TEST5 | 50.7% | 59.7% |

and precision rises too, 45.6% -> 47.9%, so the hybrid does not merely find more values, it
invents fewer.

The default is **`ollama`**: local, free, no API key, no page leaving the machine -- the
right posture for patient data. A vision failure is already caught in `_analyze_image` and
the geometric reading stands with a warning, so an install without Ollama degrades to the
previous behaviour rather than breaking. The cost is about 60s a page against a few seconds
for Tesseract alone, which is why the setting stays a setting.


## A rule withdrawn for want of a measurement (2026-09-13)

A caption of a single letter is the practice's own contact block -- forms print `P:` and
`F:` above their phone and fax, and the reader binds those as captions owning the numbers,
so the clinic's switchboard is reported as a patient's answer. Offline the rule looked free:
11 of 137 false values removed, none of 49 real captions touched.

End to end it was not. In the run that added it TEST4 fell 60.3% -> 52.4% and TEST5 59.7%
-> 55.2%, and **whether that was the rule or ordinary movement could not be established**:
Groq had begun stalling on image requests, and the local model needs about 60s a page, so
the three repeats that would have settled it were unaffordable that night.

Withdrawn from the live path, kept as code and tests with the reason recorded. The lesson
is the placement of the evidence, not the idea: an offline count over a saved census is not
an end-to-end measurement, and a rule that changes what the pipeline emits has to be judged
the second way.

**Two operational notes.** Uvicorn is single-worker, so one stalled vision request hangs the
health endpoint too -- a run showing zero pages and a health check timing out is a blocked
upstream, not a broken benchmark. And the cost asymmetry now drives what is measurable:
geometry-only runs are seconds and deterministic, vision runs are ~60s a page locally, so
anything needing repeats needs a working hosted provider.


## A cache for model readings, and a log line that never printed (2026-09-13)

Every pipeline experiment this week was judged through a non-deterministic model, so a
five-point pipeline change and eight points of model movement were indistinguishable. That
is what left the letterhead rule unresolved, and it is why several results this week were
inconclusive rather than wrong.

`VISION_CACHE=1` stores each reading under a key made from the image's bytes, the provider,
the model, the prompt and the tile/table settings. A baseline and a variant then replay
byte-identical model output, so the difference between them is the pipeline change and
nothing else.

| TEST5, one measurement | |
|---|---|
| before the cache | **30 min** |
| successes cached | 6 min 20s |
| **failures cached too** | **18 seconds** |

The last step matters more than it sounds. A page that defeats the model raised before the
write, so every run re-paid for it -- and each failure also buys a tiling retry, four more
calls. Three such pages were the entire remaining cost of a "warm" run. Recording the
failure as an outcome, and replaying it as one, took the run from six minutes to eighteen
seconds with the score unchanged at 61.2% and seventeen straight cache hits.

It is off by default: a cached reading is a stale reading and production must always ask the
model. It also cannot answer questions *about* the model -- for variance or quality, run it
cold.

**First use settled the open question.** The letterhead rule had looked harmful (TEST4
60.3% -> 52.4%, TEST5 59.7% -> 55.2%) but against cached readings it is exactly neutral:
TEST5 61.2% with and without. The drop was the model moving between runs.

### A correction: a conclusion drawn from silence

The overnight block reported "values dropped for want of a position: **zero** -- the theory
is dead". That came from `grep -c` over a log line written with `_LOG.info`, and **nothing
in this application configures logging**, so the root logger sits at WARNING and the line
could never have been emitted. The grep was counting silence.

Re-measured with the diagnostic at `warning`, the count really is zero, so the conclusion
survives -- but it had no evidence behind it when it was made, and it sent the search
elsewhere for the rest of the night. Both diagnostics now log at the lowest level that
reaches a default handler, with the reason recorded beside them.


## Auditing the cache, because a hundredfold is suspicious (2026-09-13)

Checked rather than trusted.

**Faithfulness.** The same form read through the cache and read fresh with the cache off
returns the *same 272 lines* of label, value and source -- identical field for field, not
merely a matching total.

**Keys.** Ten entries, ten distinct keys, no collisions. Seven successes and three failure
sentinels, and the only identical payloads are those three sentinels.

**Arithmetic.** The three failures are 120-second timeouts -- "the local model did not
answer within 120s" -- and each one also buys a tiling retry of four more calls. Three
timeouts, their retries, and seven pages at 60-120s apiece is the thirty minutes. Remove
every model call and what remains is OCR and geometry at well under a second a page, which
is the eighteen. Nothing is skipped that was not already being paid for.

### What it cannot do

The cache makes a measurement **repeatable**, never more accurate. It replays one particular
draw, so if that draw was lucky every later A/B is anchored to the luck. Use it for "did my
pipeline change help"; take headline accuracy from cold runs.

### A separate finding: three TEST5 pages are never read

Those timeouts are not a cache artefact. Three pages of TEST5 defeat the local model on
every run, cached or not, and the reported hybrid figure stands with them blank. A faster
local model, or a longer timeout, would be measuring something different from what is
reported today.

---

## 2026-09-13 — The benchmark corpus was passable without reading

### What was wrong

Every filled form in `data/real-forms/` carried **the same invented patient**. One name, one
address, one phone number, one insurer, repeated across 150 artefacts. That is a benchmark
a reader could score on by memorising a string, and any threshold that happened to suit one
name's length would have read as accuracy rather than as a coincidence. Nothing in the
pipeline trains, so nothing had literally memorised it — but the measurement could not have
told us either way, which is the same problem.

Raised by the user directly: *"wait I said different for each. why only amara c okafor.
wouldnt that cause problems?"*

### What changed

`scripts/fill_forms.py` now draws **a different patient per form** — name, middle name,
date of birth, SSN, street, city, state, ZIP, three phone numbers, email, employer,
occupation, emergency contact and relation, insurer, member and group id, pharmacy,
physician — from pools, seeded from the form's own filename with SHA-256 so the draw is
**reproducible**: re-running the filler rebuilds the identical corpus and the ground truth
stays valid.

Details that matter:

* **Fiction by construction, not by hope.** Phones stay inside the 555-01xx range reserved
  for fiction, SSNs use a 9xx area that has never been issued, mail goes to `example.com`.
* **Phone punctuation varies per patient** — `412-555-0184`, `(412) 555-0184`,
  `412.555.0184`. Scoring strips punctuation, so this costs the benchmark nothing and
  catches a reader keyed to one house style.
* **Dates stay numeric.** `form_analyzer.normalize` rewrites `"August 25 2026"` to an ISO
  date and leaves `"08/25/2026"` alone, so a spelled-out month would have scored the
  normaliser rather than the reader.
* **Internally consistent.** Area code matches the city. A "Brother" is not named Saoirse.
  A patient born in 2006 is a Student, not a Reference Librarian. None of this affects
  scoring; it affects whether a human can review the corpus by eye for the placement errors
  that *do* affect scoring.

`_patients` in each `ground_truth_new.json` records which patient went on which form.

### Three filler fixes found while doing it

These grew the corpus; none of them touched the extractor.

1. **Write inside the span, not after it.** Real forms put a whole row in one text span —
   `"Address ______ City ______ State"` — so writing after the *span* put the value past the
   end of the row, where there is no room and no field. One form refused twelve of twelve
   labels for this; eight forms filled nothing at all. The write point now comes from
   `page.search_for()`, which reports where a substring truly sits, with a proportional
   measurement as fallback. A first attempt measured proportionally only and put the value
   hard against the label — `"Patient Occupation/EmployerRiverside Logistics"` — which the
   eye separates by colour and OCR returns as one token.
2. **Six pages, not two.** These are packets; a welcome letter, a HIPAA notice and a
   financial policy commonly sit ahead of the registration page.
3. **Fillable PDFs.** 23 of 86 forms carry AcroForm widgets, so the answer space is a widget
   and not a printed rule — one form had 302 widgets and filled a single field. Values are
   now typed into widgets whose **name the designer wrote**, exact matches only (`"First
   name"` yes, `"demographic t 7"` no — guessing from a widget's position would be inferring
   the layout, which is the extractor's job and would make the benchmark circular). Then
   `doc.bake()` flattens them into page content, because a live widget is invisible to
   `page.get_text()` and the PDF reader would have been scored against answers it cannot see.

Also: the JPG artefact now renders **the page that carries answers**, not always page 1.
Half these packets open with a welcome letter, so the image half of the benchmark was
scoring against a picture of prose.

### Result

| | before | after |
|---|---|---|
| forms producing artefacts | 77 | **85** |
| annotated fields | 1,302 | **2,127** |
| forms with fewer than 5 fields | 28 | **7** |
| distinct patients | **1** | **85** |

Placement was verified by eye on rendered pages, not only by count.

### Consequence for every figure taken before today

**The 67.1% geometry-only figure on LARGE was measured on the single-patient corpus and is
not comparable to anything measured after it.** TEST2/TEST4/TEST5 were deliberately left on
their old fills so their published numbers stay comparable with the history; LARGE is the
benchmark from here, and it is re-baselined below.

### Plan to 90%

Written up separately in `docs/PATH_TO_90.md`, with the short version being that "90%
accuracy" names two different numbers 20–30 points apart, and only the enrolled one can get
there with free tools.

---

## 2026-09-13 (evening) — The control, the enrolment run, and what left the repo

### G1b: a repeated patient did not inflate the score — it lowered it

`data/real-forms/SAME/` (the same 86 blanks, the same filler, one patient on every form)
was scored against the same code and the same server process as `LARGE/`, on the 2,106
(document, field) pairs both sets hold:

| | fields | correct | recall |
|---|---|---|---|
| LARGE, a patient per form | 2,106 | 1,311 | **62.3%** |
| SAME, one patient throughout | 2,106 | 1,197 | **56.8%** |

Delta **−5.4** points. So the 67.1 → 62.3 drop this morning was the 825 extra fields from
harder forms, entirely; value variety cost nothing. LARGE reproduced its baseline to the
field (1,326 / 2,127), as a deterministic geometry-only arm should. SAME has been deleted,
as its README said to.

The two sets are not field-for-field twins — a longer name does not fit where a shorter
one did, so 34 fields exist only in SAME and 21 only in LARGE — which is why the
comparison is on the intersection and not on the two headlines (56.3% / 62.3%).

**The useful finding is not the delta but the variance.** 98 of 170 pages changed score
when *only the written values* changed, and two JPG pages collapsed (8/8 → 0/8 and
8/13 → 2/8). Re-analysed against a clean registry (`flip_check.py`, four saved runs):
same page, same 37 boxes detected, and the varied fill reads as `inline` with the names
right while the single-patient fill reads as `boxed` and binds printed captions as
answers (`DATE OF BIRTH = 'FIRST NAME MIDDLE'`, `EMPLOYER'S ADDRESS = 'STATE ZIP CODE'`).
`_best_layout` scores the two readers by how many fields they answered, and a reader that
mistakes captions for answers answers *more* — 21 "filled" against 11. The strategy choice
is therefore sensitive to the values written on the page, which is exactly the
overfitting the varied corpus exists to expose. It is a scoring problem, not a detection
one, and it is the next thing to fix in the reader.

### G3: enrolment is negative at scale

`scripts/evaluate_enrolled.py --set LARGE`: 85 blanks, **50 enrolled, 36 not derivable**;
all 170 artefacts re-scored with the 50 templates registered; every template released
(registry identical before and after).

| forms | artefacts | fields | zero-shot | enrolled | delta |
|---|---|---|---|---|---|
| own blank enrolled | 100 | 1,269 | 64.9% | 61.1% | **−3.9** |
| blank not derivable | 70 | 858 | 58.5% | 58.5% | **±0.0** |
| all | 170 | 2,127 | 62.3% | 60.0% | −2.3 |

Two facts in that split. Template matching never mis-fired: the 70 artefacts without a
template of their own moved by exactly zero fields, so fifty registered templates did not
capture forms that were not theirs. And where enrolment applied, it made reading *worse*:
14 → 1 on one PDF, with the losses in the plain demographics (Address 8, Email 6, Date of
Birth 6, State 6, Last Name 5). 133 of 170 artefacts unchanged, 14 improved (largest +4),
23 worsened (largest −13).

**B1's kill condition in `docs/PATH_TO_90.md` is met.** TEST4's +12.7 was the outlier and
TEST2's −2.9 was the signal. The ≥90%-enrolled track has no evidence behind it *as
implemented* — derive a template from the blank's page 0, read filled copies through it.
The concept is not disproved; this mechanism is. Before any Track B work the question is
why a matched template reads worse than no template, and
`birminghamheart_com__Patient-form_FILLED.pdf` (14 → 1) is the case to open first.

Two defects surfaced on the way, both fixed:

* `evaluate_enrolled.py` printed "0 slots" for every enrolment — it read `pages[0].fields`
  from a response that carries `field_count`. The templates had 5–36 slots each.
* `drkahlam_com__reginfo.pdf` could not be enrolled: a one-letter caption slugged to `'f'`,
  which fails `TemplateField`'s two-character id pattern and aborted the whole derivation.
  `label_key` now pads such ids (`field_f`), with tests.

Of the 36 blanks that could not be enrolled, most report "not derivable" or "no answer
regions" on page 0; several of these packets open with a letter, and `derive_template`
learns page 0 only. Multi-page enrolment is not modelled.

### The benchmark is now 112 forms

The 26 unlocked blanks outside LARGE (NEW 7, TEST2 7 — TEST3 holds the same seven —
TEST4 5, TEST5 4, the original 3) were folded in and filled with the same seeded filler.
The old 86 are untouched: all 170 existing truth entries identical, all 85 existing JPG
renders byte-identical (re-saved PDFs differ in bytes only). LOCKED stays locked.

| | before | after |
|---|---|---|
| blank forms | 86 | **112** |
| artefacts | 170 | **222** |
| annotated fields | 2,127 | **2,775** |
| distinct patients | 85 | **111** |

Zero-shot geometry on the 112 forms: **61.2% (1,699 / 2,775)**.

### What left the repo

At the user's direction ("delete all that won't mess up"): the stale `backend/artifacts`
root (8.4G, written by servers started inside `backend/`), 1,932 run folders older than a
day (2.7G), `SAME/`, the synthetic corpus with its generator, manifest checker and
`make corpus`, `evaluate_corpus.py` and `make evaluate` (they scored the dead `/captures`
pipeline), `fill_fixture.py`, `try_vision.py`, `show_progress.sh`, `docs/DEMO_SCRIPT.md`.
All of it sits in `~/.Trash/prelimmd-cleanup-2026-09-13` until the Trash is emptied, and
the tracked files are one `git checkout` away besides. `make smoke` now uploads a real form
through `/form-analyses` and starts its server from the repo root — starting it inside
`backend/` is how the 8.4G folder came to exist. The registry starts empty; the
regeneration unit tests skip when no template is on disk (7 skipped), a coverage gap to
close with a real-form-derived fixture. Kept on purpose: `NEW/` (seven unique real blanks),
TEST2–5, HANDWRITTEN, LOCKED, FUNSD, `evaluate_fill.py`, `demo_roundtrip.sh`,
`funsd_signal_probes.py`, `sweep_constants.py`, `evaluate_scans.py`.

Not done tonight, deliberately: removing the dead `/captures` pipeline (`extraction.py`,
89 lines of routes, four functions in `api.ts`, one test). Shipped code, its own commit.

### Addendum, later the same evening

At "remove anything unnecessary that you are 100% sure won't be reused": also to the Trash
went every run folder under `artifacts/` (1,295, 1.9G, all of tonight's benchmark residue),
`artifacts/evaluation/` (outputs of the deleted corpus scorer), `data/real-forms/NEW/` (its
seven blanks are in LARGE; only the contaminated single-patient fill remained),
`data/real-forms/TEST3/` (a second fill of TEST2's seven blanks, no README, unreferenced),
three unreferenced blank renders in the real-forms root, `scripts/funsd_signal_probes.py`
(a negative result plan.md already records) and `HANDOFF.md` (written on the belief that
sessions could not transfer between accounts). `fill_forms.py` now defaults to LARGE.
Kept because reuse is not ruled out: TEST2/TEST4/TEST5 (`make bench` runs them and every
historical vision number was taken on them), the original three-form set (all regeneration
numbers), `evaluate_fill.py`, `demo_roundtrip.sh`, `plan.md`, the build caches.

One consequence for LARGE's standing as an unseen set: seven of its 112 blanks are NEW's,
which were fixed against five times, and seven are TEST2's, which were used to diagnose
the PDF baseline tolerance. `data/real-forms/CONTAMINATION.md` now records that.

### Addendum: the legacy `/captures` pipeline is gone

At the user's go: `backend/app/services/extraction.py` (200 lines), the seven `/captures`
and `/runs/*` routes (101 lines of `main.py`), the four unused functions in
`frontend/src/lib/api.ts` and the `schemas.ts` only they imported, and the three tests of
the legacy normaliser. Nothing in the UI called any of it — every page runs on
`/form-analyses` — and its last user was the smoke test rewritten earlier tonight. With it
goes the two-`normalize` phone-format trap. `docs/API.md` is now generated from the live
route table. Tests 118 passed / 7 skipped, lint and typecheck clean, smoke green.
Also removed: the six schema definitions only that pipeline used (`ExtractionSource`, `ExtractedField`, `ExtractedPage`, `ExtractionSummary`, `ExtractionRun`, `EvaluationMetrics`); the one test that used them now checks the live `DetectedFormField` contract.

## 2026-09-14 — Enrolment made additive, the fifty-template cap, every page learned

The research pass (`docs/RESEARCH_TO_90.md`, eight capped agents in a separate session)
re-ranked the levers against the loss breakdown. Its reframing holds: 1,699 correct + 791
misses = 2,490 = 89.7%, so fixing *every* miss perfectly still lands under 90%, and 90% is
an enrolled-track number only. Its three L1 defects were checked by hand against the repo
before anything was written: `align_to_template` is referenced only by a docstring, the
template read was `pages[0]` hardcoded, and `_merge_template` let an *answered* slot
outrank a correct free-form value. All three true.

### L1a: the template may only add

`_merge_template` inverted. The page's own reading stands; a slot is appended only where it
brings something the page did not -- a value where the page read none, a field where the
page found none. A reading that only adds cannot score below the reading it adds to, so the
zero-shot score is a floor by construction. Measured (`evaluate_enrolled.py --set LARGE`,
50 templates -- see the cap below -- registry identical before and after):

| forms | artefacts | fields | zero-shot | enrolled | delta |
|---|---|---|---|---|---|
| own blank enrolled | 100 | 1,297 | 66.9% | **68.8%** | **+1.9** |
| no template of their own | 122 | 1,478 | 56.2% | 56.2% | ±0.0 |
| all | 222 | 2,775 | 61.2% | **62.1%** | +0.9 |

Yesterday the same mechanism was −3.9 where it applied. 16 artefacts improved (largest +5,
`hoag_FILLED.pdf` 10 → 15), 4 lost a field -- which the additive rule should make
impossible. The mechanism, from the field diffs: a slot captioned "Date of Birth" whose box
caught `STREET,`, appended beside the page's correct birth date read under "DOB"; "Zip
Code" holding `State ______ LA Zip ____` beside the page's correct zip under "Zip"; "Phone
Number" holding `INSURED NAME`. Same question, a second and worse answer, listed under the
ground truth's own wording. Two refinements landed, not yet re-measured: a slot answering a
question the page already answered -- by canonical key or by caption wording -- is dropped,
and slot values go through `_blank_furniture` like every other field (they never had).
Precision on the enrolled subset went 58.0 → 54.9% (1,572 → 1,806 proposed values), which
is the same furniture; it is the number to watch on the re-run.

### The fifty-template cap

Of 112 blanks, 50 enrolled, 15 were genuinely not derivable (11 "no answer regions", 4 "no
usable caption"), and **47 crashed with `RuntimeError: No derived template ids remain`**.
Derived ids ran `T50`–`T99`: fifty, total, for a clinic or a benchmark. Yesterday's "36 of
85 not derivable" was mostly this cap, reported as "not derivable" because a plain-text 500
is not JSON and the script printed its fallback. `template_id` now allows `T\d{2,4}`,
`DERIVED_ID_END` is 9999, and `evaluate_enrolled.py` says "server error" when the reply
is not JSON.

With fifty templates registered every analysis took ~15 s instead of ~2.5: `registry.match`
re-read and re-described every master image on every call. Masters' ORB features are now
cached per template (cleared on `load()`); a page is described once and matched fifty times
in well under a second.

### Every page is learned

`derive_template` learns every page of the packet, not page 0; `_read_with_template` reads
page *k* of a filled copy through template page *k* (packets keep their page order between
blank and fill); a page with no regions is kept as an empty page so indexes line up;
`blank_preview_p{k}.png` per page, page 0 keeping its old name so earlier templates still
resolve. `derive-template` reports counts across pages. Not yet measured.

### AcroForm widgets are answer spaces

`widget_boxes(pdf, index)` returns the text widgets' rectangles in render pixels; when a
page has them `geometric_fields` puts them first and keeps only the detected boxes and
rules that are not the same space seen twice. The designer's own field names are not used
yet (they are often `Text1`); captions still come from the page. Not yet measured.

### Precision beside recall, and LARGE in `make bench`

`evaluate_real_forms.py` prints `REAL-FORM PRECISION` from the same responses, by
`intake_field_coverage.py`'s rule (a value is vouched for when it and a written answer
contain one another). LARGE zero-shot: **61.2% recall (1,699 / 2,775), 58.1% precision
(1,857 / 3,196 proposed)**. `benchmark_all.py` now runs LARGE zero-shot every time and
LARGE enrolled outside `--quick`; the benchmark had sat outside the runner for a day.
`--dump DIR` keeps every analysis response for page-by-page comparison.

### Housekeeping

`_reads` (no callers since the merge change) removed. `"prelimmd medical center"` removed
from `HEADINGS`: a specific clinic's letterhead from the deleted synthetic corpus, inside a
rule meant to be generic. One caution recorded for L4 (PDF font cues): the benchmark's
values were typed by `fill_forms.py` in a font it chose, so caption-versus-value font
differences measured here would measure the filler, not clinics' forms. Ollama carries no
local text model; L3 would need `qwen2.5:7b-instruct` pulled first.

### L2: the two readers reconciled one caption at a time

`_best_layout` kept whichever reader answered more fields, whole page at a time, and a
reader that mistakes captions for answers answers *more* (21 "filled" to 11 on a page the
eleven were right about). Behind `LAYOUT_CHOICE=assign`, both readers' proposals are now
pooled and taken best-first under one rule -- a run of words serves once, as one caption's
value or as one caption, never both -- with hand-set weights: the value's plausibility for
its type (soft), a known caption, the reader's confidence, a bonus where both readers
propose the same value for the same caption, and a small prior for the reader that read
the page better as a whole. Furniture weighs nothing. The reader that lost the page may
contribute only captions the vocabulary knows.

Three versions, all measured on LARGE against the same build (`LAYOUT_CHOICE=best`
reproduced 61.2 / 58.1 exactly first):

| version | recall | precision | what was wrong |
|---|---|---|---|
| page-level count (today's baseline) | 61.2% | 58.1% | — |
| v1: Hungarian matching, captions and values in separate pools | 63.2% (+1.9) | 55.6% (−2.5) | a sum objective takes two mediocre edges over one good one; every junk edge that conflicts with nothing is added; "Portland" served as one candidate's caption and another's value |
| v2: greedy with run exclusivity, furniture = 0, loser needs a known caption | 54.1% (−7.2) | 66.3% | PDF text layers hand over "__________ Eguchi"; `plausible` calls the underscore furniture, and the whole PDF half fell 14 points |
| **v3: v2, value judged with its rule stripped** | **63.2% (+1.9)** | **67.8% (+9.7)** | PDF 62.8 → 65.4 / 53.7 → 65.8; JPG 59.3 → 60.4 / 64.8 → 70.6; 45 artefacts up, 11 down by at most two fields |

v3 meets the research's own kill test (beat the count on recall *and* precision on the same
set) and lands inside its predicted precision band (65–72%). Not adopted as the default
until TEST2/TEST4/TEST5 and FUNSD agree -- both arms are being run on them now. scipy was
added for v1 and removed again with it.

Two findings on the way that are worth more than the numbers. The page-level score
(`reads`) uses `plausible`, which calls a rule-carrying PDF value furniture, so it has been
under-counting the inline reader on every ruled PDF -- a bias in which reader wins those
pages that nobody had noticed. And `plausible` tested for furniture before it tested for an
e-mail, so `noor_k@example.com` scored as implausible; fixed, with a test (no benchmark
address carries an underscore, so no number moved).

### Enrolment at full scale: every blank, every page, widgets, the additive rule

`evaluate_enrolled.py --set LARGE` with the id cap gone, every page learned and widgets in
(zero-shot floor = the assignment reader's third version, 63.2 / 67.8 on the same build):
**108 of 112 blanks enrolled, 4 genuinely not derivable, no errors**; registry identical
before and after.

| forms | artefacts | fields | zero-shot | enrolled | delta |
|---|---|---|---|---|---|
| own blank enrolled | 216 | 2,727 | 63.0% | **67.4%** | **+4.4** |
| no template of their own | 6 | 48 | 72.9% | 72.9% | ±0.0 |
| all | 222 | 2,775 | 63.2% | **67.5%** | +4.3 |

61 artefacts improved (largest +6), **one lost one field**, and the six without a template
did not move. The additive rule is doing what it was built to do: yesterday's −3.9 is +4.4
on more than twice the artefacts. Precision on the enrolled subset fell 67.8 → 63.0
(2,623 → 3,151 proposed): of the 527 values slots added, **206 are answers written on the
page and 321 are the form's own printing** caught inside slot boxes -- option rows ("angry
expressing guilt suspicious"), the neighbouring caption ("apt/trailer # city ____",
"contact", "(name)"), rule glyphs. The enrolled track has an oracle for exactly that which
zero-shot never will: the blank. Each slot now stores the text the blank prints inside it
(`TemplateField.printed_text`, learned from the blank's own OCR), and a read through the
slot drops those tokens with multiplicity before anything else looks at the value.
Measured in the final `make bench` below.

### The fourth version of the reader, and why it was reverted in part

Per-label diffs of the third version showed three losses with mechanisms: `Race` rows lost
to a losing-reader *blank* candidate captioned with an option's own text that hijacked the
tick boxes; "Emergency Contact" = a real name zeroed because the caption sits in
`HEADINGS`; "#_GRP24741" zeroed because one underscore survives the rule stripping and
`plausible` calls it furniture. The fourth version made plausibility, heading and caption
length soft costs for every caption and stopped the losing reader adding blanks: recall
63.2 → 64.4 but precision 67.8 → **61.4** -- softening the tests for every caption
re-admitted the unopposed junk they exist for. The fifth keeps the hard tests, exempts only
captions the vocabulary maps from the heading and length tests, and strips a stray rule
glyph before judging shape. It also carries the date-ordering term (a birth caption prefers
the page's earliest date, a plain date caption its latest; `DATE_ORDER`), which the
research listed as L6.

### The fifth version, on every set

Same build, `LAYOUT_CHOICE=best` against the default (`assign`, fifth version):

| set | recall before → after | precision before → after |
|---|---|---|
| **LARGE (the benchmark)** | **61.2 → 64.5** | **58.1 → 67.9** |
| TEST2 | 72.5 → 72.5 | 62.1 → 71.1 |
| TEST4 | 44.4 → 52.4 | 41.7 → 53.9 |
| TEST5 | 50.7 → 55.2 | 58.5 → 58.0 |
| intake precision (TEST2/4/5 by `intake_field_coverage.py`) | — | 54.2 → 61.4 |
| FUNSD links (third version; fifth in the final table below) | 18.3 → 17.1 | — |

Against the third version, the fifth gained 36 fields on 33 artefacts and lost none. LARGE
decides (user, 2026-09-14: "the 100+ real forms ... should be the main"); the others are
run so that nothing is adopted on one set, and the two TEST2 `Race` rows the third version
had lost are back.

### A regression the full run caught: captions beneath a rule

The first full `make bench` on the assignment reader came back with placement 76.5%
(13 / 17) against the recorded 78.9% (15 / 19) and the dev forms at 27 / 34 where the
record says 34 / 34. Same four misplaced values at the same distances, so not a placement
error: two St. Clair values were no longer *read*. Under `LAYOUT_CHOICE=best` on the same
build, 15 / 19 and 34 / 34 came straight back, so it was the reader. The dumps showed the
`CITY` / `STATE` / `ZIP CODE` column fields gone from the page entirely: their captions
are printed beneath the address rule at the same height as the phone row, whose answer
region spans the page, and the rule that a run of words cannot be one proposal's caption
and another's value tested "same place" either way -- the small captions' centres lay
inside the wide phone region, and all three fields were excluded, not even kept blank.
Across kinds the test now requires the *same run* (each region holds the other's centre),
which is what the Portland case it was written for looks like. Dev forms 33 / 34 with
precision 73.9 → 82.5, placement back to 15 / 19, and LARGE +3 fields with none lost
(64.6 / 67.8). The 34th dev field was a PDF birth date typed into boxed cells, handed
over as `04 / 17 / 1986`; `plausible`'s date pattern refused the spaces and the quality
function zeroed it, where the old reader had only used that test to score the page.
`plausible` now collapses spaces around date separators (with a test).

Two lessons the day repeated three times: every hard zero copied from `_looks_answered`
into a per-field decision cost real values it had never cost as a page score, and a
"same place" test that is right for two captions or two values is wrong across kinds.
Both are recorded in the code beside the rules.

### Final `make bench`, 2026-09-14 (assignment reader, additive multi-page enrolment)

| benchmark | result | before today |
|---|---|---|
| dev forms (tuned against) | 100.0% recall, 80.5% precision | 100.0% / 73.9% |
| TEST2 zero-shot | 72.5% / 71.1% | 72.5% / 62.1% |
| TEST4 zero-shot | 52.4% / 53.9% | 44.4% / 41.7% |
| TEST5 zero-shot | 55.2% / 58.0% | 50.7% / 58.5% |
| **LARGE zero-shot** | **64.6% / 67.8%** | 61.2% / 58.1% |
| intake precision (TEST2/4/5) | 61.4% | 54.2% |
| **LARGE enrolled** | **68.3% / 67.8%** (own-template subset +3.7, others ±0, 47 up / 0 down) | 60.0% (−3.9 where applied) |
| FUNSD linking | 17.1% | 18.3% |
| placement ≤2mm | 78.9% | 78.9% |
| round-trip | **65.2%** | 69.6% |

Registry identical before and after. Two dips, both recorded as open: FUNSD −10 links of
812 (the yardstick, not the benchmark), and round-trip −1 value of 23 -- on the regenerated
St. Clair page the reader now returns "Riverside" for Employer where it returned "Riverside
Logistics"; the first analysis reads the full value, so it is the re-read of the regenerated
page that truncates, most likely an inline candidate winning the caption with a shorter run.
Not investigated today.

What did not happen today, and why: L3 (the grounded re-linker over orphan spans) needs a
local text model that is not installed (`qwen2.5:7b-instruct`, ~4.7 GB; Ollama here has
only the vision model, PaddleOCR-VL and cloud-hosted tags, which are not admissible), and it
is the one lever whose payoff the research could not predict; L4's font cues would measure
the filler's font choice on this benchmark (see above); registration (`align_to_template`)
stays unwired for want of a skewed scan to test it against. The user's UI backend on port
8000 was started before any of this and runs the old code until restarted. Nothing is
committed.

## 2026-09-14 (later) — What a reviewer's correction teaches a template

The user's reaction to the day's numbers was the right one: the research was commissioned
to find a route to 90%, and 64.6% zero-shot is not it. The research's answer was that
90% is an enrolled number reached by *corrections teaching each template* -- "the second,
third and fifth examples are free" -- and nothing had been built for that. Now there is:

- `POST /api/v1/templates/{id}/corrections` takes `{page_index, label, bbox, value?}`
  entries and makes (or replaces) that caption's slot, keyed by the vocabulary where it
  knows the caption, with the blank's printing inside the region recorded for subtraction
  (`template_builder.learn_corrections`). A review UI that lets a reviewer confirm or draw
  a region calls this; the PATCH on a field does not call it yet, because a corrected
  *value* on a field bound to the wrong place would teach the wrong place.
- A second benchmark fill, `data/real-forms/LARGE_B/` (same 112 blanks, `fill_forms.py
  --variant B`: 222 artefacts, 2,793 fields, 111 distinct patients, none shared with the
  first set). Teaching happens on the first set's fill and scoring on the second, so
  nothing memorised about one patient can count. Zero-shot on it: **64.3% recall, 69.6%
  precision** -- the first set's 64.6 / 68.9 on different values, which is the check that
  the day's reader was not fitted to the first set.
- `scripts/evaluate_corrected.py --positions {none,detected,oracle}`: the blank alone,
  what today's UI can confirm (regions of detected fields whose value the reviewer agrees
  with), and where every true value actually sits on the teaching fill (the ceiling for
  the mechanism, and the case for a draw-a-region affordance in review).

Values shown in review are clean now, too: `normalize` strips runs of rule glyphs, so a
PDF's "__________ Guerrero" is "Guerrero" (the scorer never saw the difference; a reviewer
saw it on every ruled PDF), and a reading whose raw text has words *between* rule runs
("__ Male __ Female", a tick row drawn as rules) is furniture. LARGE 64.6 / 68.9 after
both, placement and round-trip unchanged.

### One corrected fill per form, measured on a second fill

`evaluate_corrected.py --positions oracle` (teach from the first set's fill at the true
positions of its values, score the second set; 108 templates, 1,489 corrections taught,
registry identical after):

| arm, second set (2,793 fields) | recall | precision |
|---|---|---|
| zero-shot | 64.3% | 69.6% |
| blank + one corrected fill, slots additive | **74.7%** | 70.8% |

+10.6 on the 216 artefacts with their own template, ±0 on the six without, **124 artefacts
up and none down**. The research's arithmetic said 90% was an enrolled number reached by
corrections; the first corrected fill is worth ten points on a form never seen with that
patient.

Of the 708 remaining failures, 326 are misses and 363 wrong values, and **350 of the 363
wrong values are on captions the template had been taught**: the page's own misreading
("Chattanooga State", "Name_Analise", "H2A Visa" for a state) stood in front of the
reviewer's slot, because the additive rule lets the page lead. That rule was right for a
slot guessed from a blank and wrong for one a reviewer placed. Taught slots are now marked
(`TemplateField.taught`), read back with `source="reviewer"`, and displace the page's
fields at their place and on their question; inferred slots still only add. Re-measured
below.

### The reviewer's slot leads, with two guards

Unconditional override measured worse than additive (74.0 / 70.6): misses fell 326 → 208
but wrong values rose 363 → 518. Of the 224 fields it broke, 159 were the slot reading
*past* the true value into the next field ("2986 Lantern Hill Road Apt/Trailer") and 61
were the oracle placing a value's *first* occurrence on the page, which for a surname the
emergency contact shares was the contact's slot. Two guards: a taught region ends at the
next word on its line, and is placed at the occurrence nearest its caption (both in the
script, standing in for a reviewer who draws the region beside the caption); and in the
merge, when the page's reading is a strict part of the slot's, the tighter reading wins
(`_strictly_within`), otherwise the reviewer's slot wins and displaces the page's fields
at its place and on its question.

| arm, second set (2,793 fields) | recall | precision |
|---|---|---|
| zero-shot | 64.3% | 69.6% |
| blank + one corrected fill, additive | 74.7% | 70.8% |
| blank + one corrected fill, override unguarded | 74.0% | 70.6% |
| **blank + one corrected fill, override guarded** | **82.8%** | **71.2%** |

+18.8 on the 216 artefacts with their own template, ±0 on the six without, **155 artefacts
up, 3 down by one field**. Remaining: 182 misses and 299 wrong values (17.2 points). This
is the first number on the enrolled track measured the honest way -- taught on one patient,
scored on another -- and it is nine points below 90 with one correction pass per form.

### Two corrected fills, and where the rest of the loss is

A third fill set (`LARGE_C`, 2,777 fields, a third patient per form, zero-shot 64.9 /
70.6 -- the third draw to land on the same zero-shot number, so the day's reader is fitted
to none of them). Taught from the first two sets' fills, scored on the third: **83.3% /
72.4%**, against 82.8 / 71.2 from one fill. A second correction at the same positions
replaces the first slot rather than adding evidence, so it is worth half a point; the
remaining loss is not where-to-look.

After one corrected fill on the second set the PDF half reads **88.1%** and the image
half **76.3%**. What is left, by artefact kind: misses on taught captions 41 PDF / 112
image; wrong values on taught captions 112 PDF / 164 image, in three shapes -- a reading
from elsewhere on the page (51 / 77), the slot reading past the value into the next field
(32 / 51), a fragment (29 / 36). Three causes with fixes: a photograph of a later packet
page was matched against every template's *page 0* and read through page 0's slots (the
thirteen later-page images per set read at 44-72%) -- `registry.match_page` now tries every
stored master page and the read uses the matched page; a caption just past a slot's edge
was not recorded as printing and crept into the read ("Chattanooga State") -- the blank's
printing is recorded with a small margin (`PRINTED_MARGIN`); and OCR glues a caption, its
rule and the value into one token ("Name_Analise") -- the underscore is treated as the
seam and each piece judged on its own. The image half's remaining misreads ("42/26/2006",
"94/49/4950") are Tesseract at 150 dpi, which no reader rule fixes.

### What today's review screen can teach

`--positions detected`: the reviewer confirms values on fields the reader already found,
and each confirmed field's own region becomes the slot -- no drawing. 1,088 corrections
against the oracle's 1,489, and the regions are the reader's, wide or wrong where it was.
Second set: **71.8% / 70.1%** (+7.5), 98 artefacts up, 24 down. Against 82.8% with placed
regions, that is the measured worth of a draw-a-region control in review: eleven points.
Until it exists, the PATCH on a field should not teach, because a corrected *value* on a
field bound to the wrong place would teach the wrong place.

### The corrections track, final for the day

One corrected fill per form, scored on the second set, after page-aware matching, the
printed-text margin and the glued-caption seam (registry identical after):

| arm, second set (2,793 fields) | recall | precision |
|---|---|---|
| zero-shot | 64.3% | 69.6% |
| blank only (enrolled arm) | ~68 | ~68 |
| blank + one fill, positions the UI can confirm today | 71.8% | 70.1% |
| blank + one fill, positions placed beside the caption | **83.9%** | **71.1%** |
| blank + two fills, third set | 83.3% | 72.4% |

PDF artefacts **88.4%**, images **78.5%**; later packet pages 75–80% where they were
44–72%. 166 artefacts up, 3 down by one field, the six without a template unchanged.
154 misses and 296 wrong values remain (16.1 points), the majority on images: Tesseract
misreads at 150 dpi and readings from elsewhere in the slot's region. The next levers on
this track, in order of expected value: a draw-a-region control in review (the eleven
points between "detected" and "placed"); a better OCR engine for photographs
(PaddleOCR-VL read 77.6% of handwriting where Tesseract read 51%, and is installed); and
corrections that accumulate rather than replace, so a second fill narrows a region
instead of moving it.

Matching every stored page of every template made an upload take twelve seconds; only
page 0 of each template and pages with slots are candidates now, and the geometric fit
runs for the twenty-five with the most raw descriptor matches (`GEOMETRIC_FIT_POOL`).
Validated by the final `make bench` below.

### Final `make bench`, 2026-09-14 evening (the shipped build)

| benchmark | result | before today |
|---|---|---|
| dev forms (tuned against) | 100.0% recall, 78.6% precision | 100.0% / 73.9% |
| TEST2 / TEST4 / TEST5 zero-shot | 72.5% / 52.4% / 55.2% | 72.5% / 44.4% / 50.7% |
| **LARGE zero-shot** | **64.6% / 68.9%** | 61.2% / 58.1% |
| intake precision (TEST2/4/5) | 64.0% | 54.2% |
| **LARGE enrolled, blank only** | **68.4% / 68.7%** | 60.0% (−3.9 where applied) |
| **LARGE_B, one corrected fill per form** | **83.9% / 71.1%** | — (no such mechanism) |
| FUNSD linking | 17.1% | 18.3% |
| placement ≤2mm | 78.9% | 78.9% |
| round-trip | 65.2% | 69.6% |

Registry identical before and after every arm. The pre-filtered matcher reads the enrolled
arm at 68.4 / 68.7 against 68.3 / 67.8 with exhaustive matching, so the speed-up cost
nothing. Open, unchanged from the morning: round-trip −1 value (St. Clair "Employer" re-read
as "Riverside" from the regenerated page) and FUNSD −10 links. Nothing is committed.

## 2026-09-14 (night) — The rest of the plan after the research

The user's instruction: execute everything that was part of the plan after the research.
Three items were left with a bearing on 90: the photograph half of the corrected track,
the draw-a-region control the corrections track needs in review, and the research's L3.

### Photograph slots read on their own, enlarged

After one corrected fill the PDF half read 88.4% and the photograph half 78.5%, and the
photograph failures were Tesseract misreads at 150 dpi inside slots the template already
knew ("42/26/2006"). Whole-page upscaling is a measured wash (see `word_source`), but a
slot is not a page: `words_in_region` crops the slot from the page image, enlarges it
three times and reads it as one block; the reading Tesseract is surer of wins, and an
empty page reading yields to the crop only when the crop is legible. Applied to every
template slot on image pages; measured in the corrected arm below.

### Draw a region in review

The review page now teaches the matched template: "Teach template here" sends a field's
current region and value to `POST /templates/{id}/corrections`; "Place on page" lets the
reviewer drag a rectangle on the page image for a field the reader missed or misplaced,
and sends that. Taught regions are drawn dashed. Both appear only once a template has
matched (or been learned from the form). The PATCH that saves a value still does not
teach on its own: measured, teaching from the reader's own regions is worth +7.5 and from
placed regions +19.6, and a corrected value on a misplaced field would teach the wrong
place.

### L3: the grounded re-linker

`relinker.py`, off unless `RELINK_PROVIDER=ollama` (`qwen2.5:7b-instruct`, Apache-2.0,
pulled locally, 4.7 GB). Captions the vocabulary knows that the page prints and no reader
answered are put to the model with the page's lines and their positions quantised to
percent cells (LMDX's coordinate tokens); the model names a caption's value and the line
it came from; an answer that is not an exact substring of that line is discarded, a copied
caption prefix is stripped ("MARITAL STATUS Married" -> "Married"), and what survives is
admitted only if it passes the readers' own quality test. A model can invent; grounding
makes an invention a miss, never a wrong value.

First probe, one photographed page the readers missed the e-mail on: 12 grounded answers
of 16 asked, 5 of them right and new (e-mail, marital status, date of birth, occupation,
ethnicity), the rest captions the quality test removes. **52 seconds per page** for the
7B model on this machine, prompt-bound; windowing the prompt to the lines around asked
captions saved almost nothing because sixteen questions cover the page. Measured on a
fixed 30-form sample (`LARGE/ground_truth_sample30.json`, 60 artefacts, 847 fields) below.

### A leak, caught: two servers, one template folder

The first re-linker sample came back +24 points on twenty artefacts with only 36 values
adopted -- impossible -- and the dump showed why: the second measurement server had matched
the aetna form to `T50`, a template the corrected arm on the other port had just taught
*from that same fill*, and read it through reviewer slots at 10 of 10. Two backends shared
`data/templates/`. The derived-template folder is now `DERIVED_TEMPLATES_DIR` when set, so
a measurement server can be given its own; the sample was re-run in isolation. The rule
is now in `CLAUDE.md`: one registry per backend, never two measurements against one folder.

### L3 measured, in isolation

Ten-form sample (20 artefacts, 258 fields), `RELINK_PROVIDER=ollama`, its own template
folder, ten questions a page:

| | recall | precision |
|---|---|---|
| base reader | 56.6% | 64.1% |
| + re-linker, first grounding | 61.2% | 61.2% |
| + re-linker, tick-row options rejected | **61.2%** | **63.3%** |

+12 fields on 4 artefacts, none worse; 27 values adopted, 15 of them a written answer. On
the aetna form -- a behavioural-health packet the readers read 0 of 10 on -- it grounded
the name, birth date, e-mail, address, gender and marital status. What it gets wrong is
now mostly a "none" written under "Medications" that the truth does not list, and an
option it picked off a row the tick filter did not catch. The cost is the finding that
decides its place: **thirty to fifty seconds a page** on this machine for the 7B model,
prompt-bound, so it stays opt-in. Worth revisiting with a smaller Apache-licensed model
(qwen2.5:1.5b-instruct) or an MLX backend; the mechanism is safe by construction.

### The enlarged slot read, measured

Corrected arm on the second set with photograph slots re-read enlarged: **84.5% / 70.7%**
against 83.9 / 71.1 without; the photograph half 78.5 → 79.7, PDFs unchanged. 16
artefacts up by 29 fields, 11 down by 13: the crop's reading wins by Tesseract's own
confidence, which is occasionally confident about the wrong thing. Kept for the net gain;
a confidence margin rather than a tie is the obvious next adjustment, unmeasured.

### The review controls, in a browser

Playwright against the dev frontend and the port-8000 backend, 2026-09-14 night: "Learn
template from this form" on a five-page packet produced a 46-slot template; "Teach
template here" on Date of Birth answered "Taught T158 (46 slots)" and the template on disk
carried the slot marked taught with the blank's printing recorded; "Place on page" with a
dragged rectangle over the City value did the same for City with the drawn region
(x 0.140, y 0.304, 0.309 wide). Both regions draw dashed on the page. The test template
was released and the UI backend restarted, because its lazily loaded registry had picked
up the corrected arm's 108 templates from the shared folder in the meantime -- the same
leak as above, seen from the other side, and the reason measurement servers now get their
own `DERIVED_TEMPLATES_DIR`.

### Final `make bench`, 2026-09-14 night (the shipped build)

| benchmark | result | before today |
|---|---|---|
| dev forms (tuned against) | 100.0% / 78.6% | 100.0% / 73.9% |
| TEST2 / TEST4 / TEST5 zero-shot | 72.5% / 52.4% / 55.2% | 72.5% / 44.4% / 50.7% |
| **LARGE zero-shot** | **64.6% / 68.9%** | 61.2% / 58.1% |
| intake precision (TEST2/4/5) | 64.0% | 54.2% |
| **LARGE enrolled, blank only** | **68.5% / 67.9%** (50 artefacts up, none down) | 60.0% (−3.9 where applied) |
| **LARGE_B, one corrected fill per form** | **84.5% / 70.7%** (PDFs 88.4, photos 79.7) | — |
| LARGE_B, corrections from the reader's own regions | 71.8% / 70.1% | — |
| LARGE_C, two corrected fills | 83.3% / 72.4% | — |
| re-linker, ten-form sample, opt-in | +4.7 recall / −0.8 precision | — |
| FUNSD linking | 17.1% | 18.3% |
| placement ≤2mm | 78.9% | 78.9% |
| round-trip | 65.2% | 69.6% |

Measured on a server with its own `DERIVED_TEMPLATES_DIR`; registry identical before and
after every arm. Nothing is committed. The user's UI backend on port 8000 runs this build;
the frontend dev server on port 3000 was started for the browser test and left running.

## 2026-09-15 — Photographs that never matched, and a second reader for slots

Committed the day's work first (three commits on `nathan`, nothing pushed), then went
after the remaining loss on the corrected track. The dumps showed the largest single
cause: **30 of the 111 photographs never matched their own template** (scores 0.05-0.5
against the 0.5 threshold), so no correction could reach them and they were read
zero-shot -- about an eighth of every field on the set. The mechanism is scale: a master
is rendered at 1,224 px across and a photograph arrives at 1,275, and that four percent
halves ORB's geometric agreement. Scored offline against their proper page, all thirty
match once both images are brought to one width (1,200 px) and ORB is given 2,000
features (`MATCH_WIDTH`, `MATCH_FEATURES`; every score 0.72-1.0).

Second: RapidOCR (ONNX PaddleOCR models) is installed and reads an enlarged slot crop
better than Tesseract -- "01/26/1958" at 0.98 where Tesseract read "ic 01/26/1958 SEX
ASSIGNE" at 0.48. It loses on whole pages because it returns lines, not words; inside a
slot the slot is the geometry, so its lines are split into words by their share of the
line and the printed-text subtraction works as before. `REGION_OCR_ENGINE=rapidocr` by
default, falling back to Tesseract when it is not installed. Both measured below.

### Measured together, then split

One run with both changes, corrected arm on the second set: 84.5 / 70.7 → 83.5 / 68.6,
photographs 79.7 → 77.6. Split by whether a photograph had matched before the fix: the
newly matched ones (14) gained 13 fields, the previously matched ones (45) lost 40 -- and
their only change was RapidOCR reading their slots. Its confidence sits near 0.98 for
everything, so its crop reading always outranked the page's, wrong or not. Matching at one
width stays (102 of 105 photographs match, from 81 of 111); RapidOCR goes back to opt-in
(`REGION_OCR_ENGINE=rapidocr`) until the arbitration is something other than confidence.

### Faster measurement, same numbers

The user asked for the loop to move faster without measuring less. Three changes, none
of which touch what is measured:

- The upload endpoint was `async` and called the analyser on the event loop, so every
  upload waited for the previous one and the health check hung during an analysis. The
  analyser and the preprocessing now run in the thread pool. Four scoring workers
  (`evaluate_real_forms.py --parallel 4`, the default in `benchmark_all.py`) score the
  ten-form sample in 48 s where one took 82 s, with per-artefact lines identical -- and
  that on a machine also running two other measurements.
- Enrolling and teaching the 112 blanks costs half an hour a run and yields the same
  templates whenever only the reading side changed. `evaluate_corrected.py --snapshot DIR
  --templates-dir DIR` keeps the taught templates after one run and reuses them from a
  fresh backend the next time, so a read-side change is scored in ten minutes.
- A ten-form sample (`LARGE/ground_truth_sample10.json`) answers "is this worth a full
  run" in a minute; the full set decides.

### The hybrid, measured on the main forms at last

`VISION_PROVIDER=ollama` (qwen2.5vl:7b, local) had never been measured on LARGE -- gate
G2 in `PATH_TO_90.md` had sat at "next" since 2026-09-13. Ten-form sample (20 artefacts,
258 fields), `VISION_CACHE=1`, its own template folder:

| | recall | precision |
|---|---|---|
| geometry only | 56.6% | 64.1% |
| geometry + re-linker (L3) | 61.2% | 63.3% |
| **geometry + vision** | **62.0%** | **67.3%** |

Photographs +12 recall on the artefacts scored first, no artefact worse. The cost is the
same finding as for L3: about two and a half minutes a page on this machine, so the full
set is an eighteen-hour run. It is the strongest zero-shot lever measured on these forms,
and it is already the shipped default outside benchmarks (`vision_provider = "ollama"`);
every quoted figure is geometry-only by protocol, and that protocol under-reports what the
product does on a photograph by about that much.

### Overnight loop (user asleep; "keep improving for eight hours")

Each step below is one change, measured on the second fill set through the fast loop
(taught templates reused from the snapshot, four scoring workers), kept if it wins on
recall without losing precision, reverted otherwise. Baseline for the loop: the corrected
arm at **85.0 / 70.6** (photographs matched, Tesseract slot reads).

1. **Wider crop margins** (1.5 slot heights to the left, 0.5 to the right): **reverted**.
   82.8 / 65.9 -- the crop pulled in the caption printed beside the slot, which the blank's
   recorded printing, learned with a narrower margin, did not cover. 41 artefacts worse.
   The crop and the recorded printing must agree on their reach.
2. **Wide-gap cut inside a slot** (a line's value stops at the grouper's own wide-gap
   split; "Savannah GA" becomes "Savannah"): measuring.
   **Reverted**: 84.8 / 70.5, two artefacts lost six fields and none gained. A date typed
   into boxed cells, "04 / 17 / 1986", is a value with wide gaps inside it.
3. **RapidOCR as a second opinion**, asked only when Tesseract's confidence on a crop is
   below the legibility line (`REGION_OCR_ENGINE=tesseract+rapidocr`): measuring.
   **Reverted, and removed**: 84.9 / 69.1, 18 artefacts worse against 9 better. Asked
   only where Tesseract was unsure, RapidOCR still overruled more right readings than it
   fixed. Both RapidOCR crop paths are out of the code; the probe that promised them read
   one crop, and one crop is not a measurement.
4. **First line only for one-line questions** (a region drawn a little tall caught the
   e-mail written under a name; multi-line types keep every line): measuring.
   **Reverted**: 85.0 / 70.2, eleven fields gained and eleven lost, precision −0.4. A
   region drawn tall catches the line below about as often as a value's second line is
   the value.
5. **A crop reading that is a strict part of the page's reading loses** ("rid" inside
   "Ingrid" is the page's word cut at the crop's edge, not a surer reading): measuring.

### `make bench` on the overnight build (photographs matching at one width)

| benchmark | result | previous |
|---|---|---|
| LARGE zero-shot | 64.6% / 68.9% | same |
| LARGE enrolled, blank only | **68.8% / 67.9%** | 68.5% / 67.9% |
| LARGE_B, one corrected fill | **85.0% / 70.6%** | 84.5% / 70.7% |
| dev / TEST2 / TEST4 / TEST5 | 100 / 72.5 / 52.4 / 55.2 | same |
| FUNSD / placement / round-trip | 17.1% / 78.9% / 65.2% | same |

Scored with four workers on a server with its own template folder; registry identical
before and after. The hybrid pass over all 112 forms runs in the background from its cache
whenever the machine is free; its output lands in `artifacts/hybrid_large_zero_shot.txt`.
   **Reverted**: 84.5 / 70.6, eleven artefacts worse against two better. When the crop's
   reading is a strict part of the page's, the page's is usually "Name Analise" -- the
   caption glued on -- and the crop was right.

Five read-side rules measured overnight, five reverted. The taught-slot read is at a local
optimum with rules of this kind; what remains on the second fill set is OCR quality on
photographs and a few forms whose photograph OCR is poor throughout. The loop turns to
teaching-side placement and to the hybrid.
6. **Teaching-side placement**: the taught region starts 0.015 of the page width before
   the value (was 0.005) and reaches 0.2 of the value's height above and below (was 0.3).
   Re-taught into a new snapshot; measuring.
   **Kept, marginal**: 85.1 / 70.8 against 85.0 / 70.6 -- PDFs +1.1, photographs −1.1,
   28 artefacts up and 28 down. The wider left margin is what helped; the tighter vertical
   reach is what cost the photographs, and splitting the two would take another hour of
   re-teaching for half a point. Left as is.

**Overnight tally.** Six changes measured on the second fill set; one kept. The corrected
track ends the night at **85.1% / 70.8%**, the enrolled arm at 68.8 / 67.9, zero-shot at
64.6 / 68.9. The rules that were reverted are recorded above so nobody retries them
without a new reason. The hybrid pass continues from its cache; whatever fraction of the
112 forms it has read by morning is in `artifacts/hybrid_large_zero_shot.txt` once the
scorer exits, and its per-artefact dumps are readable before that.
7. **A confidence margin before a crop reading replaces the page's** (`CROP_MARGIN`
   0.05; a tie no longer goes to the crop): **kept**. 85.1 / 70.8 → **85.4 / 70.7**,
   eleven artefacts up and one down, photographs +1.0. The night's corrected-track figure
   is 85.4 / 70.7 on the second fill set.
8. **Vision only where a page asks a question** (`_worth_a_model_call`: geometry found a
   field, or the page prints a caption the vocabulary knows): **kept**. Identical numbers
   on the cached sample (62.0 / 67.3, no artefact changed) with 45 model calls instead of
   58. Welcome letters and policies no longer cost minutes each; the full hybrid pass runs
   with it from here.

### Morning summary, 2026-09-15

Overnight, on the user's instruction to keep improving for eight hours:

| track, on the 112 forms | recall | precision |
|---|---|---|
| zero-shot, geometry only (`make bench`) | 64.6% | 68.9% |
| zero-shot, hybrid (vision on), first 79 of 222 artefacts, same artefacts geometry-only in brackets | **70.7%** (67.4) | 70.2 (69.5) |
| enrolled from the blank | 68.8% | 67.9% |
| one reviewer-corrected fill per form, scored on a second fill | **85.4%** | **70.7%** |

Kept overnight: photographs match their template at one width (30 of 111 had never
matched); a confidence margin before a crop reading replaces the page's; a wider left
margin for taught regions; the vision model called only on pages that ask a question; the
analyser off the event loop, four scoring workers and taught-template snapshots, which
turned a 65-minute measurement into 15. Reverted after measuring: wider crop margins, a
wide-gap cut, RapidOCR as sole reader and as second opinion, first-line-only, a crop-cut
rule. Four commits on `nathan`, nothing pushed; tree clean; smoke test green.

The hybrid pass over all 112 forms continues in the background from its cache (about two
minutes a page even with the gate); its summary lands in `artifacts/hybrid_large_zero_shot.txt`
when the scorer exits, and on the artefacts done so far it reads photographs 6.7 points
better than geometry alone. The measurement servers were stopped except the vision one on
port 8011; the UI backend on port 8000 runs the final commit; the frontend dev server on
port 3000 is still up.
