# Architecture (current)

Reflects the **shipped** system: `gemini-3.5-flash` · prompt `v5` · `temperature=0`,
a fixed per-claim pipeline (not ReAct), and a decoupled evaluation framework.
Rationale & history: `decisions.md` / `experiment_log.md`. Scores: `results.md`.

## Data flow

```
INPUTS (dataset/)                         SHARED
  claims.csv ───────────┐                 code/schema.py
  user_history.csv ───┐ │                   14-col order + allowed values + per-field
  evidence_req.csv ──┐│ │                   spec; imported by BOTH pipeline & eval
  images/ ─────────┐ ││ │                          │
  .env GEMINI_KEY ┐│ ││ │                          │ (clamp)          (validate/score)
                  ▼▼ ▼▼ ▼                           ▼                        ▼
        ┌──────────────────────┐   ╔════════ PER-CLAIM PIPELINE (pipeline.py) ════════╗
        │ main.py               │   ║ build_prompt (prompts.py, PROMPT_VERSION=v5):    ║
        │  load + join:         │──►║   claim_object + image-ids                       ║
        │   history by user_id  │   ║   transcript  ........ UNTRUSTED data            ║
        │   evidence by object  │   ║   user history ....... risk context only        ║
        │  resolve image paths  │   ║   evidence requirements                         ║
        │  load_dotenv(.env)    │   ║   field definitions + anti-injection rules       ║
        │  per-claim try/except │   ║              │                                   ║
        └──────────────────────┘   ║              ▼                                   ║
                                   ║   Cache.get(key = model_id | PROMPT_VERSION |    ║
        ┌──────────────────────┐   ║              prompt-hash | image-set)            ║
        │ cache.py (.cache/)   │◄─►║        hit ──────────────► response              ║
        │  disk JSON, resumable│   ║        miss ─► GeminiVLMClient.analyze() ─► set  ║
        └──────────────────────┘   ║                    │                             ║
                                   ║   GeminiVLMClient (vlm_client.py, native SDK):   ║
        ┌──────────────────────┐   ║     images(base64, id-labelled) + prompt         ║
        │ Gemini API           │◄─►║     temperature=0, JSON mode                      ║
        │ gemini-3.5-flash     │   ║     throttle (--min-interval) + 429 backoff      ║
        └──────────────────────┘   ║                    │                             ║
                                   ║              ▼                                   ║
                                   ║   _extract_json ─► clamp() to schema             ║
                                   ║     (allowed values, per-object parts, bools,    ║
                                   ║      sets, supporting_ids ⊆ this row's images)   ║
                                   ║              │                                   ║
                                   ║   [optional --verify grounding pass — OFF,       ║
                                   ║    rejected EXP-006]                             ║
                                   ║              ▼                                   ║
                                   ║   assemble 14-col row (inputs + clamped decision)║
                                   ╚══════════════════╪═══════════════════════════════╝
                                                      ▼
                                   output.csv (one row/claim, schema-exact)
                                   output.csv.run.json (model, prompt_ver, calls, images, time)
                                                      │
                                                      ▼  (decoupled: eval consumes CSV, never runs the model)
        ┌───────────────────────────── EVALUATION (code/evaluation/) ─────────────────────────────┐
        │ truth = sample_claims.csv  +  predictions.csv                                            │
        │   loader.align()  — pair rows on image_paths (unique per case; user_id is NOT unique)    │
        │   validators      — schema/contract check (allowed values, per-object parts, ids ⊆ imgs) │
        │   scorers         — scalar fields: accuracy; set fields: micro-F1; headline=claim_status │
        │   report          — reports/<tag>.json (+ markdown, per-case diff)                       │
        │   scoreboard.py   — aggregate reports/*.json ─► docs/results.md                          │
        └─────────────────────────────────────────────────────────────────────────────────────────┘
```

## Components

- **schema.py** — single source of truth: output column order, allowed-value
  vocabularies (per-object `object_part`), and the per-field spec (kind + how it is
  validated/scored). Imported by both the pipeline (to clamp) and eval (to validate/score),
  so the two can never drift.
- **main.py** — entry point. Loads/joins the CSVs, resolves image paths, loads `.env`,
  runs each claim through the pipeline (one failure → a conservative schema-valid row,
  never aborts the batch), writes `output.csv` + `output.csv.run.json`.
- **pipeline.py** — the fixed per-claim DAG above. `clamp()` guarantees schema-valid output.
- **prompts.py** — versioned prompt (`PROMPT_VERSION`). Treats the transcript and any
  in-image text as untrusted data (anti-injection); defines confusable issue types,
  the severity rubric, the claim_status boundary, and a multi-image "any-image-suffices" rule.
- **vlm_client.py** — `VLMClient` interface; `GeminiVLMClient` (native `google-genai`,
  `temperature=0`, JSON mode, throttle + 429 backoff); `StubVLMClient` (offline). Each
  client's `model_id` is part of the cache key, so models never contaminate each other.
- **cache.py** — on-disk response cache; makes runs resumable and re-runs ~free.
- **evaluation/** — reusable, model-decoupled scorer + validator + scoreboard.

## Key invariants

- **Images are the source of truth**; transcript = what to check (untrusted); history =
  risk context only, never overrides pixels.
- **Fixed pipeline, no agentic loop** → reproducible, countable calls, minimal injection surface.
- **`temperature=0` + cache** → reproducible output; per-run noise ≈ ±5 (1 case) otherwise.
- **clamp** → every emitted field is in the allowed vocabulary (output always validates).
```
