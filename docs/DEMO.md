# Final demo

The final review is one hour on Zoom with Frank: the current state of the application, shown
live, and a review of the work. This is the script, the evidence to have open, and a fallback
for every part that can fail. It follows the structure the LOF LABS Participant Guide asks for:
problem, user, workflow, core feature, evidence, limitations, next steps, and what changed
since the prior gate.

Walked through end to end on 2026-10-21 against the current build. Timings on the development
laptop: a PDF reads in 2 to 6 seconds, a photograph in about 5. Nothing in the demo path waits
on a network, a model or an API key.

## The hour

| Minutes | What |
|---|---|
| 0-5 | Problem, user, workflow |
| 5-20 | Live demo, five steps below |
| 20-30 | Evidence: the measurements and how they were taken |
| 30-40 | Limitations, honestly, and next steps |
| 40-60 | Questions |

## Before the call

```bash
# terminal 1: backend on 8000, on its own empty folders so the stored-forms list holds only
# what is uploaded during the call (benchmark runs share the default folder)
cd backend && VISION_PROVIDER=disabled SLOT_VISION=disabled \
  ARTIFACT_ROOT=../../demo-state/artifacts DERIVED_TEMPLATES_DIR=../../demo-state/templates \
  ../.venv312/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# terminal 2: staff app on 3000
cd frontend && npm run dev
```

`VISION_PROVIDER=disabled` keeps every page on the machine and off any API quota. Check it
took, and that the audit trail found its database:

```bash
curl -s localhost:8000/api/v1/health
# {"status":"ok", ..., "vision_provider":"disabled", "audit":{"backend":"postgresql", ...}}
```

Then open `http://localhost:3000/capture` and `/forms` once before sharing your screen, so
Next.js has compiled both routes.

**The demo inputs** are fixed, numbered in the order they are used, and kept outside the
repository because they are real clinic forms filled with invented data (`../demo-inputs/`):

| File | What it is |
|---|---|
| `1-blank-form.pdf` | A four-page physical therapy intake packet, blank |
| `2-filled-form-with-ticks.pdf` | The same packet filled in, with sixteen tick-box answers |
| `3-same-form-phone-photo.jpg` | Its first page as a 150-dpi image, the way a scanner delivers it (a render, not a camera photo) |
| `4-unreadable-page.png` | A page with nothing legible on it |
| `5-wrong-file-type.txt` | Not a form at all |

Do not use anything from `UNSEEN`, `UNSEEN2`, `UNSEEN_TICK` or `LOCKED/`. Opening one of those
in the app spends a held-out set.

## 1. Problem, user, workflow

**Problem.** Outpatient clinics still take intake on paper, and someone retypes every packet
into the clinic's records by hand. It is slow, it introduces errors, and the clinic's own form
is the thing patients and staff already know.

**User.** Front-desk or intake staff, the person doing that retyping today.

**Workflow.** The patient fills in the clinic's own packet. Staff scan or photograph it.
PrelimMD reads it and marks everything it is unsure of. Staff check it on screen, correct, and
sign off. The answers are stored, and a clean filled copy of the clinic's own form can be
produced from them. A clinic uses a handful of forms thousands of times, so each form is learned
once from its blank and every later copy is read through it.

**What the software does not do:** diagnose, triage or advise; read a signature's content;
read handwriting well; or send a page off the machine. A person checks every form.

## 2. The live demo

**Step 1. Learn the form.** `/capture`, upload `1-blank-form.pdf`. On the review page, tick
*This is a blank form*, then *Learn template from this form*.

> "A clinic does this once per form. It has learned four pages and 64 answer regions from the
> blank alone, including every tick-box question."

**Step 2. Read a filled copy.** `/capture`, upload `2-filled-form-with-ticks.pdf`. It matches
the form just learned and lands on the review page.

> "Green is read, amber is waiting for a person. Each answer says where it came from and how
> sure the system is." Click *Page 2*: "These are the tick-box answers, read by measuring the
> ink in each box. This page holds six of the form's sixteen tick answers, and all sixteen
> are right."

Correct one value in place and press *Save correction*.

**Step 3. Regenerate.** Press *Regenerate filled PDF*. The copy opens in a new tab.

> "That is the clinic's own blank form, refilled from what was stored. Values are in green so
> nobody mistakes a regenerated answer for one carried over. Ticks are redrawn as an X in the
> blank's own box. It is the whole packet, every page."

Read out the line under the button: *Every value found a slot*, or the list of values that had
nowhere to go. The copy says what it could not do rather than quietly doing less.

**Step 4. An image instead of a PDF.** `/capture`, upload `3-same-form-phone-photo.jpg`.

> "Same form as an image rather than a PDF, no re-learning. It matches and reads through what
> it learned." Do not call it a phone photo: it is a clean render, and a real camera photo reads
> less well.

**Step 5. Inputs that are wrong.** Upload `4-unreadable-page.png`: the review page says *OCR
read no text from this page; check image quality.* Then upload `5-wrong-file-type.txt`: the
upload page says *Only valid JPG, PNG, HEIC, and PDF files are accepted.*

