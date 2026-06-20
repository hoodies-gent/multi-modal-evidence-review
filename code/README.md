# Multi-Modal Evidence Review - Solution

Verifies damage claims (car / laptop / package) from submitted images, a support-chat transcript, user history, and minimum-evidence requirements. For each input row it emits one schema-valid `output.csv` row with the 14 required fields. Images are the primary source of truth; the conversation only says what to check; user history is risk context that never overrides clear visual evidence.

## Shipped configuration

| | |
|---|---|
| Model | `gemini-3.5-flash` (vision) |
| Prompt | `v4` (in `prompts.py`; `temperature=0` for reproducibility) |
| Post-processing | deterministic rule layer (see *How it works*) |
| Result | `claim_status` **85%** on the labeled sample; all test rows schema-valid and internally coherent |

`PROMPT_VERSION` lives in `prompts.py` and is part of the cache key; it is bumped only on a confirmed-effective prompt change.

## Quickstart

Requires **Python ≥ 3.12**.

```bash
pip install -r requirements.txt          # google-genai, python-dotenv
export GEMINI_API_KEY=...                 # or GOOGLE_API_KEY  (read from env only)
# optionally put it in a repo-root .env (gitignored); main.py auto-loads it
```

## Run the system

```bash
cd code
# produce predictions for the test set
python main.py --input ../dataset/claims.csv --output ../output.csv \
  --client gemini --model-id gemini-3.5-flash

# offline smoke test (no API / no cost) with the deterministic stub client
python main.py --input ../dataset/sample_claims.csv --output ../preds.csv --client stub
```

Key flags: `--model-id` (exact Gemini id), `--min-interval` (seconds between calls; `0.5` paid tier, `~13` for the free-tier 5-RPM limit), `--limit N`, `--no-cache`. Each run also writes `<output>.run.json` (model, prompt version, call/cache counts, image count, timing, git commit) for traceability.

## Run the evaluation

```bash
cd code/evaluation
# score predictions against the labeled sample truth
python main.py --pred ../../preds.csv --truth ../../dataset/sample_claims.csv

# schema/contract validation only (e.g. on the test output)
python main.py --pred ../../output.csv --validate-only
```

Headline metric is `claim_status` accuracy; per-field breakdowns are exact-match accuracy (scalars) and micro-F1 (the multi-valued `risk_flags`, `supporting_image_ids`). Full report: `evaluation/evaluation_report.md`.

## How it works

### Single-call VLM pipeline

```
dataset/*.csv + images -> loader/join -> build prompt -> [cache or VLM call] -> parse JSON -> clamp to schema -> deterministic rules -> output.csv (+ .run.json)
```

One vision call per claim: the model receives the images plus a structured prompt — the transcript and any in-image text passed as **untrusted data** behind explicit anti-injection framing — and returns JSON constrained to the allowed vocabularies. A **clamp** step coerces any malformed or out-of-vocab field to a schema-valid value, so the output always passes the evaluator's validators.

### Deterministic rules

Some fields are functions of structured inputs or of the already-decided verdict, not independent visual judgments. We derive these deterministically (`pipeline.py`), which is more reliable and reproducible than trusting the model — at zero extra API cost:

- `user_history_risk` — derived from the joined `history_flags`.
- `manual_review_required` — set when the verdict is contradicted, the user has any history flag, or a claim/object mismatch was flagged.
- `evidence_standard_met` — set to `claim_status != not_enough_information` (a verdict means the evidence was sufficient; NEI means it was not).
- `severity` — forced to `unknown` whenever the claim could not be assessed (NEI / evidence not met).

These guarantee internal coherence regardless of model error.

### Cache + fault tolerance

- **On-disk cache** keyed by `model_id + prompt_version + prompt + image-set`, so runs are **resumable** (a re-run fills only gaps) and re-scoring is free.
- **Per-claim isolation**: an API failure on one claim writes a conservative schema-valid row instead of aborting the batch; 429/5xx are retried with backoff that honors the server's suggested `retryDelay`.

## Extending to another provider

The pipeline depends only on the `VLMClient` interface (`vlm_client.py`): implement `analyze(prompt, image_paths) -> raw_text` for your provider, register it in `get_client()`, and read its key from an env var (e.g. `OPENAI_API_KEY`, `OPENROUTER_API_KEY`). Nothing else changes - the cache `model_id` keeps responses from different models from colliding. (Only Gemini is implemented; untested clients are intentionally not shipped.)

## Secrets

API keys are read from environment variables only (`GEMINI_API_KEY`); none are hardcoded, and `.env` is gitignored.

## Project docs (`docs/`)

| File | Purpose |
|---|---|
| `planning.md` | task understanding, failure modes, eval spec |
| `decisions.md` | architecture/library choices (Choice / Alternative / Reason) |
| `experiment_log.md` | every eval run (Hypothesis / Change / Result / Decision) |
| `results.md` | auto-generated scoreboard across configs |
