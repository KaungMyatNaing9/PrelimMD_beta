"""The benchmark this project reports, in one run, with the registry checked.

The benchmark is `data/real-forms/LARGE/`: 112 real clinic forms, 222 artefacts, 2,775
fields, a distinct invented patient per form (user, 2026-09-14: "the 100+ real forms ...
should be the main"). By default this runs its zero-shot arm and its enrolled arm and
nothing else. FUNSD, the small TEST sets, the dev forms, intake precision, placement and
round-trip run only with `--extras`, and only when the user asks for them (user,
2026-09-15: "Don't test FUNSD anymore unless I tell you to, only the 100+ forms").

Two things it enforces that ad-hoc runs do not:

- **The template registry is identical at the end.** A derived template left behind is
  matched against every later upload; seventeen strays once inflated placement 78.9% ->
  84.2% with nobody noticing.
- **Recall and precision are printed together.** Precision is trivially 100% for an
  extractor that emits nothing, and recall is trivially high for one that emits everything,
  so either alone can be improved by making the product worse.

    python scripts/benchmark_all.py [--api ...] [--quick] [--extras] [--keep DIR]

`--quick` runs the zero-shot arm only (~10 minutes); the enrolled arm takes ~45 minutes.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


KEEP: Path | None = None


def run(script: str, *args: str) -> str:
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), *args],
        capture_output=True, text=True, check=False,
    )
    text = out.stdout + out.stderr
    if KEEP:
        # The summary table keeps one number per benchmark; the per-form lines are what a
        # regression is diagnosed from, and re-running a 45-minute arm to see them again
        # is the alternative.
        tag = "_".join(a.replace("/", "-") for a in args if not a.startswith("http"))
        (KEEP / f"{Path(script).stem}_{tag}.txt".replace("_--", "_")).write_text(text)
    return text


def find(text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    return match.group(1) if match else "--"


def templates(api: str) -> list[str] | None:
    """The registered template ids, or None when the API did not answer.

    An empty registry is the normal state since the synthetic corpus went (2026-09-13);
    treating "no templates" as "no API" refused to run a single benchmark.
    """
    out = subprocess.run(["curl", "-s", "--max-time", "20", f"{api}/api/v1/health"],
                         capture_output=True, text=True, check=False).stdout
    try:
        return sorted(json.loads(out).get("templates", []))
    except json.JSONDecodeError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--quick", action="store_true", help="the zero-shot LARGE arm only")
    parser.add_argument("--extras", action="store_true",
                        help="also the dev forms, TEST2/4/5, intake precision, FUNSD, "
                        "placement and round-trip -- only when the user asks for them")
    parser.add_argument("--keep", default=None,
                        help="directory to keep every benchmark's full output in")
    parser.add_argument("--parallel", type=int, default=4,
                        help="artefacts scored at once; identical results, a third of the time")
    args = parser.parse_args()
    workers = ["--parallel", str(args.parallel)] if args.parallel > 1 else []
    global KEEP
    if args.keep:
        KEEP = Path(args.keep)
        KEEP.mkdir(parents=True, exist_ok=True)

    before = templates(args.api)
    if before is None:
        print(f"No API at {args.api} -- start the backend first.")
        return 1
    rows: list[tuple[str, str, str]] = []

    print("LARGE zero-shot (the benchmark, ~10 minutes)", flush=True)
    large = run("evaluate_real_forms.py", "--api", args.api,
                "--truth", "LARGE/ground_truth_new.json", *workers)
    rows.append(("LARGE zero-shot", find(large, r"ACCURACY .*= ([\d.]+%)"), "recall"))
    rows.append(("LARGE precision", find(large, r"PRECISION .*= ([\d.]+%)"), "precision"))

    if not args.quick:
        print("LARGE enrolled (learn every blank, read through it, release; ~45 minutes)",
              flush=True)
        enrolled = run("evaluate_enrolled.py", "--api", args.api, "--set", "LARGE", *workers)
        rows.append(("LARGE enrolled", find(enrolled, r"ACCURACY .*= ([\d.]+%)"), "recall"))
        rows.append(("LARGE enrolled prec.", find(enrolled, r"PRECISION .*= ([\d.]+%)"),
                     "precision"))

    if args.extras:
        print("dev forms (regression check only -- tuned against, not evidence)", flush=True)
        rows.append(("dev forms", find(run("evaluate_real_forms.py", "--api", args.api, *workers),
                                       r"ACCURACY .*= ([\d.]+%)"), "recall"))
        for name in ("TEST2", "TEST4", "TEST5"):
            print(f"{name} recall", flush=True)
            rows.append((f"{name} zero-shot",
                         find(run("evaluate_real_forms.py", "--api", args.api,
                                  "--truth", f"{name}/ground_truth_new.json", *workers),
                              r"ACCURACY .*= ([\d.]+%)"), "recall"))
        print("intake precision", flush=True)
        precision = run("intake_field_coverage.py", "--api", args.api)
        rows.append(("intake precision", find(precision, r"= ([\d.]+%) precision"), "precision"))
        print("FUNSD linking (only when asked)", flush=True)
        funsd = run("evaluate_funsd.py", "--api", args.api)
        rows.append(("FUNSD linking", find(funsd, r"= ([\d.]+%) of annotated"), "recall"))
        print("placement", flush=True)
        rows.append(("placement <=2mm", find(run("evaluate_placement.py", "--api", args.api),
                                             r"within 2.0mm = ([\d.]+%)"), "fidelity"))
        print("round-trip (~4 minutes, silent until it finishes)", flush=True)
        # Anchored to the total line: every form also prints "N/M survived = X%", and an
        # unanchored match picks up the first form instead of the run.
        rows.append(("round-trip", find(run("evaluate_roundtrip.py", "--api", args.api),
                                        r"ROUND-TRIP .*survived = ([\d.]+%)"), "fidelity"))

    print(f"\n{'benchmark':22} {'result':>9}   kind")
    for name, value, kind in rows:
        print(f"{name:22} {value:>9}   {kind}")

    after = templates(args.api)
    print(f"\ntemplates before {before}\ntemplates after  {after}")
    if after != before:
        print("!! registry changed -- a benchmark leaked a template, every later run is suspect")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
