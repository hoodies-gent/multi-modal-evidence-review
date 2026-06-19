"""Schema / contract validation for a predictions CSV.

Independent of accuracy scoring: this catches submission-breaking problems
(wrong columns, illegal values, ids not present in the row's images) on the
test output.csv where no ground truth exists. Use via main.py --validate-only.
"""

from __future__ import annotations

from dataclasses import dataclass

import schema
from evaluation.scorers import norm, parse_set


@dataclass
class Issue:
    key: str       # image_paths of the offending row (or "<header>")
    column: str
    message: str


def validate_columns(fieldnames: list) -> list:
    """Output CSV must have exactly the required columns in the required order."""
    issues = []
    if fieldnames != schema.OUTPUT_COLUMNS:
        missing = [c for c in schema.OUTPUT_COLUMNS if c not in fieldnames]
        extra = [c for c in fieldnames if c not in schema.OUTPUT_COLUMNS]
        if missing:
            issues.append(Issue("<header>", "", f"missing columns: {missing}"))
        if extra:
            issues.append(Issue("<header>", "", f"unexpected columns: {extra}"))
        if not missing and not extra:
            issues.append(Issue("<header>", "", "columns present but in wrong order"))
    return issues


def validate_rows(rows: list) -> list:
    """Check every cell against the allowed-value vocabularies."""
    issues = []
    for row in rows:
        key = (row.get("image_paths") or "").strip() or "<no image_paths>"
        for field, spec in schema.FIELD_SPEC.items():
            kind = spec["kind"]
            raw = row.get(field)
            val = norm(raw)

            if kind == "free_text":
                continue

            if kind == "passthrough":
                if field == "claim_object" and val not in schema.CLAIM_OBJECTS:
                    issues.append(Issue(key, field, f"illegal claim_object {raw!r}"))
                elif raw in (None, ""):
                    issues.append(Issue(key, field, "empty input echo column"))
                continue

            if kind == "bool":
                if val not in schema.BOOL_VALUES:
                    issues.append(Issue(key, field, f"expected true/false, got {raw!r}"))

            elif kind == "categorical":
                if spec.get("per_object"):
                    allowed = schema.parts_for(row.get("claim_object"))
                    if val not in allowed:
                        issues.append(Issue(key, field, f"{raw!r} not a valid {row.get('claim_object')} part"))
                else:
                    if val not in spec["allowed"]:
                        issues.append(Issue(key, field, f"{raw!r} not in allowed values"))

            elif kind == "set_fixed":
                bad = parse_set(raw, spec["none_token"]) - {v for v in spec["allowed"]}
                if bad:
                    issues.append(Issue(key, field, f"unknown flags: {sorted(bad)}"))

            elif kind == "set_dynamic":
                allowed_ids = schema.image_ids_from_paths(row.get("image_paths"))
                bad = parse_set(raw, spec["none_token"]) - allowed_ids
                if bad:
                    issues.append(Issue(key, field, f"image ids not in this row's images: {sorted(bad)}"))
    return issues


def validate(fieldnames: list, rows: list) -> list:
    return validate_columns(fieldnames) + validate_rows(rows)
