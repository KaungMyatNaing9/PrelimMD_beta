# Research: what it would actually take to reach 90% / 70% on the 112-form benchmark

**Asked (user, 2026-09-14):** extra-comprehensive research on reaching **≥90% recall at ≥70%
precision** on the 112-form benchmark using only free, local or free-tier tools and **no frontier
models**; experimental tools welcome; tools that sit *alongside* the OCR and vision we already
have are of special interest; keep in mind everything already tried.

**How this was produced.** Eight capped research agents — three on `sonnet` for the decisive
questions (label→value linking, enrolment, caption-vs-answer tagging and global assignment), one
capped follow-up, and five on `haiku` for surveys (local VLMs, OCR/HTR, datasets, layout
toolkits, validators). Never more than two at a time, each capped in web fetches and words,
each writing its report to disk as it finished. ~600k agent tokens total. Every claim below
that touches our own code was **re-verified against the repo by hand**, and Section 7 lists the
agent claims that did not survive that check.

---

## 1. The finding that reframes the target

Do the arithmetic on our own benchmark before reading any literature.

> 2,775 fields. 1,699 correct (61.2%). 791 misses, 285 wrong values.
> **1,699 + 791 = 2,490 = 89.7%.**

**Fixing every single miss — all 791, perfectly — still lands below 90%.** Reaching 2,498
correct means fixing **74.3% of all 1,076 current failures**, misses *and* wrong values.

That is the honest shape of the ask, and it kills the zero-shot framing outright:

