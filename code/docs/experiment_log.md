# Experiment Log (Hypothesis / Change / Result / Decision)

## EXP-000 — Trivial baseline (floor) on sample (n=20)

- **Hypothesis:** A constant/majority predictor establishes the accuracy floor the real system must beat, and exercises the scorer to confirm it has no bugs.
- **Change:** Two runs through `code/evaluation`. (1) Self-consistency: pred = truth = `sample_claims.csv`. (2) Trivial predictor: every row `claim_status=supported` (majority), `evidence_standard_met=true`, `valid_image=true`, `risk_flags=none`, `issue_type=unknown`, `object_part=unknown`, `supporting_image_ids=none`, `severity=unknown`.
- **Result:**
  - Self-consistency: 100% on all fields, **0 validation issues** → scorer/validator correct, allowed-value vocab matches dataset exactly.
  - Floor (trivial): `claim_status` **60.0%** (12/20 are `supported`); evidence_standard_met 85%; valid_image 90%; issue_type 15%; object_part 5%; severity 15%; risk_flags micro-F1 0%; supporting_image_ids micro-F1 0%.
  - Sample `claim_status` distribution: supported 12, contradicted 5, not_enough_information 3.
- **Decision:** Floor locked. The VLM MVP must clear 60% headline to justify using vision at all; the near-zero fields (risk_flags, supporting_image_ids, object_part, issue_type) are where most headroom is. Proceed to architecture/MVP.

## EXP-001 — MVP skeleton wired end-to-end (StubVLMClient)

- **Hypothesis:** The candidate-A pipeline (loader/join -> prompt -> client -> parse -> schema clamp -> output writer + cache + run-metadata) is correct and model-agnostic, verifiable without any API.
- **Change:** Built `code/{schema,vlm_client,cache,prompts,pipeline,main}.py`. Ran `main.py` on `sample_claims.csv` with the offline `StubVLMClient`, then scored via the eval framework.
- **Result:** 20/20 rows written, **0 validation issues** (clamp guarantees schema-valid output). Stub returns a conservative verdict (`not_enough_information`/`unknown`), scoring claim_status 15% (= the 3 NEI cases) and valid_image 90% — i.e. wiring is correct, not a real baseline. Re-run gave **20 cache hits / 0 api calls**, confirming the cache (keyed by model_id+prompt_version+prompt+image-set). run-metadata JSON emitted (model, prompt_version, counts, timing, git commit).
- **Decision:** Plumbing solid. The trivial floor (EXP-000, 60%) remains the bar. Next: wire a real Claude vision client (model selection) and run the first true VLM baseline at prompt v1.

## EXP-002 — First real VLM baseline: gemini-2.5-flash, prompt v1, sample (n=20)

- **Hypothesis:** A real VLM at prompt v1 clears the 60% trivial floor, proving vision adds value, and exposes the real per-field bottlenecks.
- **Change:** Implemented `GeminiVLMClient` (native google-genai). Ran on sample with `gemini-2.5-flash`.
- **Result:** **claim_status 75%** (> 60% floor). Per-field: object_part 80%, valid_image 85%, supporting_image_ids F1 72.7%, evidence_standard_met 75%, risk_flags F1 60.7%, issue_type 40%, **severity 20%**. 0 parse failures, 0 validation issues.
  - claim_status errors concentrate on the adversarial `contradicted` cases (case_008 watermark, case_020 seal-text, case_014 damage-not-visible) — model is too cautious there (flips to not_enough_information) or over-trusts the claim.
  - **Biggest bottlenecks:** severity (20% — systematically over-rates, medium->high repeatedly) and issue_type (40% — semantic confusions: crack<->glass_shatter, stain<->water_damage). Both look prompt-fixable (define severity levels; disambiguate issue types).
  - risk_flags: over-triggers `text_instruction_present` (case_014/015) and misses `manual_review_required`.
- **Operational:** Free tier `gemini-2.5-flash` = **5 RPM** — first run 429'd after ~5 calls. Added a 13s proactive throttle + 429-aware backoff (honours server retryDelay) + resumable cache; full 20-claim run then completed in ~485s (16 live calls + 4 cache hits). Cost ~cents. This RPM/throttle/cache behaviour feeds the operational-analysis report.
- **Decision:** Floor cleared. Before prompt iteration, finish the model sweep (gemini-2.5-pro, optionally gemini-3.5-flash) to pick the development model; then iterate prompt v1->v2 targeting severity + issue_type.

## EXP-003 — prompt v2 (severity rubric + issue_type disambiguation + risk_flags tightening): QUOTA-BLOCKED

- **Hypothesis:** Defining severity levels, disambiguating confusable issue_types, and tightening text_instruction_present / manual_review_required lifts severity (20%) and issue_type (40%) without a model change.
- **Change:** prompts.py v1->v2 (added `_DEFINITIONS`). Ran gemini-2.5-flash on sample.
- **Result:** NO RESULT — all 20 v2 calls hit the free-tier **daily** cap (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, **20/day** for gemini-2.5-flash), already spent by EXP-002. Run aborted with no output.
- **Operational finding (for the report):** gemini-2.5-flash free tier = 5 RPM AND **20 requests/day**, per model. The daily quota resets at midnight Pacific (~07:00 UTC), which is AFTER the challenge deadline (05:30 UTC) — so waiting for reset is not viable. Free tier cannot support iteration + a 44-row test run in time.
- **Decisions:** (1) Made `main.py` resilient — per-claim try/except writes a schema-valid conservative row on API failure and keeps cached successes, so one 429 no longer nukes the whole batch (verified with stub). (2) User is enabling **billing (paid tier)** to remove the quota blocker (total volume is cents). Re-run EXP-003 once paid tier is live.
