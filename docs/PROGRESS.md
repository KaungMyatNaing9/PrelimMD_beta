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
measurements -- **but treat this as unconfirmed.**

The same technique on FUNSD: **+3.0 on ten forms, -1.0 on all fifty** (41.5% -> 40.5%, 42
templates derived). Ten forms produced a false positive of the same size and sign as the
intake result, which rests on 63 fields. Until intake is reproduced on a larger set, the
honest position is that self-templating is **unproven**, not proven.

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
