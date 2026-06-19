"""Shared schema and allowed-value vocabularies for the claim-verification task.

Single source of truth imported by BOTH the production system (code/main.py) and
the evaluation framework (code/evaluation/). Keeping the column order and allowed
values in one place prevents drift between what the system emits and what the
evaluator validates/scores.

All values mirror dataset/problem_statement.md exactly. Edit here only.
"""

from __future__ import annotations

import os

# --- Columns ---------------------------------------------------------------

INPUT_COLUMNS = ["user_id", "image_paths", "user_claim", "claim_object"]

# Exact output column order required by problem_statement.md. Do not reorder.
OUTPUT_COLUMNS = [
    "user_id",
    "image_paths",
    "user_claim",
    "claim_object",
    "evidence_standard_met",
    "evidence_standard_met_reason",
    "risk_flags",
    "issue_type",
    "object_part",
    "claim_status",
    "claim_status_justification",
    "supporting_image_ids",
    "valid_image",
    "severity",
]

# --- Allowed value vocabularies -------------------------------------------

CLAIM_OBJECTS = {"car", "laptop", "package"}

CLAIM_STATUS = {"supported", "contradicted", "not_enough_information"}

ISSUE_TYPE = {
    "dent", "scratch", "crack", "glass_shatter", "broken_part", "missing_part",
    "torn_packaging", "crushed_packaging", "water_damage", "stain",
    "none", "unknown",
}

SEVERITY = {"none", "low", "medium", "high", "unknown"}

BOOL_VALUES = {"true", "false"}

RISK_FLAGS = {
    "none", "blurry_image", "cropped_or_obstructed", "low_light_or_glare",
    "wrong_angle", "wrong_object", "wrong_object_part", "damage_not_visible",
    "claim_mismatch", "possible_manipulation", "non_original_image",
    "text_instruction_present", "user_history_risk", "manual_review_required",
}

# object_part vocabulary depends on claim_object.
OBJECT_PARTS = {
    "car": {
        "front_bumper", "rear_bumper", "door", "hood", "windshield",
        "side_mirror", "headlight", "taillight", "fender", "quarter_panel",
        "body", "unknown",
    },
    "laptop": {
        "screen", "keyboard", "trackpad", "hinge", "lid", "corner", "port",
        "base", "body", "unknown",
    },
    "package": {
        "box", "package_corner", "package_side", "seal", "label", "contents",
        "item", "unknown",
    },
}

# --- Field spec: how each output column is validated and scored -------------
#
# kind:
#   passthrough  - echoed input column; validated for presence, not scored
#                  (claim_object additionally checked against CLAIM_OBJECTS)
#   bool         - "true"/"false"; scored by exact match
#   categorical  - single value from a fixed vocabulary; scored by exact match
#                  (object_part uses per_object=True -> vocab depends on row)
#   set_fixed    - ";"-separated subset of a fixed vocabulary; scored by set F1
#   set_dynamic  - ";"-separated ids; allowed values derived per row from
#                  image_paths; scored by set F1
#   free_text    - not auto-scored in v1 (hook left for an optional LLM judge)

FIELD_SPEC = {
    "user_id":                    {"kind": "passthrough"},
    "image_paths":                {"kind": "passthrough"},
    "user_claim":                 {"kind": "passthrough"},
    "claim_object":               {"kind": "passthrough", "allowed": CLAIM_OBJECTS},
    "evidence_standard_met":      {"kind": "bool"},
    "evidence_standard_met_reason": {"kind": "free_text"},
    "risk_flags":                 {"kind": "set_fixed", "allowed": RISK_FLAGS, "none_token": "none"},
    "issue_type":                 {"kind": "categorical", "allowed": ISSUE_TYPE},
    "object_part":                {"kind": "categorical", "per_object": True},
    "claim_status":               {"kind": "categorical", "allowed": CLAIM_STATUS},
    "claim_status_justification": {"kind": "free_text"},
    "supporting_image_ids":       {"kind": "set_dynamic", "none_token": "none"},
    "valid_image":                {"kind": "bool"},
    "severity":                   {"kind": "categorical", "allowed": SEVERITY},
}

HEADLINE_FIELD = "claim_status"

SCORED_KINDS = {"bool", "categorical", "set_fixed", "set_dynamic"}

# Fields scored by the evaluator, in a stable reporting order.
SCORED_FIELDS = [f for f in OUTPUT_COLUMNS if FIELD_SPEC[f]["kind"] in SCORED_KINDS]
FREE_TEXT_FIELDS = [f for f in OUTPUT_COLUMNS if FIELD_SPEC[f]["kind"] == "free_text"]

# --- Helpers ---------------------------------------------------------------


def parts_for(claim_object: str) -> set:
    """Allowed object_part values for a given claim_object (empty if unknown object)."""
    return OBJECT_PARTS.get((claim_object or "").strip().lower(), set())


def image_ids_from_paths(image_paths: str) -> set:
    """Derive the set of image IDs (filename without extension) from an
    image_paths cell. Paths are ';'-separated; ID is the basename stem."""
    ids = set()
    for raw in (image_paths or "").split(";"):
        raw = raw.strip()
        if not raw:
            continue
        stem = os.path.splitext(os.path.basename(raw))[0]
        if stem:
            ids.add(stem)
    return ids
