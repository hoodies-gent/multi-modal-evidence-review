"""Evaluation entry point.

Usage (run from the `code/` directory):

  # Score predictions against ground truth (and validate schema):
  python -m evaluation.main --pred preds.csv --truth ../dataset/sample_claims.csv

  # Validate a label-less output.csv (schema/contract only, e.g. before submission):
  python -m evaluation.main --pred ../output.csv --validate-only

Reports go to stdout; --json / --md write machine- and human-readable copies.

The framework is decoupled from the model: it consumes a predictions CSV, it does
not run the VLM. To compare strategies, score multiple pred files against one truth.
"""

from __future__ import annotations

import argparse
import os
import sys

# Make `schema` (code/schema.py) and the `evaluation` package importable whether
# this file is run as a module (-m evaluation.main) or as a script.
_CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CODE_ROOT not in sys.path:
    sys.path.insert(0, _CODE_ROOT)

from evaluation import loader, report, scorers, validators  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Evaluate / validate claim-verification predictions.")
    p.add_argument("--pred", required=True, help="predictions CSV")
    p.add_argument("--truth", help="ground-truth CSV (e.g. dataset/sample_claims.csv)")
    p.add_argument("--validate-only", action="store_true", help="schema/contract validation only")
    p.add_argument("--json", help="write the machine-readable report here")
    p.add_argument("--tag", help="config label embedded in the report (e.g. 'gemini-2.5-flash·v2')")
    p.add_argument("--md", help="write the markdown report here")
    args = p.parse_args(argv)

    pred_fields, pred_rows = loader.read_csv(args.pred)

    # Always validate the predictions against the schema.
    issues = validators.validate(pred_fields, pred_rows)

    if args.validate_only or not args.truth:
        print(f"Validation: {len(issues)} issue(s) over {len(pred_rows)} rows.")
        for i in issues[:50]:
            print(f"  - [{i.key}] {i.column}: {i.message}")
        if len(issues) > 50:
            print(f"  ... and {len(issues) - 50} more")
        return 1 if issues else 0

    # Score against truth.
    _, truth_rows = loader.read_csv(args.truth)
    alignment = loader.align(pred_rows, truth_rows)
    score_result = scorers.score_all(alignment.pairs)
    rep = report.build_report(score_result, issues, alignment)
    if args.tag:
        rep["tag"] = args.tag
    md = report.to_markdown(rep, score_result)

    print(md)
    if args.json:
        report.write_json(rep, args.json)
        print(f"\n[wrote JSON report -> {args.json}]")
    if args.md:
        with open(args.md, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"[wrote markdown report -> {args.md}]")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