* Published GPT-class **zero-shot** extraction on structured clinical documents: **~74%**.
* True **end-to-end** FUNSD/XFUND relation extraction — entities *and* links predicted together,
  no oracle — **61.5 F1** (TreeForm, [arXiv 2402.05282](https://arxiv.org/abs/2402.05282)).
  We are at 61.2. **We are already at the published end-to-end research ceiling.**
* The 80–90+ FUNSD "RE" numbers quoted everywhere (GeoLayoutLM 89.45) are the **gold-entity
  subtask**: the model is handed correct entity boundaries and only predicts links. Our 791
  misses are overwhelmingly the *segmentation* half those benchmarks never test. `PATH_TO_90.md`
  already warned about this trap once; it is worth restating because two of the eight agents
  walked into it before being pushed.

**Conclusion: ≥90% is not reachable zero-shot, by us or by anyone, on free tools or paid.**
It is reachable **per-template** — enrolment plus accumulated corrections — which is the track
this project already designated and which is currently *broken*, not merely underperforming.
Section 3's L1 is therefore the highest-value work available, and it is a bug-fix, not research.

**Revised, defensible targets:**

| track | target | basis |
|---|---|---|
| zero-shot, 112 forms | **70–75%** recall, **≥70%** precision | L2+L3+L4+L6 below, reasoning in each |
| enrolled + corrected | **≥90%** recall | template worth 19–27 pts (LMDX); few-shot-with-labelling reaches 95%+ |

---

## 2. Where the loss is, restated as budgets

Every lever below is scored against these, not against a vibe.

| failure class | count | max recall pts | notes |
|---|---|---|---|
| misses on stacked / sub-captioned rows | 378 | **13.6** | the single largest addressable block |
| other misses | 413 | 14.9 | |
| wrong: another field's value (mis-association) | 71 | 2.6 | arbitration, not reading |
| wrong: superset (value + neighbouring printed text) | 67 | 2.4 | precision as much as recall |
| wrong: partial | 67 | 2.4 | |
| wrong: OCR misread | 71 | 2.6 | |
| wrong: furniture | 9 | 0.3 | |
| `Date` caught the wrong date | 55 (subset of wrong) | 2.0 | one constraint may fix most of it |
| **value not legible on the page at all** | **36** | — | **unrecoverable by any reader** |

**~94% of all 1,076 failures had the wanted value plainly legible.** This is an association and
arbitration problem. Two independent research threads reached that conclusion from opposite
directions, and it is the reason OCR work is ranked last.

---

## 3. The levers, ranked

### L1 — Make enrolment non-destructive. *Highest value; it is a bug-fix, not research.*

**What it is.** Three verified defects in the enrolled read path, each independently sufficient
to explain a negative result. All three confirmed by hand against the repo:

1. **Registration is dead code.** `templates.align_to_template`
   ([templates.py:160](../backend/app/services/templates.py#L160)) computes exactly the
   homography the template read needs. Repo-wide grep: it is referenced **nowhere** except its
   own definition and a docstring in `_read_with_template` saying it is deliberately not
   reached for. The template read compares raw page fractions, so any margin, DPI, skew or
   PDF-render-vs-JPG-scan difference shifts every field with no correction.
2. **Page 0 is hardcoded.** [form_analyzer.py:1567](../backend/app/services/form_analyzer.py#L1567)
   is `slots = definition.pages[0].fields`. These are packets — a welcome letter, a HIPAA
   notice and a financial policy commonly sit *ahead of* the registration page. This is also
   why 36 of 85 blanks were "not derivable".
3. **The template outranks a correct zero-shot value.** `_merge_template`
   ([form_analyzer.py:1504](../backend/app/services/form_analyzer.py#L1504)) keeps an ad-hoc
   value only where no template slot at that position answered *anything*. A template misread
   therefore silently replaces a correct free-form hit. The docstring records an earlier fix
   from blocking on slot *existence* to slot *answered*; the same argument applies one step
   further, to slot *answered correctly* — which cannot be known, so the safe rule is that the
   template may only ever **add**.

**License / size / M1.** None. No new dependency.

**Failure class.** All of them, on the 100 artefacts that have their own template.

**Expected gain, with reasoning.** Measured today: own-template artefacts 64.9 → 61.1 (**−3.9**);
artefacts with no template exactly ±0. Making the merge additive puts a **hard floor at the
zero-shot score by construction** — a strategy that only adds cannot score below one that does
not add. So −3.9 → ≥0 is not an estimate, it is an invariant. Registration and page-matching
then supply the actual upside, and bring some of the 36 non-derivable blanks into play.
Corpus-wide the enrolled arm scored 60.0 against 62.3 zero-shot on 85 forms: **≈2.3 points are
recoverable to parity before any new capability is added at all.**

**Risk.** Low. The risk is that after all three fixes enrolment is merely neutral — which would
mean the derived template geometry itself is wrong (it is built from `geometric_fields`, the
boxed-only reader, while most intake forms are read by `inline`), and that is the next thing to
open.

**Minimal first experiment.** Fix the merge rule alone — one condition — and re-run
`scripts/evaluate_enrolled.py` over the same 85 forms against
`data/real-forms/LARGE/ground_truth_new.json`. Expect the own-template subset to move from 61.1
to **≥64.9**. Then add registration, then page fingerprinting, scoring each separately. Open
`birminghamheart_com__Patient-form_FILLED.pdf` (14 fields → 1) first; it is the extreme case and
will show which of the three dominates.

**Kill condition.** If the merge fix alone does not restore the own-template subset to ≥64.9,
the loss is not in merge authority and the hypothesis ranking in
`scratchpad/research/B_enrolment.md` should be worked down in order.

---

### L2 — Replace winner-takes-all with a global assignment. *Best precision lever; free.*

**What it is.** Today `_best_layout` picks between the `boxed` and `inline` readers by **counting
answered fields**. That is a cardinality objective, and it is why a reader that binds captions as
answers wins 21-to-11 on a page where the other reader was right — reproducibly, same page, same
boxes. The project's own note is *"fix the score, not the detectors."*

Replace it with a **quality-weighted assignment** over candidates from **both** readers at once:
canonical fields on one side, candidate value spans on the other; a span may serve at most one
field, a field takes at most one span; each field contributes **at most one term** to the
objective however many candidates it attracted. A reader can then only win by proposing *better*
edges, never *more* of them. This is precisely why DETR uses Hungarian matching for set
prediction — to stop multiple predictions greedily claiming the same target
([DETR](https://huggingface.co/docs/transformers/main/model_doc/detr)).

Cost terms: geometry (distance, direction, whether a ruled line intervenes, alignment to the
answer space), caption-to-alias text similarity, type validity **as a soft cost**, source
agreement (both readers proposed this span), and cross-field consistency (L6).

**License / size / M1.** `scipy.optimize.linear_sum_assignment` (**BSD-3**) — already a
dependency, microseconds at this scale. Escalate to **OR-Tools CP-SAT (Apache-2.0)** only if
mutual-exclusion and consistency constraints need to be *hard*; at ~30 fields × ~200 spans the
instance is trivial for CP-SAT. Start with Hungarian.

**Failure class.** 71 mis-association wrongs directly; 67 superset + 9 furniture via the soft
type/SER terms; and the misses on pages where the wrong reader won the whole page.

**Expected gain, with reasoning.** Precision is the primary win and the one the target most
needs: at ~50%, half of what a reviewer sees is the form's own printed text, and a cardinality
objective actively *rewards* emitting it. Removing that reward plus soft type costs should reach
**65–72% precision**. Recall gain is smaller and less certain — **+2 to +6** — and comes from
pages where winner-takes-all discarded the better reader wholesale. Note this lever is *not*
"merge the two readers", which was tried and was worse: merging takes the union and keeps both
readers' errors; assignment forces them to compete edge-by-edge under a constraint that only one
can win each field.

**Risk.** Weights must be hand-set — 112 forms is too few to learn them without overfitting, and
this project's recurring failure mode is exactly a change tuned on one set that loses on another.
Hand-set the weights, and hold TEST2/TEST4/TEST5 out.

**Minimal first experiment.** Emit both readers' candidates for all 222 artefacts without
choosing, score the Hungarian assignment against `ground_truth_new.json`, and compare with the
winner-takes-all baseline on **both** recall and precision. Then report how many pages changed
their chosen reader and whether those flips were right.

**Kill condition.** If after a few rounds of hand-weighting it cannot beat winner-takes-all on
both recall and precision simultaneously, the cost *features* are wrong, not the solver — stop
and fix features before touching the objective again.

---

### L3 — LMDX-style grounded re-linker over orphan spans. *The one genuinely new capability. Gain unknown — measure it.*

**What it is.** Serialise OCR words with quantised coordinate tokens and ask a **local** text LLM
to link captions to values, then **ground every emitted value back to a real OCR span**. From
LMDX ([arXiv 2309.10952](https://arxiv.org/pdf/2309.10952)), fetched and read for mechanics:

* Each segment's **centre** is normalised and quantised into **100 buckets per axis**, appended
  as `XX|YY`, e.g. `Print Date 02/28/20 14:21:20 75|02`. That pair doubles as the segment's ID.
* The model emits JSON: entity text **plus the coordinate-token ID of its source segment**;
  missing entities are `null`.
* Grounding looks the segment up by ID and checks the emitted text is an **exact substring** of
  that segment's real OCR text. In the paper's own words, values with segments that fail to
  ground **"are discarded."**

**That last property is why this lever is admissible here at all.** It is a hard filter, not a
soft flag: an ungrounded answer becomes a **miss**, never a **wrong value**, and coordinates come
from our own OCR rather than from the model. This project abandoned vision tables (−9.5
precision) and vision checkboxes because models invented content, and found that anti-invention
*prompt wording* had no effect. Grounding is a mechanism, not a request.

Run it **only over spans the existing readers left orphaned or ambiguous** — not as a whole-form
replacement. That keeps cost down and keeps the existing readers authoritative where they fire.

**License / size / M1.** **Qwen2.5-7B-Instruct is Apache-2.0** (note: Qwen's licence varies by
size — the 3B and 72B are *not* Apache-2.0; verify per checkpoint). ~4.5 GB at 4-bit, comfortable
on 16 GB. Constrained decoding is available and confirmed on Apple silicon three ways: llama.cpp
**GBNF** grammars (backend-agnostic, works under Metal), **Outlines** via its documented `mlx-lm`
integration, and **XGrammar** (`pip install "xgrammar[metal]"`). Llama 3.x and Gemma 3 carry
custom non-Apache licences and are **out** on licence grounds regardless of quality.

**Failure class.** The 378 stacked / sub-captioned misses (13.6 pts) — the largest single block,
and precisely the shape a coordinate-aware reader can resolve and a nearest-label-above/left
heuristic cannot.

**Expected gain, with reasoning.** **Honestly unknown, and that is the finding.** LMDX's headline
used PaLM-2 / Gemini-class models. A dedicated follow-up search found **no published KIE number
for any open 7–14B model in a layout-prompted, grounded setting** — the closest architecturally
similar result (F1 80.9 CORD / 83.9 SROIE) could not be confirmed to use an open model, so it is
not usable even as a proxy. Anyone quoting a number here is guessing. What *is* known: the
mechanism is safe by construction, the target block is 13.6 points, and the experiment is cheap.
Treat the first run as the measurement, not as a deployment.

**Risk.** Two real ones. (a) Recall depends on the model phrasing values exactly as OCR read
them; near-misses ground-fail and become misses. (b) Non-determinism — see Section 6; measure
with repeats and quote median and range, or the result is noise.

**Minimal first experiment.** Take the 154 misses on `State`, `City` and `Zip` — one tight,
stacked-layout-heavy block. Serialise each page's words with coordinate tokens, ask Qwen2.5-7B
for those three fields, ground every answer, score against `ground_truth_new.json`, and run it
**three times** to get a spread. Report recovered fields and any precision change.

**Kill condition.** Recovers <15% of that block, **or** costs any precision (grounding should make
that impossible — if precision drops, the grounding is not implemented correctly and must be
fixed before the lever is judged).

---

### L4 — Read the PDF's real vector geometry and font cues instead of morphology on a render.

**What it is.** 1,526 of our 2,775 fields come from PDFs with a genuine text layer, and **567 of
the 568 PDF failures had the value legible**. Yet the `boxed` reader finds rules and boxes by
running **morphology on a rasterised render** even when the PDF's drawing operators state the
lines exactly. `pdfplumber` exposes `.lines`, `.rects`, `.edges`, `.curves` directly. Separately,
its `chars` carry `fontname`, `size` and `non_stroking_color` — and a typed fill is very often a
different font, size or colour from the pre-printed caption, which is a **free caption-vs-answer
signal on more than half the corpus**, feeding straight into L2's cost table.

**License / size / M1.** `pdfplumber` **MIT**, `pdfminer.six` MIT, `pypdfium2` **Apache-2.0/BSD-3**.
Pure-Python or prebuilt arm64 wheels; no model, no GPU.

**Failure class.** Superset (67) and furniture (9) wrongs via font cues; answer-space detection
for part of the miss block.

**Expected gain, with reasoning.** Mostly **precision**, concentrated on the 55% of fields that
are PDF-sourced. Recall **+1 to +3**. Modest, but nearly free and it removes a whole class of
raster-approximation error.

**Risk — and an honest limit.** Vector extraction **misses** several cases that matter on intake
forms specifically: underscore runs typed as *text* rather than drawn as vectors (very common
here), rules drawn as thin *filled* rectangles rather than strokes, and dotted rules drawn as
many tiny segments. **Vector geometry cannot replace morphology; it augments it.** Anyone
planning to delete the morphology path should not.

**Minimal first experiment.** On the PDF half only, measure how often caption and value differ in
`fontname`/`size`/colour across the 76 superset+furniture wrong values. If the separation is
clean, wire it in as an L2 cost term; if most forms are single-font, drop it.

**Kill condition.** Caption and value share font, size and colour on most sampled forms.

---

### L5 — Enrol from AcroForm widgets where they exist.

**What it is.** 23 of 86 forms carry AcroForm widgets. Widget **rectangles** are the answer-space
geometry we are otherwise trying to infer from pixels, and the designer's **field names** are
caption evidence. `pypdf` (**BSD-3**) exposes both. This is close to free ground truth for
exactly the geometry L1's templates get wrong.

**License / size / M1.** `pypdf` BSD-3, pure Python, negligible.

**Failure class.** Template geometry on 27% of forms; feeds L1 directly.

**Expected gain, with reasoning.** Large *on those forms*, modest corpus-wide — roughly a quarter
of the enrolled-track gain, arriving with near-zero effort and no new failure mode.

**Risk.** A filled copy that was printed, signed and rescanned no longer shares the blank's
coordinate space, so widget rects must be gated on the filled artefact still being a digital PDF.

**Minimal first experiment.** Build templates from widget rects for those 23 forms, compare
against `derive_template`'s output on the same forms, score both with `evaluate_enrolled.py`.

**Kill condition.** Widget rects do not beat derived geometry on forms that have both.

---

### L6 — Soft validators and cross-field consistency, inside L2's objective.

**What it is.** Type and consistency evidence as **graded costs in the assignment**, never as hard
filters. The distinction is load-bearing: per-field **hard** type-validity arbitration was already
tried here and gave **no gain**, because a hard filter must be right every time, while a soft cost
can be outvoted by geometry and source agreement.

The highest-value single constraint: **DOB < form Date**. `Date` is our most-wrong label (55
wrong, 2.0 pts) and the cause is an ambiguous caption catching the wrong date — exactly what an
ordering constraint resolves.

**License / size / M1.** `phonenumbers` **Apache-2.0**; `dateparser` **BSD-3**; `usaddress` and
`probablepeople` **MIT**; `email-validator` public domain. Offline data: **US Census surname and
given-name frequency lists** (public domain, ~20 MB, committable); **SimpleMaps US ZIPs** (free
with attribution, ~50 MB, committable) for ZIP↔city↔state. **NPPES NPI registry** is public
domain but **1.1 GB** — fetch at setup, never commit. **Avoid `nameparser` and `python-stdnum`
(both LGPL-2.1+)** — usable in principle but they add copyleft compliance obligations to an
Apache-2.0 release for very little benefit here.

**Failure class.** 55 wrong `Date` values; ZIP/City/State mutual errors; name-field confusion.

**Expected gain, with reasoning.** The DOB<Date constraint plausibly reaches **~1.0–1.5 of the 2.0
`Date` points**. Address consistency adds a few tenths. Small individually — worth doing because
it is nearly free and because these are the cost terms that make L2 work at all.

**Risk.** False alarms. ZIP↔city is ~95% clean; area-code↔state ~90% (number portability, VOIP);
name-vs-combined-name matching is genuinely noisy and should be weighted weakly or skipped.

**Minimal first experiment.** Add DOB<Date as a single soft term and score the 55 wrong `Date`
values before and after.

**Kill condition.** No movement on the `Date` block — which, given the hard-filter precedent,
would mean the signal really is uninformative rather than merely mis-applied.

---

### L7 — Replace PyMuPDF. *Not a recall lever — a release blocker.*

PyMuPDF is **AGPL-3.0** and the project must ship Apache-2.0. Our actual call sites are narrower
than a generic capability list suggests:

| what we use it for | where | Apache-compatible replacement |
|---|---|---|
| words with boxes | `form_analyzer` | `pdfplumber` (MIT) / `pypdfium2` (Apache-2.0/BSD-3) |
| page → image at DPI | `form_analyzer:1281-1285` | `pypdfium2` |
| text width metrics (`get_text_length`) | `regeneration`, `fill_forms`, `template_builder` | `reportlab.pdfbase.pdfmetrics.stringWidth` (BSD-3) |
| compose page: image + text | `regeneration.py:126-161` | `reportlab` — `drawImage` + `drawString` |
| AcroForm `widgets()` / `bake()` | `fill_forms` | `pypdf` (BSD-3) |
| `search_for` substring position | `fill_forms` (benchmark filler only) | word boxes from `pdfplumber` |

One survey reported "no Apache-2.0 library can write text into a PDF" as a blocker. **That is not
our case.** `regeneration.py` does not edit an existing PDF's form fields — it opens a *new*
document, `insert_image`s the blank as a background and draws text over it. That is exactly
`reportlab`'s native mode. **There is no missing capability.**

Also flagged: **Marker and Surya are GPL-3.0** — disqualifying, same reason as PyMuPDF. Reports
conflicted on Surya's weights (GPL-3.0 vs OpenRAIL-M); either way it is not Apache-2.0-clean.

---

### Not recommended: more OCR work.

Ranked last on our own numbers. Perfect OCR would fix the **71 misreads (2.6 pts)** plus the
**29 JPG near-misses (1.0 pt)** — a **≈3.6-point ceiling** — and the **36 fields whose value is
not on the page at all** are beyond any reader. One survey put this at "5–8%" by treating 71/1076
(6.6% *of failures*) as 6.6 points *of recall*; against 2,775 fields it is 2.6. The corrected
number makes the survey's own conclusion stronger, not weaker: **focus downstream.**

Worth noting cheaply if an engine is ever swapped: **Apple Vision** (`VNRecognizeTextRequest`) is
free, on-device, pre-installed on the M1, returns per-word boxes with confidences, and carries
**no licence risk at all** — reachable from Python via `pyobjc` or `ocrmac`. Also flagged:
PaddleOCR's dependency chain can pull in **PyMuPDF (AGPL)**; check before adopting.

---

## 4. What this means for the two tracks

**Zero-shot: 61.2 → 70–75%, precision → ~70%.** L2 (+2 to +6 recall, precision 50→65-72), L3
(unknown, 13.6-point block in range), L4 (+1 to +3), L6 (+1 to +1.5). These are additive only in
part — L4 and L6 largely *feed* L2 rather than stacking on it. **90% zero-shot is not on this
list and should not be promised**, per Section 1.

**Enrolled: the only credible route to ≥90%.** L1 restores it from negative to a guaranteed floor
and recovers ≈2.3 points to parity; L5 supplies exact geometry on 27% of forms; then per-template
corrections supply the rest. Azure Document Intelligence's custom template models require **~5
labelled samples**; VRDU's few-shot track is built on **10/50/100/200 documents per template**.
Our review UI already captures corrections (`PATCH /api/v1/form-analyses/{id}/fields/{id}`), so
**the second, third and fifth examples are free** — a receptionist fixing a form teaches its
template. That is the mechanism that reaches 90%, and it is a product loop, not a model.

---

## 5. Dead ends — checked and rejected, with the reason

* **LayoutLMv3** — weights **CC-BY-NC-SA-4.0**. Disqualifying regardless of its ~90 F1 SER.
  Two separate agents recommended it anyway; do not.
* **Treating FUNSD gold-entity RE numbers as evidence** — Section 1. The end-to-end number is
  61.5, not 89.45.
* **A hard caption/answer pre-filter from an SER tagger.** Worked through arithmetically: at ~90%
  tagger accuracy a hard filter deletes up to ~170 true answers (recall 61.2 → ~55) to remove at
  most ~143 wrong values. **Net-negative.** Use the tag as a *soft cost* in L2 instead, where a
  mis-tagged true answer can still win on geometry.
* **Llama 3.x, Gemma 3** — custom non-Apache licences. (A claim that "Gemma 4 is Apache-2.0"
  surfaced from a secondary source twice and was **not** confirmed primarily; treat as unverified.)
* **Qwen2.5-VL-72B** — recommended by one survey for a 16 GB M1 at "20–24 GB at 4-bit". It does
  not fit. Rejected on arithmetic.
* **SuperPoint+LightGlue** for registration — SuperPoint's original weights are **non-commercial**.
  LightGlue is Apache-2.0 but its default keypoint source is not.
* **FUNSD as a committable fixture** — **non-commercial only** (it derives from the restricted
  Tobacco/RVL-CDIP corpus). It may stay a local regression set; it may **not** ship in an
  Apache-2.0 repo.
* **Self-consistency voting for extraction** — published gains are on *reasoning* tasks; no
  measured gain for KIE.
* **Constrained JSON decoding as an accuracy lever** — evidence shows it can *cost* semantic
  accuracy on capable models. Use it for *format* safety only; **grounding**, not grammar, is what
  prevents invention (L3).
* **HTR (TrOCR, PyLaia)** — our corpus is machine-filled; HTR buys nothing on this benchmark. It
  matters for real clinic handwriting, which is a different measurement.
* **LOINC / FHIR SDC / USCDI / HL7 alignment** — not useful as automatic alignment targets for a
  form reader; useful as export targets and checklists. Standards-theatre if hardcoded.
* **Synthetic training corpora** — credible for *pre-training* only, never as a benchmark. This
  project already deleted its synthetic corpus for the right reason.

---

## 6. Two measurement traps that will corrupt any of the above

**The 10.2-point vision spread is probably not floating-point noise.** Five identical runs scored
**42.9 / 42.9 / 53.1 / 53.1 / 53.1** — *two discrete values*, not a scatter. Continuous FP drift
does not produce a clean bimodal split; a **discrete branch flipping** does. Given `_best_layout`
is known to flip strategy on close counts (21 vs 11), the most likely mechanism is the *layout
choice* flipping between runs, not the model's arithmetic. One survey attributed it to FP
non-associativity and "distributed parallelism", which does not apply to single-machine Ollama.
**This is testable and should be tested before any vision measurement is trusted**: log the chosen
layout per page per run and check whether the score tracks the strategy flip. If it does, L2 fixes
the measurement noise as a side effect.

**`qwen3-vl` returning empty output is a known Ollama template bug**, not a model defect
([ollama#14798](https://github.com/ollama/ollama/issues/14798)): the template omits the
thinking-control logic, so the whole token budget is consumed by thinking with nothing left for
content. Documented workarounds: `"raw": true`, appending `/no_think`, or prefilling an empty
thinking block. Worth one retry before writing the model off.

---

## 7. Agent claims that did not survive verification

Recorded so the reports in the scratchpad are read with the right amount of trust.

| claim | status |
|---|---|
| `align_to_template` is never called; page 0 hardcoded; template outranks ad-hoc | **confirmed** by hand against the repo |
| "No Apache-2.0 library can write text to PDF" — a blocker | **wrong for us**; `regeneration` composes a new page, `reportlab` covers it |
| OCR ceiling "5–8% recall" | **wrong**; conflated % of failures with recall points. True ceiling ≈3.6 pts |
| Qwen2.5-VL-72B viable on 16 GB M1 | **wrong**; 20–24 GB at 4-bit does not fit |
| LayoutLMv3 recommended for retrieval/tagging | **wrong**; CC-BY-NC-SA-4.0. Use DiT for layout retrieval |
| "All validators are boolean-only" | **overstated**; `usaddress`/`probablepeople` are CRF-based and python-crfsuite exposes marginal probabilities — a graded score is obtainable. Verify before relying on it |
| 10.2-pt spread = floating-point drift | **unconvincing**; bimodal pattern indicates a discrete branch. See Section 6 |
| Florence-2 licence "unverified, disqualify" | **unresolved**; believed MIT. Not load-bearing — not recommended anyway |
| SROIE/DocILE "Apache/MIT compatible" | **unverified**; ICDAR competition data usually is not. Confirm before committing fixtures |

---

## 8. Suggested order of work

1. **L1 merge-authority fix** — one condition, invariant-backed, restores ≈2.3 points. Do it first.
2. **L2 global assignment** — the precision target lives here, and it also fixes the measurement
   noise in Section 6.
3. **L5 AcroForm widgets** — nearly free, feeds L1.
4. **L1 registration + page fingerprinting** — unlocks the 36 non-derivable blanks.
5. **L4 font cues** — feeds L2's cost table.
6. **L6 DOB<Date** — one soft term, ~1–1.5 points.
7. **L3 grounded re-linker** — the experiment that measures the unknown. Last, because it is the
   only one whose payoff nobody can currently predict.
8. **L7 PyMuPDF replacement** — schedule independently; it is a licence blocker, not a score lever.

Per the project's own protocol: `make bench`, never individual scripts; quote recall **and**
precision together; repeat anything with vision in the loop and quote median and range; no lever
is adopted on one set.

---

## Sources

Full reports, with every URL each agent gathered, are in the session scratchpad:
`A_linking.md`, `A2_lmdx_mechanics.md`, `B_enrolment.md`, `C_tagging_assignment.md`,
`D_local_vlms.md`, `E_ocr_htr.md`, `F_datasets_fewshot.md`, `G_layout_toolkits.md`,
`H_validators.md`.

Primary references relied on above:
- [LMDX, arXiv 2309.10952](https://arxiv.org/pdf/2309.10952) — coordinate tokens, grounding
- [TreeForm, arXiv 2402.05282](https://arxiv.org/abs/2402.05282) — end-to-end FUNSD/XFUND 61.5
- [GeoLayoutLM, arXiv 2304.10759](https://arxiv.org/abs/2304.10759) — Apache-2.0, gold-entity RE 89.45
- [BROS](https://github.com/clovaai/bros) (Apache-2.0), [LiLT](https://github.com/jpWang/LiLT) (MIT)
- [VRDU, arXiv 2211.15421](https://arxiv.org/pdf/2211.15421) — few-shot per template
- [KVP10k, arXiv 2405.00505](https://arxiv.org/pdf/2405.00505)
- [DETR set prediction](https://huggingface.co/docs/transformers/main/model_doc/detr) — Hungarian matching precedent
- [pdfplumber](https://github.com/jsvine/pdfplumber) (MIT), [pypdf](https://pypi.org/project/pypdf/) (BSD-3), [pypdfium2](https://pypi.org/project/pypdfium2/) (Apache-2.0/BSD-3)
- [ollama#14798](https://github.com/ollama/ollama/issues/14798) — qwen3-vl empty output
- [US Census name files](https://www.census.gov/data/developers/data-sets/surnames.html) (public domain), [SimpleMaps US ZIPs](https://simplemaps.com/data/us-zips), [NPPES](https://download.cms.gov/nppes/NPI_Files.html)
