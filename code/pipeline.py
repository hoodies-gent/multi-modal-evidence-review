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
from prompts import PROMPT_VERSION, build_prompt
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


def _derive_risk_flags(decision: dict, history_row: Optional[dict]) -> None:
    """Deterministic rule layer for the two DERIVABLE risk flags (planning.md §6).

    `user_history_risk` and `manual_review_required` are functions of structured
    inputs / already-decided fields, not independent visual judgments, so we derive
    them deterministically instead of trusting the model -- which systematically
    OVER-emits `manual_review_required` (EXP-012: 7 false positives on the sample).
    Rules (validated against sample gold): user_history_risk iff the joined user
    history carries it; manual_review_required iff the claim is contradicted, OR the
    user has any history flag, OR a claim/object mismatch was flagged. All other
    flags are perceptual and left exactly as the model emitted them. Mutates
    `decision` in place. Zero extra API calls; sample risk_flags micro-F1 .73 -> .80.
    """
    sset = {t for t in decision["risk_flags"].split(";") if t and t != "none"}
    sset.discard("user_history_risk")
    sset.discard("manual_review_required")

    hist_flags = set()
    if history_row:
        hist_flags = {t.strip().lower() for t in (history_row.get("history_flags") or "").split(";")
                      if t.strip() and t.strip().lower() != "none"}

    if "user_history_risk" in hist_flags:
        sset.add("user_history_risk")
    if (decision["claim_status"] == "contradicted" or hist_flags
            or (sset & {"claim_mismatch", "wrong_object"})):
        sset.add("manual_review_required")

    ordered = [f for f in sorted(schema.RISK_FLAGS) if f in sset]
    decision["risk_flags"] = ";".join(ordered) if ordered else "none"


def _derive_evidence_met(decision: dict) -> None:
    """Coherence invariant: evidence_standard_met == (claim_status != not_enough_information).
    Reaching a supported/contradicted verdict means the evidence WAS sufficient to evaluate;
    not_enough_information means it was not. This holds 20/20 on the sample gold. We derive it
    from the verdict (not the reverse) because the model's raw evidence flag is the noisier of
    the two -- it can say "the images conflict" (a confident contradiction) while still flagging
    evidence insufficient. MUST run before _derive_severity, which keys off evidence_standard_met.
    Confirmed EXP-014: ev_met 85->95; trades severity 60->55 on one already-misjudged case
    (002); removes all "contradicted + evidence-not-met" incoherence. Mutates `decision` in place."""
    decision["evidence_standard_met"] = (
        "true" if decision["claim_status"] != "not_enough_information" else "false"
    )


def _derive_severity(decision: dict) -> None:
    """Coherence invariant: severity is `unknown` whenever the claim cannot be
    assessed -- i.e. claim_status is not_enough_information OR the evidence standard
    is not met. You cannot grade the severity of damage you couldn't evaluate. Holds
    20/20 on sample gold; deterministically corrects the model's occasional slip
    (EXP-013: severity 55->60, zero extra API calls). Mutates `decision` in place."""
    
    if decision["claim_status"] == "not_enough_information" or decision["evidence_standard_met"] == "false":
        decision["severity"] = "unknown"


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
    _derive_risk_flags(decision, history_row)
    _derive_evidence_met(decision)
    _derive_severity(decision)

    out = {col: row.get(col, "") for col in schema.INPUT_COLUMNS}
    out.update(decision)
    return {col: out.get(col, "") for col in schema.OUTPUT_COLUMNS}, stats
