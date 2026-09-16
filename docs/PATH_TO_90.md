# Path to 90% — what it takes, and on which forms

**Question asked (user, 2026-09-13):** *"create a plan to continually improve until they
reach at least 90% accuracy across all forms (that 100+) using only free tools."*

**Short answer: 90% is reachable, but not on the number we have been quoting.** There are
two different accuracies hiding under one word, they differ by 20–30 points, and only one
of them can reach 90% with free tools. This document separates them, says which one the
product actually needs, and gives a measured, ranked plan for each.

---

## 1. The two accuracies

| | what it means | who sees it |
|---|---|---|
| **Zero-shot** | a form the system has never seen, read on first upload | a new clinic's first day |
| **Enrolled** | the blank form was learned once, then read thousands of times | every day after that |

Every headline figure in `CLAUDE.md` except one is **zero-shot**. That is the hardest
possible framing, and it is not the one a clinic lives in: a practice has perhaps five to
fifteen intake forms and uses each of them continuously.

**Published work says zero-shot 90% is not available to anyone right now, free or paid.**

* Zero-shot GPT-class extraction on structured **clinical** documents: **~74%**
  ([Lido, 2026](https://www.lido.app/blog/best-ocr-for-forms)).
* FUNSD question→answer **linking** SOTA, the closest public analogue to "bind this value
  to that caption": **44.1% (StrucTexT) – 58.96%**. Figures above 80% quoted for FUNSD are
  entity *labelling*, a different task.
* LayoutLMv2 loses **19–27 points of Micro-F1** moving from a single known template to
  unseen templates; LMDX (a frontier LLM) loses **<5%**
  ([LMDX, arXiv 2309.10952](https://arxiv.org/pdf/2309.10952)).
* Few-shot systems **with upfront labelling reach 95%+** on complex documents.

Our own hybrid sits at **76.8% (TEST2) / 65.1% (TEST4)** zero-shot. That is already at or
above the published zero-shot clinical figure. **Asking for 90% zero-shot is asking to beat
GPT-class zero-shot by ~16 points using free local models.** Nothing in the literature
suggests that is available, and any plan that promises it is promising something it cannot
show.

**So the target splits:**

| track | target | basis |
|---|---|---|
| **Zero-shot, all 85 LARGE forms** | **80%** | ~6 points above the published clinical zero-shot figure; reachable on the evidence below |
| **Enrolled, all 85 LARGE forms** | **≥90%** | the few-shot literature says 95%+; we measured +12.7 points from enrolment alone on TEST4 with no model at all |

Both are "free tools only". Neither needs a paid API.

---

## 2. Where we actually are

Measured on `data/real-forms/LARGE/` — **112 real clinic forms, 222 artefacts (PDF + JPG),
2,775 annotated fields** since the evening of 2026-09-13 (85 / 170 / 2,127 before the 26
unlocked blanks from NEW, TEST2, TEST4, TEST5 and the original set were folded in), every
form filled with **its own distinct invented patient** (`scripts/fill_forms.py`, seeded per
filename).

| arm | score | date |
|---|---|---|
| geometry only, 85 forms | **62.3%** (1,326 / 2,127) | 2026-09-13 |
| geometry only, 112 forms | **61.2% (1,699 / 2,775)**, precision 58.1% | 2026-09-13 |
| geometry only, 112 forms, assignment reader (L2) | **64.6% (1,792 / 2,775)**, precision **67.8%** | 2026-09-14 |
| hybrid (ollama qwen2.5vl:7b) | not yet measured on LARGE | — |
| enrolled, 85 forms | **60.0%** overall; own-blank forms 64.9 → 61.1 (**−3.9**), others ±0 | 2026-09-13 |
| enrolled, 112 forms, after L1 (additive merge, every page, widgets, blank subtracted) | **68.3%**, precision **67.8%**; own-blank 64.4 → 68.2 (**+3.7**), others ±0 | 2026-09-14 |
| enrolled + one corrected fill per form, scored on a second fill (LARGE_B) | **85.4%**, precision **70.7%**; PDFs 89.2, photos 80.9 | 2026-09-15 |

Smaller sets, for reference (these are the historical figures):

| set | geometry | hybrid | enrolled |
|---|---|---|---|
| TEST2 (69 fields) | 72.5% | 76.8% | 69.6% |
| TEST4 (63 fields) | 44.4% | 65.1% | **57.1%** |
| TEST5 (67 fields) | 50.7% | 59.7% | — |

**Precision is the other half and it is bad: ~50%.** Recall figures cannot fall when the
extractor invents fields, so quoting recall alone is misleading in exactly the direction
that flatters us. `scripts/intake_field_coverage.py` measures it. Every gate below names
both.

---

## 3. Why the corpus changed today, and why that matters

Until 2026-09-13 every filled form in the corpus carried **the same invented patient**
("Amara C Okafor", one address, one phone number, on 150 artefacts). That is a benchmark
that can be passed by memorising one string, and any threshold that happened to suit one
name length would have read as accuracy.

`scripts/fill_forms.py` now draws a **different patient per form** — name, address, city,
state, ZIP, three phone numbers, insurer, employer, emergency contact, all from pools,
seeded from the filename so the draw is reproducible and the ground truth stays valid
across re-runs. Phone punctuation varies between forms on purpose (scoring strips
punctuation, so it costs nothing and catches a reader keyed to one house style).

Three filler fixes landed with it, all of which grew the corpus rather than the score:

1. **Write inside the span, not after it.** These forms put a whole row in one text span —
   `"Address ______ City ______ State"` — so writing after the *span* put the value past
   the end of the row. Twelve of one form's twelve labels were refused for this and eight
   forms filled nothing at all. Position now comes from `page.search_for()`, which reports
   where a substring truly sits.
2. **Six pages, not two.** These are packets; a welcome letter, a HIPAA notice and a
   financial policy commonly sit ahead of the registration page.
3. **Fillable PDFs.** 23 of 86 forms carry AcroForm widgets. Widget values are typed into
   the fields whose **name the designer wrote** (exact match only — `"First name"` is used,
   `"demographic t 7"` is not), then `doc.bake()` flattens them into page text so the PDF
   and image readers both see them, exactly as a printed fill would.

Result: **610 → 2,127 fields**, forms with fewer than 5 fields **28 → 7**.

> The one rule that must not be broken here: **the filler never imports the extractor.**
> Filling a benchmark with our own detector and then scoring that detector against it only
> proves the detector agrees with itself.

---

## 4. The plan — ranked by measured evidence, not by appeal

> **Superseded in part, 2026-09-14 — see [`RESEARCH_TO_90.md`](RESEARCH_TO_90.md).**
> An eight-agent research pass re-ranked these levers against the measured loss breakdown and
> changed three things materially:
>
> 1. **The 90% zero-shot target is arithmetically dead.** 1,699 + 791 misses = 2,490 = **89.7%**:
>    fixing *every* miss perfectly still falls short of 90%. Reaching it needs **74.3% of all
>    1,076 failures** fixed, misses and wrong values alike. Zero-shot realistically tops out at
>    **70–75%**; **≥90% is an enrolled-track number only**.
> 2. **Track B was not wrong — it was broken by three verified code defects** (dead registration,
>    a hardcoded page 0, and a merge rule that lets a template misread outrank a correct
>    zero-shot value). G3's negative result is explained. See **L1**.
> 3. **The highest-value new lever is arbitration, not reading**: replacing `_best_layout`'s
>    field *count* with a global assignment. That is where the ≥70% precision target lives.
>
> Mapping: **A1** → not a priority (OCR ceiling is ≈3.6 pts, not "+4 to +8"); **A2** → **L2/L3/L4**;
> **A3** → unchanged; **A4** → **L2** (precision is an objective problem, not a rule problem);
> **B1** → answered, see **L1**; **B2** → **L1** + per-template corrections; **B3** → unchanged.

Each lever states what it costs, what it is expected to gain, **and what result would kill
it**. Anything that cannot be falsified is not on this list.

### Track A — zero-shot, target 80%

*(Target revised to **70–75%** by [`RESEARCH_TO_90.md`](RESEARCH_TO_90.md) §1.)*

**L3 (research) — grounded re-linker: measured 2026-09-14, +4.7 recall / −0.8 precision on a ten-form sample at 30–50 s a page; opt-in (`RELINK_PROVIDER=ollama`). See [`PROGRESS.md`](PROGRESS.md).**

**A1. PaddleOCR-VL-1.6 in the `word_source` slot.** ~~*Largest untested lever.*~~
**Demoted 2026-09-14:** the legibility census caps *any* OCR work at **≈3.6 recall points**
(71 misreads + 29 JPG near-misses; 36 values are not on the page at all). See
[`RESEARCH_TO_90.md`](RESEARCH_TO_90.md) §3 "Not recommended: more OCR work".
Apache 2.0, 1B params on ERNIE-4.5-0.3B, ~1.8GB, runs locally.
**96.33% on OmniDocBench v1.6**, SOTA over both open and proprietary systems as of
2026-05-28 ([HF](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6),
[arXiv 2606.03264](https://arxiv.org/html/2606.03264v1)). We already measured it reading
**77.6%** of our handwritten values against **51%** for `qwen2.5vl:7b`.
*Expected:* the FUNSD census says the field builder discards ~40 points of text the reader
already recovers, so a better reader alone will not close the gap — but rapidocr's +19.6
points of legible text produced +6.1 points of field values, so budget **+4 to +8**.
*Kill condition:* any regression on TEST2/dev, the way `OCR_ENGINE=rapidocr` won FUNSD and
lost everything else (dev 100% → 85.3%). Ship it opt-in until three sets agree.
*Pull:* `hf.co/PaddlePaddle/PaddleOCR-VL-1.6-GGUF` — **not** the community
`MedAIBase/PaddleOCR-VL` tag, which ships no vision projector and takes 500s per image.

**A2. Fix the field builder, not the reader.** *Best evidence, least glamorous.*
**Now split into L2 (global assignment), L3 (grounded re-linker) and L4 (PDF vector/font cues)
in [`RESEARCH_TO_90.md`](RESEARCH_TO_90.md).**
FUNSD census: answers legible to OCR **78.2%**, answers arriving as a field value **38.4%**,
correctly linked **22.0%**. The builder is where ~40 points go. Concretely:
stacked layouts (value on the line *below* its caption) are not modelled at all, and that is
the single most common shape we refuse.
*Expected:* **+5 to +10**. *Kill condition:* precision falls below the pre-change figure.

**A3. Gate `VISION_TABLES` by whether a table is present.**
Worth **+10.7 points on FUNSD** (table cells 20.4% → 39.8%) and **−9.5 points of precision
on intake** — on a form with no table, asking for tables invents one. The fix is not to
choose one setting but to detect ruled grids first (`field_layout.detect_boxes()` already
finds rules) and ask for tables only then.
*Expected:* **+2 to +4** on mixed corpora, **0** on pure intake. *Kill condition:* precision
moves at all on forms with no table.

**A4. Raise precision to 70%.** **Now L2** — a cardinality objective *rewards* emitting the
form's own printed text, so precision is fixed by changing the objective, not by adding rules
([`RESEARCH_TO_90.md`](RESEARCH_TO_90.md) §3 L2). Currently ~50%: half of what a reviewer is shown is the
form's own printed text. Three general rules already landed (answer-space gap,
`is_blank_marker`, `_is_printed_prose`). The remaining classes are countable from
`intake_field_coverage.py`.
*Note:* this does not raise the headline — it is the number that keeps the headline honest,
and Gate 3's "graceful behaviour" is unarguable without it.

### Track B — enrolled, target ≥90%

This is the track that can actually reach 90%, and it is currently **one script, unmeasured
at scale**.

**B1. Measure enrolment on all 85 LARGE forms.** `scripts/evaluate_enrolled.py` learns each
blank form first, as a clinic would at setup. TEST4 went **44.4% → 57.1%** with *no model
and no tuning*; TEST2 went the other way (72.5% → 69.6%) and that is unexplained. 85 forms
will settle whether enrolment is a win or a wash. **Do this before anything else in Track B
— everything below is contingent on it.**
*Kill condition:* if enrolment is not clearly positive across 85 forms, Track B's premise is
wrong and the 90% target has to be renegotiated rather than chased.

**Cause found 2026-09-14 — three verified code defects, not a wrong premise. See
[`RESEARCH_TO_90.md`](RESEARCH_TO_90.md) §3 L1; B2/B3 are unblocked once L1 lands.**

**Measured 2026-09-13 on all 85: the kill condition is met.** 50 blanks enrolled, 36 not
derivable; on the 100 artefacts with their own template, 64.9% → 61.1% (−3.9); on the 70
without, exactly ±0 (matching never mis-fired). The mechanism — derive from the blank's
page 0, read through it — reads *worse* than no template. B2 and B3 are on hold until
the reason is found; `birminghamheart_com__Patient-form_FILLED.pdf` went 14 → 1 and is the
case to open. Details in `docs/PROGRESS.md`.

**B2. Enrol from the blank form plus one corrected fill.** The literature's 95%+ comes from
*few-shot with labelling*; our enrolment currently uses the blank form only. The review UI
already captures corrections (`PATCH /api/v1/form-analyses/{id}/fields/{id}`), so the second
example is free — a receptionist fixing one form teaches the template.
*Expected:* this is the mechanism that gets from ~57% to ≥90%, on the LMDX finding that a
known template is worth 19–27 points over an unseen one.
*Kill condition:* corrections that do not generalise past the form they came from.

**B3. Self-templating for forms that were never enrolled.** Measured **+1.5 (TEST2)** and
**+6.4 (TEST4)** on the geometric path, **+3.1** on top of vision, and **−1.0 on FUNSD** —
it helps on clean digital renders and hurts on noisy scans. Gate it on render quality; do
not wire it in unconditionally.
*Expected:* **+2 to +4** zero-shot. Already implemented, not shipped.

---

## 5. Measurement protocol — non-negotiable

These rules exist because each was learned by getting a number wrong.

1. **`make bench`, never individual scripts.** FUNSD went unmeasured across three changes to
   the reader because it sat outside the commands run by habit.
2. **Quote recall and precision together, or neither.** Precision is trivially 100% for an
   extractor that emits nothing.
3. **`VISION_CACHE=1` for "did my change help"; cold runs for "how good is the model".** The
   letterhead rule looked like it cost TEST4 eight points and against cached readings was
   exactly neutral.
4. **Repeat anything with vision in the loop.** Five runs of identical code scored 42.9,
   42.9, 53.1, 53.1, 53.1 — a 10.2-point spread. Nothing smaller than the spread is
   measurable. Use `--repeat N`, quote median and range.
5. **Restart the backend between arms.** A run against a stale `uvicorn` once compared
   identical code and read as "no change".
6. **Absolute paths.** Four empty results came from a shell left in `backend/`.
7. **Assert the template registry is unchanged.** Seventeen stray derived templates once
   inflated placement 78.9% → 84.2% unnoticed.
8. **10 forms is not 50.** Self-templating measured +3.0 on ten FUNSD forms and −1.0 on all
   fifty.

---

## 6. Gates

| gate | criterion | status |
|---|---|---|
| G0 | corpus ≥100 forms, distinct patient per form, ≥5 fields on ≥90% of forms | **done** — 112 forms / 222 artefacts / 2,775 fields |
| G1 | zero-shot geometry re-baselined on the varied corpus | **done — 62.3%** on 85 forms; **61.2% (1,699 / 2,775)** on 112 |
| G1b | single-patient control | **done** — repeated patient scored −5.4, not +; the drop was the harder forms |
| G2 | hybrid measured on all forms | next |
| G3 | enrolment measured on all 85 (**B1**) | ~~negative~~ **re-measured 2026-09-14 after L1: +4.4 on the 216 artefacts with their own template, ±0 on the rest** (108 of 112 blanks enrolled) |
| G4 | zero-shot ≥80%, precision ≥70% | **revised to ≥70–75% / ≥70%**; 2026-09-14 after L2: **64.6% / 67.8%** (from 61.2 / 58.1) |
| G5 | enrolled ≥90%, precision ≥70% | **85.4% / 70.7%** with one corrected fill per form, measured on a second fill (2026-09-15); precision met, recall five points short; the draw-a-region control exists in review; the hybrid (vision on) is the strongest remaining lever, measured +5.4 on a sample |

**No lever is adopted on one set.** The pattern that keeps repeating is a change that wins
on the set it was developed against and loses elsewhere — rapidocr, self-templating,
`VISION_TABLES`, the letterhead rule. Three sets must agree, or it ships opt-in.

---

## 7. What this plan will not do

* **It will not claim 90% zero-shot.** The published ceiling for GPT-class zero-shot on
  clinical documents is ~74% and FUNSD linking SOTA is 44–59%. If a run here reports 90%
  zero-shot, suspect the benchmark before believing it.
* **It will not use a paid API to get there.** Claude Opus 5 reads our handwriting set at
  91.8% and Sonnet at 89.8%; that is the *ceiling* measurement, not the product.
* **It will not tune against a form.** Once unseen forms arrive, no part of components 1–3
  may need tuning by Claude (user, 2026-09-10). If a fix only works because someone looked
  at one form, it is a bug.

---

## Sources

- **[`RESEARCH_TO_90.md`](RESEARCH_TO_90.md) — 2026-09-14 research pass; supersedes the ranking in §4.**

- [PaddleOCR-VL-1.6 model card](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6)
- [PaddleOCR-VL-1.6 paper, arXiv 2606.03264](https://arxiv.org/html/2606.03264v1)
- [LMDX: Language Model-based Document Information Extraction, arXiv 2309.10952](https://arxiv.org/pdf/2309.10952)
- [KVP10k: dataset for key-value pair extraction, arXiv 2405.00505](https://arxiv.org/pdf/2405.00505)
- [Best OCR for forms — zero-shot clinical figure](https://www.lido.app/blog/best-ocr-for-forms)
- [Open-source OCR/document VLMs compared, 2026](https://www.spheron.network/blog/best-open-source-ocr-vlm-self-host-gpu-cloud-2026/)

## Status, 2026-09-15 evening

Reached on the corrected track: **93.4% recall / 71.8% precision** on the 112-form
benchmark with the slot reader on (90.8 / 70.7 without it), and **88.9 / 64.0 on 21 unseen
forms** collected after every rule was written and kept locked. What got it there, in
order of size: the vision model reading each photograph's taught slots on one numbered
sheet; the form's date as its own vocabulary key; captions cut off both ends of a slot
reading; the blank's printing subtracted on the footprint it was recorded on; one-word
captions cutting a value only after a case change or a gap; whole-word containment; and a
benchmark oracle that teaches slots the way a reviewer places them. Details and every
measurement in `docs/PROGRESS.md`, 2026-09-15.
