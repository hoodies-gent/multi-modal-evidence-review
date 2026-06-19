"""CSV loading and predicted<->truth alignment.

Rows are aligned on `image_paths`, which uniquely identifies a case directory.
`user_id` is NOT unique across the test set, so it cannot be the join key.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from typing import Optional


def read_csv(path: str):
    """Return (fieldnames, rows) where rows is a list of dicts. Empty cells -> ''."""
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = [{k: (v if v is not None else "") for k, v in row.items()} for row in reader]
    return fieldnames, rows


def _key(row: dict) -> str:
    return (row.get("image_paths") or "").strip()


@dataclass
class Alignment:
    pairs: list          # list of (key, pred_row, truth_row) present in BOTH
    pred_only: list      # keys present only in predictions
    truth_only: list     # keys present only in truth (missing predictions)
    duplicate_keys: list # keys appearing more than once in either file


def align(pred_rows: list, truth_rows: list) -> Alignment:
    """Align predictions to truth by image_paths."""
    pred_idx, pred_dupes = _index(pred_rows)
    truth_idx, truth_dupes = _index(truth_rows)

    pairs = []
    for key, truth_row in truth_idx.items():
        if key in pred_idx:
            pairs.append((key, pred_idx[key], truth_row))

    pred_only = [k for k in pred_idx if k not in truth_idx]
    truth_only = [k for k in truth_idx if k not in pred_idx]
    duplicate_keys = sorted(set(pred_dupes) | set(truth_dupes))
    return Alignment(pairs, pred_only, truth_only, duplicate_keys)


def _index(rows: list):
    idx = {}
    dupes = []
    for row in rows:
        k = _key(row)
        if k in idx:
            dupes.append(k)
        idx[k] = row
    return idx, dupes
