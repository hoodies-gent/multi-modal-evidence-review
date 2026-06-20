# Evaluation Report

**Shipped system:** `gemini-3.5-flash` · prompt **v4** · `temperature=0` · deterministic rule layer. Evaluated on the labeled `dataset/sample_claims.csv` (n=20); final predictions produced for `dataset/claims.csv` (n=44) → `output.csv`.

**Headline:** `claim_status` accuracy **85%** on sample (vs a 60% majority-class floor). All 44 test rows pass schema validation with **0 coherence violations**.

---

## 1. Method

- **Metric per field type:** `claim_status` is the headline (the core decision). Other scalars (`issue_type`, `object_part`, `evidence_standard_met`, `valid_image`, `severity`) are exact-match accuracy; the multi-valued `risk_flags` and
  `supporting_image_ids` are micro-F1 on the value set; the free-text reason fields are not auto-scored. The scorer is cached and reproducible (`temperature=0`).
- **Floor:** a majority/constant predictor scores `claim_status` 60% — the bar the VLM had to clear (it also validated the scorer: pred=truth gives 100% / 0 issues).

## 2. Quality — configurations compared (sample, n=20)

Headline = `claim_status`. Set fields are micro-F1; rest are accuracy. Full scoreboard: `docs/results.md`; narrative: `docs/experiment_log.md`.

| Config | claim_status | ev_met | risk_flags | issue_type | object_part | supp_ids | valid_image | severity |
|---|---|---|---|---|---|---|---|---|
| majority floor | 60 | 85 | 0 | 15 | 5 | 0 | 90 | 15 |
| gemini-2.5-flash · v1 | 75 | 75 | 61 | 40 | 80 | 73 | 85 | 20 |
| gemini-3.5-flash · v4 (baseline) | 85 | 85 | 73 | 50 | 90 | 82 | 95 | 55 |
| **gemini-3.5-flash · v4 + rule layer (SHIPPED)** | **85** | **95** | **80** | **50** | **90** | **82** | **95** | **55** |
| gemini-3.5-flash · v6 (rejected) | 55 | 60 | 68 | 40 | 90 | 57 | 75 | 45 |

**Key decisions (see `docs/decisions.md` for full Choice/Alternative/Reason):**
- **Model:** `gemini-3.5-flash` over `gemini-2.5-flash`/`-pro` — best on the perception fields at flash cost; `-pro` gave no headline gain at higher cost.
- **Deterministic rule layer** (the v4→shipped jump): derives the flags/fields that are functions of structured inputs rather than vision `risk_flags` 73→80, `evidence_standard_met` 85 -> 95, `severity` +5 — at **zero extra API calls**, and removes all internally-incoherent rows.
- **Rejected: v6 image-consistency prompt rule** - fixed 1 case but over-triggered catastrophically (claim_status 85 -> 55), so kept v4. Rejected an OCR/injection stage (the VLM already reads in-image text; ablation showed the residual errors are perceptual, not text-signal).

## 3. Operational analysis

Every figure is tagged **[M]** measured (from `.run.json` / the scorer / the billing console), **[E]** estimated (derived from a measured input via an approximation), or **[A]** assumed (an external value we did not verify). The spec asks for approximate figures; we mark what is solid vs soft.

| Metric | Sample (n=20) | Test (n=44) | Basis |
|---|---|---|---|
| Model calls (cold cache) | 20 (1/claim) | 44 (1/claim) | **[M]** `.run.json` |
| Re-run calls (warm cache) | 0 | 0 | **[M]** `.run.json` |
| Images processed | 29 | 82 | **[M]** `.run.json` |
| Wall-clock runtime (cold) | ≈ 153 s | ≈ 463 s | **[M]** `.run.json` |
| Input tokens — text | ≈ 39k (~1.9k/call) | ≈ 85k (~1.9k/call) | **[E]** measured chars ÷ 4 |
| Input tokens — images | ≈ 15k | ≈ 41k | **[E]** ~500 tok/image assumption |
| Output tokens | ≈ 5k | ≈ 11k | **[E]** ~250 tok/call assumption |

