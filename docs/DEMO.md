# Running the demo

For whoever is driving on the day. Six minutes of clicking, with the numbers to say beside
each step and a fallback for every part that can fail.

Everything below was walked through on 2026-09-19 against the current build. Measured
timings on the development laptop: **a PDF reads in about 4 seconds, a photograph in about
8.** Nothing in the demo path waits on a network, a model, or an API key.

## Before you start

```bash
# terminal 1 — backend on 8000
cd backend && VISION_PROVIDER=disabled SLOT_VISION=disabled \
  ../.venv312/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# terminal 2 — staff app on 3000
cd frontend && npm run dev
```

`VISION_PROVIDER=disabled` matters: it keeps every page on the machine and off anybody's
API quota. Check it took, and that the audit trail found its database:

```bash
curl -s localhost:8000/api/v1/health
# {"status":"ok", ..., "vision_provider":"disabled", "audit":{"backend":"postgresql", ...}}
```

Then open `http://localhost:3000/forms` once, before the audience is watching, so Next.js
has compiled the route.

**Have ready:** a filled PDF of a form, and the same form as a phone photograph. Use
something from `data/real-forms/`. Do **not** use anything from `UNSEEN`, `UNSEEN2`,
`UNSEEN_TICK` or `LOCKED/` — opening one of those in the UI spends a held-out set, and
three forms have already been lost that way.

## The script

**1. Capture (`/capture`).** Upload the filled PDF. It lands on the review page in about
four seconds.

> "This is a form a clinic published, filled with an invented patient. Nothing here has
> ever seen this layout before."

**2. Review (`/review/{id}`).** The page shows the upload with a box around every answer
region — green where a value was read, amber where the system wants a human to look — and
the values beside it with a confidence and where each came from.

> "It has read thirty-three values. It is not claiming to be sure about all of them: the
> amber ones are flagged, and the count of what still needs review is at the top."

Point at one flagged field and correct it in place. The correction saves immediately.

**3. Teach it (optional, 30 seconds).** On a field the reader put in the wrong place, click
**Place on page** and drag a rectangle around the real answer.

> "That taught the template, not this one form. Every later copy of this form is read
> there."

**4. Regenerate.** Click **Regenerate filled PDF**. The copy opens in a new tab.

> "That is the same blank form, refilled from what we stored. The values are in green so
> you can tell what the system wrote from what was already on the page."

Say the line under the button out loud — it will read *"Every value found a slot"*, or it
will name the values that had nowhere to go. That honesty is the point.

> "Across a hundred and eleven real layouts, we count six things a copy can get wrong — a
> value dropped, written twice, run off the page, written over the form's own printing, a
> tick in the wrong box or missing. All six are at zero, on every page of every form."

**5. The second form (30 seconds).** Upload the *photograph* of the same form. It matches
the template learned a minute ago and is read through it.

> "Same form, phone camera, no re-learning. That is the case a clinic actually lives in:
> one layout, used thousands of times."

**6. Stored forms and history (`/forms`).** Show the list, open one, click **History**.

> "Every read and every correction is an append-only row. A correction never overwrites
> what a patient wrote — the earlier value stays."

Then sign off, and point at the caveat the app itself prints: *identity not verified*.

> "It records who claimed to check it. Nothing authenticates that yet, and the app says so
> rather than pretending."

## Numbers, said honestly

Quote recall **and** precision, or neither.

| | |
|---|---|
| A layout it has never seen | 67.4% / 78.2% |
| A layout learned once, one copy corrected | **91.8% / 94.1%** |
| Held out, twenty-one unseen layouts | **93.3% / 90.0%** |
| Held out, ten layouts, no blank form at all | **91.9% / 98.9%** |
| Tick boxes | **86.1%** of 417 ticked questions |
| Mistakes in a regenerated copy | **zero**, 111 layouts, every page |

If asked about the ≥95% target: we are at 93.3% on held-out forms, the gap is wrong-label
binding, and `docs/KNOWN_LIMITATIONS.md` says what has been tried. If asked about
handwriting: 43–53%, it is the one criterion we do not meet, the ceiling on that set is
91.8% and the honest read is that it needs a better recogniser than a laptop runs.

## If something breaks

| What | Do this |
|---|---|
| Upload rejected | Check the file is JPG, PNG, HEIC or PDF. The app says so itself. |
| Page reads nothing | It shows *"OCR read no text from this page; check image quality."* Say that is the intended behaviour — silent rather than confidently wrong — and move to the next form. |
| "Unknown template" | Should not happen any more; a form whose template is missing now derives one. If it does, regenerate again. |
| Image upload returns 503 | `tesseract` is not on the PATH. Demo with the PDF instead. |
| Backend wedged | Restart terminal 1. Stored forms and the audit trail survive it. |
| Everything is on fire | Fall back to the walkthrough: screenshots of each step above, plus a regenerated PDF, kept outside the repo. |

## What not to do

- Do not upload a form from a locked held-out set.
- Do not demo handwriting.
- Do not turn the vision model on for speed — it invents checkbox answers, including a
  patient's sex.
- Do not quote a single accuracy number without its precision.
