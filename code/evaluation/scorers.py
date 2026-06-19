"""Per-field scoring, dispatched by field kind (see schema.FIELD_SPEC).

Scalar fields (bool/categorical) -> accuracy + confusion counts.
Set fields (set_fixed/set_dynamic) -> micro/macro P/R/F1 + exact-set-match.
Free-text fields are not scored in v1.

All comparisons are normalized (trim + lowercase) and order-independent for sets.
"""

from __future__ import annotations

from collections import Counter

import schema


def norm(v) -> str:
    return ("" if v is None else str(v)).strip().lower()


def parse_set(value, none_token: str) -> frozenset:
    """Split a ';'-separated cell into a normalized set. '' or the none_token -> empty set."""
    n = norm(value)
    if n == "" or n == none_token:
        return frozenset()
    parts = {p.strip().lower() for p in n.split(";")}
    parts.discard("")
    parts.discard(none_token)
    return frozenset(parts)


def _prf(tp: int, fp: int, fn: int):
    # Both-empty (tp=fp=fn=0) counts as a perfect match.
    if tp == 0 and fp == 0 and fn == 0:
        return 1.0, 1.0, 1.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def _score_scalar(field: str, pairs: list) -> dict:
    total = 0
    correct = 0
    confusion = Counter()   # (gold, pred) -> count
    errors = []             # (key, gold, pred)
    for key, pred_row, truth_row in pairs:
        gold = norm(truth_row.get(field))
        pred = norm(pred_row.get(field))
        total += 1
        confusion[(gold, pred)] += 1
        if gold == pred:
            correct += 1
        else:
            errors.append((key, gold, pred))
    return {
        "kind": "scalar",
        "n": total,
        "correct": correct,
        "accuracy": (correct / total) if total else None,
        "confusion": {f"{g}|{p}": c for (g, p), c in sorted(confusion.items())},
        "errors": errors,
    }


def _score_set(field: str, pairs: list, none_token: str) -> dict:
    tp_sum = fp_sum = fn_sum = 0
    macro_f1_sum = 0.0
    exact = 0
    total = 0
    errors = []  # (key, sorted(gold), sorted(pred))
    for key, pred_row, truth_row in pairs:
        gold = parse_set(truth_row.get(field), none_token)
        pred = parse_set(pred_row.get(field), none_token)
        tp = len(gold & pred)
        fp = len(pred - gold)
        fn = len(gold - pred)
        tp_sum += tp
        fp_sum += fp
        fn_sum += fn
        _, _, f1 = _prf(tp, fp, fn)
        macro_f1_sum += f1
        total += 1
        if gold == pred:
            exact += 1
        else:
            errors.append((key, sorted(gold), sorted(pred)))
    micro_p, micro_r, micro_f1 = _prf(tp_sum, fp_sum, fn_sum)
    return {
        "kind": "set",
        "n": total,
        "micro_precision": micro_p,
        "micro_recall": micro_r,
        "micro_f1": micro_f1,
        "macro_f1": (macro_f1_sum / total) if total else None,
        "exact_match_accuracy": (exact / total) if total else None,
        "errors": errors,
    }


def score_field(field: str, pairs: list) -> dict:
    spec = schema.FIELD_SPEC[field]
    kind = spec["kind"]
    if kind in ("bool", "categorical"):
        return _score_scalar(field, pairs)
    if kind in ("set_fixed", "set_dynamic"):
        return _score_set(field, pairs, spec.get("none_token", "none"))
    raise ValueError(f"field {field!r} (kind={kind}) is not scoreable")


def score_all(pairs: list) -> dict:
    """Score every scoreable field. Returns {field: result, ...} plus a summary."""
    results = {field: score_field(field, pairs) for field in schema.SCORED_FIELDS}
    headline = results.get(schema.HEADLINE_FIELD, {})
    summary = {
        "n_pairs": len(pairs),
        "headline_field": schema.HEADLINE_FIELD,
        "headline_accuracy": headline.get("accuracy"),
    }
    return {"summary": summary, "fields": results}
