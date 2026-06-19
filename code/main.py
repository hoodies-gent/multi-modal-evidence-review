"""System entry point: run the claim-verification pipeline over an input CSV and
write predictions to output.csv.

Usage (from the code/ directory or anywhere):

  # MVP smoke run on the labeled sample, using the offline stub client:
  python main.py --input ../dataset/sample_claims.csv --output ../preds_sample.csv --client stub

  # Real run on the Gemini VLM (pass an exact current gemini model id):
  python main.py --input ../dataset/claims.csv --output ../output.csv --client gemini --model-id <gemini-model-id>

Writes <output>.run.json with run metadata (model, prompt version, call/cache
counts, timing, git commit) for experiment traceability and the operational report.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import time

import schema
from cache import Cache
from prompts import PROMPT_VERSION
from pipeline import process_row, conservative_row
from vlm_client import get_client

_CODE_ROOT = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_CODE_ROOT)
_DATASET_DIR = os.path.join(_REPO_ROOT, "dataset")

# Load secrets from a repo-root .env (gitignored) into os.environ, so the Gemini
# client picks up GEMINI_API_KEY/GOOGLE_API_KEY. No-op when python-dotenv is absent
# or no .env exists (e.g. the evaluator sets env vars directly).
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_REPO_ROOT, ".env"))
except Exception:
    pass


def _read_csv(path: str) -> list:
    with open(path, newline="", encoding="utf-8") as f:
        return [{k: (v or "") for k, v in r.items()} for r in csv.DictReader(f)]


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=_REPO_ROOT, stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return "unknown"


def _index_history(rows: list) -> dict:
    return {r["user_id"]: r for r in rows}


def _index_evidence(rows: list) -> dict:
    by_obj = {obj: [] for obj in schema.CLAIM_OBJECTS}
    for r in rows:
        obj = (r.get("claim_object") or "").strip().lower()
        if obj == "all":
            for k in by_obj:
                by_obj[k].append(r)
        elif obj in by_obj:
            by_obj[obj].append(r)
    return by_obj


def _abs_image_paths(image_paths: str) -> list:
    out = []
    for p in (image_paths or "").split(";"):
        p = p.strip()
        if p:
            out.append(os.path.join(_DATASET_DIR, p))
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Run claim-verification pipeline -> output.csv")
    p.add_argument("--input", default=os.path.join(_DATASET_DIR, "claims.csv"))
    p.add_argument("--output", default=os.path.join(_REPO_ROOT, "output.csv"))
    p.add_argument("--client", default="gemini", choices=["stub", "gemini"])
    p.add_argument("--model-id", default="gemini-3.5-flash", help="exact model id for the gemini client")
    p.add_argument("--min-interval", type=float, default=0.5,
                   help="min seconds between gemini calls (paid ~0.5; free tier needs ~13)")
    p.add_argument("--limit", type=int, default=None, help="process only the first N rows")
    p.add_argument("--no-cache", action="store_true")
    args = p.parse_args(argv)

    rows = _read_csv(args.input)
    if args.limit:
        rows = rows[: args.limit]
    history = _index_history(_read_csv(os.path.join(_DATASET_DIR, "user_history.csv")))
    evidence = _index_evidence(_read_csv(os.path.join(_DATASET_DIR, "evidence_requirements.csv")))

    client = get_client(args.client, args.model_id, min_interval_s=args.min_interval)
    cache = Cache(enabled=not args.no_cache)

    totals = {"rows": 0, "cache_hits": 0, "api_calls": 0, "parse_failures": 0, "errors": 0}
    out_rows = []
    t0 = time.time()
    for row in rows:
        ev = evidence.get((row.get("claim_object") or "").strip().lower(), [])
        totals["rows"] += 1
        try:
            out_row, stats = process_row(
                row, history.get(row.get("user_id")), ev, client, cache,
                _abs_image_paths(row.get("image_paths", "")),
            )
            totals["cache_hits"] += int(stats["cache_hit"])
            totals["api_calls"] += stats["api_calls"]
            totals["parse_failures"] += int(not stats["parse_ok"])
        except Exception as e:  # API failure (e.g. quota): don't abort the batch
            out_row = conservative_row(row)
            totals["errors"] += 1
            key = (row.get("image_paths") or "").split(";")[0]
            print(f"  ! {key}: {type(e).__name__}: {str(e)[:120]} -> conservative row")
        out_rows.append(out_row)
    elapsed = time.time() - t0

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=schema.OUTPUT_COLUMNS, quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(out_rows)

    meta = {
        "model_id": client.model_id,
        "client": args.client,
        "prompt_version": PROMPT_VERSION,
        "git_commit": _git_commit(),
        "input": os.path.relpath(args.input, _REPO_ROOT),
        "n_images": sum(len(_abs_image_paths(r.get("image_paths", ""))) for r in rows),
        "elapsed_sec": round(elapsed, 2),
        **totals,
    }
    with open(args.output + ".run.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    print(f"Wrote {len(out_rows)} rows -> {args.output}")
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
