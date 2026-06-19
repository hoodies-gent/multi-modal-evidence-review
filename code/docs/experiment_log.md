# Experiment Log (Hypothesis / Change / Result / Decision)

## EXP-000 — Trivial baseline (floor) on sample (n=20)

- **Hypothesis:** A constant/majority predictor establishes the accuracy floor the real system must beat, and exercises the scorer to confirm it has no bugs.
- **Change:** Two runs through `code/evaluation`. (1) Self-consistency: pred = truth = `sample_claims.csv`. (2) Trivial predictor: every row `claim_status=supported` (majority), `evidence_standard_met=true`, `valid_image=true`, `risk_flags=none`, `issue_type=unknown`, `object_part=unknown`, `supporting_image_ids=none`, `severity=unknown`.
- **Result:**
  - Self-consistency: 100% on all fields, **0 validation issues** → scorer/validator correct, allowed-value vocab matches dataset exactly.
  - Floor (trivial): `claim_status` **60.0%** (12/20 are `supported`); evidence_standard_met 85%; valid_image 90%; issue_type 15%; object_part 5%; severity 15%; risk_flags micro-F1 0%; supporting_image_ids micro-F1 0%.
  - Sample `claim_status` distribution: supported 12, contradicted 5, not_enough_information 3.
- **Decision:** Floor locked. The VLM MVP must clear 60% headline to justify using vision at all; the near-zero fields (risk_flags, supporting_image_ids, object_part, issue_type) are where most headroom is. Proceed to architecture/MVP.
