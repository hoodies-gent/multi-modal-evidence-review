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
- **Re-run (paid tier live, gemini-2.5-flash, prompt v2, sample n=20):** **claim_status 75% (unchanged vs v1)**. **severity 20% -> 45% (+25)**, **risk_flags F1 60.7% -> 74.2% (+13.5)**, supporting_image_ids F1 72.7% -> 76.5%, evidence_standard_met 75% -> 80%; object_part 80% (flat); valid_image 85% -> 80% (1-case noise); **issue_type 40% -> 35% (-5)**. 19 live calls, 0 failures, 0 validation issues.
- **Decision:** Keep prompt v2 (net win — big severity + risk_flags gains; -5s are 1-case noise). issue_type did NOT improve from definitions -> confusable types (crack vs glass_shatter) have a perceptual component; defer to the later model sweep or a different disambiguation approach. Next targets: issue_type and the adversarial `contradicted` cases that cap claim_status. Paid tier confirmed; use `--min-interval 0.5`.

## EXP-004 — Model sweep at prompt v2 (sample n=20): flash vs pro vs 3.5-flash

- **Hypothesis:** With the prompt now decent (v2), a model sweep picks the ship model and answers whether issue_type's weakness is model-bound (perceptual) or prompt-bound.
- **Change:** Same prompt v2; ran gemini-2.5-flash / -2.5-pro / -3.5-flash. See `results.md` (auto-generated scoreboard).
- **Result (claim_status | issue_type | severity | object_part):**
  - gemini-2.5-flash: **75** | 35 | 45 | 80
  - gemini-2.5-pro:   **75** | 40 | 45 | 70
  - gemini-3.5-flash: **65** | **50** | **60** | **90**
- **Findings:** (1) **pro gives no benefit over flash** — same headline 75, worse object_part, marginal elsewhere; not worth its cost/latency. (2) **issue_type IS model-sensitive** (35->40->50) — confirms it's partly perceptual, not just prompt. (3) **3.5-flash trade-off:** best on perception fields (issue_type 50, object_part 90, severity 60) but WORST on the headline claim_status (65 vs 75). Flat 8-field average: 3.5-flash 68.7 ≈ flash 68.2 > pro 67.2.
- **Decision:** Drop pro. Keep gemini-2.5-flash as the dev model for now (best on the headline decision). 3.5-flash is a strong candidate IF its claim_status drop is recoverable — investigate why its headline is lower (its `contradicted`/NEI calls) next; if fixable, it likely becomes the ship model (keeps the perception edge). Model choice stays OPEN between flash and 3.5-flash.

## EXP-005 — prompt v3 (sharper claim_status boundary) on flash + 3.5-flash (sample n=20)

- **Hypothesis (from EXP-004 error analysis):** 3.5-flash's low headline is over-skepticism (it flips clearly-visible supported cases to contradicted/NEI), not a perception failure. A v3 rule — "claimed issue visible -> supported; contradicted only when evidence conflicts; uncertainty -> NEI" — should recover its headline without hurting flash.
- **Change:** prompt v2 -> v3 (added a claim_status decision block). Ran both models. Scoreboard auto-updated.
- **Result:** flash v3 claim_status **75** (flat vs v2) with it 35->40, op 80->85, si 76->80, vi 80->85, sev 45->50, but **risk_flags 74->62**. 3.5-flash v3 claim_status **65->70** (+5, partial recovery), it 50->55, sev 60->70, op 90. Flat 8-field avg: **3.5-flash v3 72.1 > flash v3 69.6**.
- **Error analysis (v3):** flash 5 cs-errors, 3.5-flash 6; they SHARE 3 hard cases (007 blurry door-dent, 014 damage-not-visible, 020 seal-text) that cap both — the real headline ceiling. The 75-vs-70 gap is essentially 1 non-shared case (models trade: flash now mildly OVER-supports — 005/018; 3.5 mildly over-skeptical — 002/010/019). So the two models' decision quality is ~equivalent; 3.5-flash's perception lead (issue_type +15, severity +20) is the decisive differentiator.
- **Decision (recommended, pending user confirm):** Adopt **gemini-3.5-flash · v3** as ship/dev model — near-equal headline (gap = 1 case + shared hard ceiling) but clearly stronger on issue_type/severity/object_part and best flat average. Next targets: the 3 shared hard cases (007/014/020 — blurry-image handling + damage-not-visible + seal-text/authenticity). [CONFIRMED — model committed, main.py defaults updated.]

## EXP-006 — Fix 1: claim-blind grounding verification pass (3.5-flash · v3 +verify): NEGATIVE RESULT

- **Hypothesis:** A conditional second call (only on `supported` verdicts) asking "is {issue} actually visible on {part}?" catches over-support (014/020).
- **Change:** Added `--verify` (conditional 2nd VLM call; flips supported->contradicted on not_visible, ->NEI on cannot_tell). Ran 3.5-flash·v3 +verify (main cached; 11 verify calls).
- **Result:** **claim_status 70 -> 65 (WORSE)**. Exactly ONE flip occurred — case_001 (true supported rear-bumper dent) wrongly flipped to contradicted [BROKE]. It did NOT catch the target 014 (the verifier also answered "visible" — same hallucination).
- **Root cause:** SAME-MODEL self-verification yields CORRELATED errors — the verifier shares the main call's perceptual blind spots, so it neither catches the missed contradiction (014) nor avoids false flips (001). A second opinion from the same model adds no independent signal.
- **Decision:** REJECT Fix 1; `--verify` stays OFF by default (kept flag-gated for possible reuse with an INDEPENDENT/different-family verifier, which we don't currently have). Over-support on subtle "no-damage" cases (014) is a perceptual ceiling for this model family. Stop chasing the 3 ceiling cases with same-model tricks (overfit + cost risk); ship config stays gemini-3.5-flash · v3.