> "On a bad page the system says so and reads nothing, rather than guessing. For a clinical
> form, silent and flagged is the safe failure."

Finish on `/forms`, open the corrected form, and press *History*: the correction is there with
the value it replaced, which is kept, never overwritten.

## 3. Evidence

All on real clinic forms downloaded from public websites and filled with invented patients.
Quote recall and precision together, or neither.

| Measurement | Result | Script |
|---|---|---|
| Form learned once, one copy corrected, scored on a second patient | **91.8% / 94.0%** | `evaluate_corrected.py` |
| Held out: 21 layouts nobody tuned against | **92.0% / 89.1%** (93.3 / 90.0 with the optional local model) | locked set, totals only |
| Held out: 10 layouts with no blank form at all | **87.7% / 97.8%** (91.9 / 98.9 with the optional local model) | locked set, totals only |
| A form never seen before | 67.4% / 78.1% | `evaluate_real_forms.py` |
| Tick boxes, 417 ticked questions | **86.1%** read | `evaluate_ticks.py` |
| Mechanical mistakes in a regenerated copy, every page, 109 layouts enrolment accepts | **zero** | `evaluate_regeneration.py` |
| Round trip: read, learn, refill, read back | 20 of 23 values | `evaluate_roundtrip.py` |

The benchmark is 112 real layouts rather than the three synthetic templates the work plan
named. That is a deliberate deviation: a synthetic corpus once read 96.9% while real forms read
45%, so it was deleted and real forms became the only measure.

## 4. Limitations

From `docs/KNOWN_LIMITATIONS.md`, said plainly:

- **Gate 2 asked for 95% on printed text.** The best is 93.3% on held-out forms, with the optional
  local model; 92.0% as demonstrated, with it off. What blocks the
  rest is a value bound to the wrong question, and four approaches to it were tried and reverted.
- **Handwriting reads 43-53%** on a 49-field set, against a revised target near 73.5%. Claude
  Opus reads the same pages at 91.8%; no model a laptop can run comes close.
- **Placement:** 15 of 21 values land within 2mm. The misses are values the reader ran
  together from several fields, not placement error.
- **Genuinely degraded scans** are largely unread, and come back flagged rather than wrong.
- **Sign-off is not authenticated.** It records a name; the app says so on screen.
- **A caption can be filed under the wrong question.** The matcher maps a printed caption to a
  field by its words, and a caption that shares only generic words with another field's name
  can land there: "Emergency Contact Relationship" under the contact's name. Regeneration then
  writes that value into every slot with the same key. One such class, a date of any event
  becoming the birth date, was fixed on 21 October; others remain.

## 5. Next steps

1. **Binding a value to the right question.** The one defect behind both open accuracy
   criteria. The next attempt is a learned model over position and caption, not another rule.
2. **Handwriting** through a stronger recogniser, run offline on a backlog rather than live.
3. **LOINC beyond four concepts**, verified against a LOINC release, and FHIR `Patient` mapping
   for identity and contact fields.
4. **Authentication** behind sign-off, before any real clinic could use it.

## 6. What changed since Gate 3

Over sixty commits since 8 September, in five themes:

- **Measured on real forms only.** The synthetic corpus was deleted; 112 real clinic layouts and
  three locked held-out sets replaced it, with a scorer for every claim.
- **The faculty review's three items.** FHIR `QuestionnaireResponse` output with LOINC codes; an
  append-only audit trail where a correction never overwrites what was there; and the AGPL
  PyMuPDF dependency removed from the product, so the manifest is clean under Apache 2.0.
- **Tick boxes** went from 10% of ticked questions read to 86.1%.
- **Regeneration** was rebuilt: the master is the page itself, regenerated values are green,
  every page of a packet is refilled, and nothing is ever written over the form's own printing.
  A new scorer counts the mechanical mistakes a copy can make (a value dropped, doubled, run
  off the page or over the printing, a tick loose or misplaced), and all are at zero. It cannot
  see a value the reader filed under the wrong question; that is an extraction fault, below.
- **Precision** went from 78.8% to 94% on the corrected track, by recognising when a proposed
  answer is only the form's own printed text.

## If something breaks

| What | Do this |
|---|---|
| Upload rejected | The app names the accepted types itself. |
| Page reads nothing | That is the warning in step 5. Say it is the intended behaviour and move on. |
| Image upload returns 503 | `tesseract` is not on the PATH. Use the PDF. |
| Backend wedged | Restart terminal 1. Stored forms and the audit trail survive it. |
| Need to run the demo again | Skip step 1: the form is already learned, and learning the same blank twice registers a duplicate template. |
| Anything worse | Play the backup recording, `../demo-walkthrough/rec/demo-walkthrough.mp4`, captioned step by step against the real app. |

## What not to do

- Do not open a form from a locked held-out set.
- Do not demo handwriting.
- Do not turn the vision model on: it invents checkbox answers, including a patient's sex.
- Do not quote an accuracy figure without its precision.
