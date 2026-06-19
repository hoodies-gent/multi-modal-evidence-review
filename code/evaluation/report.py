"""Render scoring + validation results as JSON and human-readable markdown/console.

Includes a per-case diff so the iteration step can see exactly which cases failed
and on which fields (drives bottleneck analysis).
"""

from __future__ import annotations

import json

import schema


def _pct(x):
    return "  -  " if x is None else f"{x * 100:5.1f}%"


def build_report(score_result: dict, issues: list, alignment) -> dict:
    """Assemble a machine-readable report dict."""
    return {
        "summary": score_result.get("summary", {}),
        "alignment": {
            "pairs_scored": len(alignment.pairs),
            "predictions_without_truth": len(alignment.pred_only),
            "truth_without_predictions": alignment.truth_only,
            "duplicate_keys": alignment.duplicate_keys,
        },
        "validation": {
            "n_issues": len(issues),
            "issues": [{"key": i.key, "column": i.column, "message": i.message} for i in issues],
        },
        "fields": score_result.get("fields", {}),
    }


def write_json(report: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)


def _per_case_diffs(score_result: dict) -> dict:
    """Invert field errors into {case_key: [(field, gold, pred), ...]}."""
    cases = {}
    for field, res in score_result.get("fields", {}).items():
        if res["kind"] == "scalar":
            for key, gold, pred in res.get("errors", []):
                cases.setdefault(key, []).append((field, gold, pred))
        else:
            for key, gold, pred in res.get("errors", []):
                cases.setdefault(key, []).append(
                    (field, "{" + ";".join(gold) + "}", "{" + ";".join(pred) + "}")
                )
    return cases


def to_markdown(report: dict, score_result: dict) -> str:
    lines = []
    s = report["summary"]
    a = report["alignment"]
    v = report["validation"]

    lines.append("# Evaluation Report\n")
    if s:
        lines.append(f"**Headline — `{s['headline_field']}` accuracy: {_pct(s['headline_accuracy'])}** "
                     f"over {s['n_pairs']} cases.\n")
    lines.append(f"- Pairs scored: {a['pairs_scored']}")
    lines.append(f"- Predictions without truth: {a['predictions_without_truth']}")
    if a["truth_without_predictions"]:
        lines.append(f"- ⚠ Truth rows missing a prediction: {len(a['truth_without_predictions'])}")
    if a["duplicate_keys"]:
        lines.append(f"- ⚠ Duplicate image_paths keys: {a['duplicate_keys']}")
    lines.append(f"- Validation issues: {v['n_issues']}")
    lines.append("")

    # Scalar field accuracy table.
    lines.append("## Per-field metrics\n")
    lines.append("| Field | Type | Accuracy / micro-F1 | n |")
    lines.append("|---|---|---|---|")
    for field in schema.SCORED_FIELDS:
        res = score_result["fields"][field]
        if res["kind"] == "scalar":
            metric = _pct(res["accuracy"])
        else:
            metric = _pct(res["micro_f1"]) + " (F1)"
        lines.append(f"| {field} | {res['kind']} | {metric} | {res['n']} |")
    lines.append("")

    # Headline confusion matrix.
    head = score_result["fields"].get(schema.HEADLINE_FIELD, {})
    if head.get("confusion"):
        lines.append(f"## `{schema.HEADLINE_FIELD}` confusion (gold|pred -> count)\n")
        for k, c in sorted(head["confusion"].items(), key=lambda kv: -kv[1]):
            lines.append(f"- {k} -> {c}")
        lines.append("")

    # Per-case failures.
    diffs = _per_case_diffs(score_result)
    if diffs:
        lines.append(f"## Per-case failures ({len(diffs)} cases with >=1 wrong field)\n")
        for key in sorted(diffs):
            short = key.split(";")[0]
            field_strs = ", ".join(f"{fld}: {g}->{p}" for fld, g, p in diffs[key])
            lines.append(f"- `{short}` — {field_strs}")
        lines.append("")

    # Validation issues detail.
    if v["issues"]:
        lines.append("## Validation issues\n")
        for i in v["issues"]:
            lines.append(f"- `{i['key']}` [{i['column']}] {i['message']}")
        lines.append("")

    return "\n".join(lines)
