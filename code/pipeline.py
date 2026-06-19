"""Per-claim processing pipeline (static DAG).

Steps: build prompt -> (cache or) VLM call -> parse JSON -> clamp to schema ->
assemble the 14-column row. The clamp guarantees every emitted value is in the
allowed vocabulary, so output always passes the evaluator's validators. A bounded
parse-repair retry is supported (still a fixed pipeline, not ReAct).
"""

from __future__ import annotations

import json
import re
from typing import List, Optional, Tuple

import schema
from cache import Cache
from prompts import PROMPT_VERSION, build_prompt, build_verify_prompt
from vlm_client import VLMClient

DECISION_FIELDS = [
    "evidence_standard_met", "evidence_standard_met_reason", "risk_flags",
    "issue_type", "object_part", "claim_status", "claim_status_justification",
    "supporting_image_ids", "valid_image", "severity",
]


def _extract_json(text: str) -> Optional[dict]:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except ValueError:
            return None
    return None


def _as_bool_str(v, default: str) -> str:
    if v is None:
        return default
    return "true" if str(v).strip().lower() in {"true", "yes", "1"} else "false"


def _clamp_categorical(v, allowed: set, default: str) -> str:
    n = ("" if v is None else str(v)).strip().lower()
    return n if n in allowed else default


def _clamp_set(v, allowed: set, none_token: str = "none") -> str:
    if v is None:
        return none_token
    raw = v if isinstance(v, str) else ";".join(str(x) for x in v)
    toks = [t.strip().lower() for t in raw.split(";")]
    keep = [t for t in toks if t and t != none_token and t in allowed]
    # de-dupe, preserve order
    seen, ordered = set(), []
    for t in keep:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    return ";".join(ordered) if ordered else none_token


def clamp(parsed: dict, row: dict) -> dict:
    """Coerce a (possibly malformed) model dict into schema-valid decision fields."""
    parsed = parsed or {}
    allowed_ids = schema.image_ids_from_paths(row.get("image_paths", ""))
    return {
        "evidence_standard_met": _as_bool_str(parsed.get("evidence_standard_met"), "false"),
        "evidence_standard_met_reason": str(parsed.get("evidence_standard_met_reason", "") or ""),
        "risk_flags": _clamp_set(parsed.get("risk_flags"), schema.RISK_FLAGS),
        "issue_type": _clamp_categorical(parsed.get("issue_type"), schema.ISSUE_TYPE, "unknown"),
        "object_part": _clamp_categorical(parsed.get("object_part"), schema.parts_for(row.get("claim_object")), "unknown"),
        "claim_status": _clamp_categorical(parsed.get("claim_status"), schema.CLAIM_STATUS, "not_enough_information"),
        "claim_status_justification": str(parsed.get("claim_status_justification", "") or ""),
        "supporting_image_ids": _clamp_set(parsed.get("supporting_image_ids"), allowed_ids),
        "valid_image": _as_bool_str(parsed.get("valid_image"), "true"),
        "severity": _clamp_categorical(parsed.get("severity"), schema.SEVERITY, "unknown"),
    }


def conservative_row(row: dict) -> dict:
    """Schema-valid fallback row (passthrough inputs + clamped empty decision) for a
    claim whose API call failed, so one failure never aborts the whole batch."""
    out = {col: row.get(col, "") for col in schema.INPUT_COLUMNS}
    out.update(clamp({}, row))
    return {col: out.get(col, "") for col in schema.OUTPUT_COLUMNS}


def process_row(
    row: dict,
    history_row: Optional[dict],
    evidence_rows: list,
    client: VLMClient,
    cache: Cache,
    abs_image_paths: List[str],
    max_repair: int = 1,
    verify: bool = False,
) -> Tuple[dict, dict]:
    """Return (output_row, stats). stats = {cache_hit, api_calls, parse_ok}."""
    prompt = build_prompt(row, history_row, evidence_rows)
    rel_paths = [p.strip() for p in (row.get("image_paths") or "").split(";") if p.strip()]
    key = Cache.make_key(client.model_id, PROMPT_VERSION, prompt, rel_paths)

    stats = {"cache_hit": False, "api_calls": 0, "parse_ok": True}

    response = cache.get(key)
    if response is not None:
        stats["cache_hit"] = True
    else:
        response = client.analyze(prompt, abs_image_paths)
        stats["api_calls"] += 1

    parsed = _extract_json(response)
    attempts = 0
    while parsed is None and attempts < max_repair:
        attempts += 1
        response = client.analyze(prompt, abs_image_paths)
        stats["api_calls"] += 1
        parsed = _extract_json(response)

    if parsed is None:
        stats["parse_ok"] = False
        parsed = {}
    elif not stats["cache_hit"]:
        cache.set(key, response, meta={"model_id": client.model_id, "prompt_version": PROMPT_VERSION})

    decision = clamp(parsed, row)

    # Fix 1: claim-blind grounding pass — only when the main verdict is `supported`
    # (over-support is the bottleneck; verifying contradicted/NEI would waste calls).
    if verify and decision["claim_status"] == "supported" and decision["issue_type"] not in ("none", "unknown"):
        vprompt = build_verify_prompt(row.get("claim_object", ""), decision["object_part"], decision["issue_type"])
        vkey = Cache.make_key(client.model_id, PROMPT_VERSION + "|verify", vprompt, rel_paths)
        vresp = cache.get(vkey)
        if vresp is None:
            vresp = client.analyze(vprompt, abs_image_paths)
            stats["api_calls"] += 1
            cache.set(vkey, vresp, meta={"model_id": client.model_id, "stage": "verify"})
        vis = str((_extract_json(vresp) or {}).get("visibility", "")).strip().lower()
        if vis == "not_visible":
            decision["claim_status"] = "contradicted"
        elif vis == "cannot_tell":
            decision["claim_status"] = "not_enough_information"

    out = {col: row.get(col, "") for col in schema.INPUT_COLUMNS}
    out.update(decision)
    return {col: out.get(col, "") for col in schema.OUTPUT_COLUMNS}, stats