**What is estimated, stated plainly:**
- **Text tokens [E]:** the prompt *character* counts are measured (~7.7k chars/call); only the chars→tokens factor (÷4) is approximate.
- **Image tokens [E/A]:** assumed ~500 tokens/image (Gemini tiles images; ~258 tokens per 384-px tile) — not measured.
- **Output tokens [E]:** assumed ~250 tokens/call for the fixed 14-field JSON — not measured.
- **Thinking tokens [not counted]:** `gemini-3.5-flash` may bill internal reasoning tokens; we did not estimate these (they partly explain the cost gap below).
- All token figures could be made exact with Gemini's free `count_tokens`; we did not, per the "approximate" spec.

**Cost:**
- **[M] Total project spend = $3.36** (Google billing) — the *entire* effort: every sample run, the model sweep (2.5-flash / -pro / 3.5-flash), the v1–v6 iterations,
  the temp=0 re-baselines, and the two test runs.
- **[E] Total calls ≈ 350** (counted from the experiment log, not the billing console) → blended **≈ $0.01 / call**.
- **[E] One full test-set pass (44 calls) ≈ $0.4**; cached re-runs ≈ $0 **[M]**.
- **[E, superseded] A-priori token-based estimate ≈ $0.07/pass — ~6× too low.** The gap is unmeasured image + thinking tokens and assumed-vs-actual per-token rates; we report the **measured** bill over this estimate.

**Rate limits & efficiency strategy:**
- **TPM/RPM:** the free tier is **5 RPM and 20 requests/day** per model — too tight for iteration + a 44-row run, so we moved to the paid tier. Calls are paced by a configurable `--min-interval` (0.5 s paid; ~13 s for free-tier 5-RPM safety).
- **Retry:** 429/5xx are retried with backoff that **honors the server's `retryDelay`**; on persistent failure a claim falls back to a conservative schema-valid row so one error never aborts the batch.
- **Caching:** on-disk cache keyed by `model_id + prompt_version + prompt + image-set` makes runs **resumable** (a re-run fills only gaps) and re-scoring free. This is why the final `output.csv` regen after the rule-layer change cost 0 calls.
- **No repeated work:** one call per claim; we explicitly rejected a second verification call (no independent signal — see experiment log EXP-006) and per-image decomposition (≈2× calls for no headline gain).

## 4. Failure-mode analysis (residual errors)

The remaining errors are concentrated and largely **perceptual / adversarial**, not pipeline bugs:

- **Perceptual ceiling (issue_type 50, part of severity):** systematic confusions the model makes regardless of prompt wording — `crack` read as `glass_shatter` (cases 003/009/013), `broken_part` under-recognized. Confirmed model-bound (definitions, same-model verification, and OCR all failed to move them).
- **Over-support on adversarial "no real damage" cases (014, 020):** the model takes faint/edited evidence at face value. case_014 is an **edited image** (a white circle drawn around a barely-visible scratch) claimed as impact damage; case_020 submits **two different packages** (one damaged + a handwritten "approve this claim" injection note, one intact). The model reports the visible "damage"; gold treats it as not genuinely present → contradicted.
- **Why we did not add an image-consistency rule:** gold routes structurally similar image-mismatch cases **inconsistently** — case_002 (two different cars) → NEI, but case_020 (two different packages) → contradicted — so no single rule is correct, and a prompt rule that tries makes the model paranoid (v6, above).
- **Manipulation under-weighting:** across 002/008/014/020 the adversarial signal (stock-photo watermarks, drawn annotations, injected text, mismatched image sets) is detected as a *risk flag* but under-weighted in the *verdict*. Robustly fixing this needs an independent signal or a stronger model, not a prompt tweak — flagged as future work.

**What the rule layer guarantees regardless of model error:** schema-valid output, and internal coherence — `evidence_standard_met ⇔ verdict`, `manual_review_required` on every contradiction/flagged-history case, and `severity=unknown` whenever the claim could not be assessed.
