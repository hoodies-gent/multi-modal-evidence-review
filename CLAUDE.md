@AGENTS.md

---

# Working Conventions (agent ↔ Yifan)

These are the agreed working conventions for this project. They govern how the
agent (Claude Code) collaborates with the user. AGENTS.md above is the
challenge's canonical contract; this section is *how we work*, layered on top.
Where this section overrides AGENTS.md, it is intentional and the user's call.

## 1. Development workflow (eval-driven, single-variable iteration)

Follow this order. Do not skip steps or jump to implementation early.

1. **理解题目 + data audit** — read the corpus; inspect label distributions,
   schema quirks, multi-image ratio, exact formatting of expected outputs.
   Data distribution determines what eval measures, so this precedes eval.
2. **设计 eval** — define the scorer and the metric for *each* output field
   before building the system (see §3).
3. **Trivial baseline** — run a constant/majority-class dummy predictor through
   the scorer to (a) validate the scorer has no bugs and (b) establish a floor
   the real system must beat. Near-zero cost.
4. **Arch 设计 / 决策** — decide architecture & libraries; record in
   `code/docs/decisions.md` before implementing.
5. **MVP 实现** — minimal end-to-end pipeline, with on-disk caching from day one.
6. **Baseline 跑分** — run MVP through eval; record in
   `code/docs/experiment_log.md`.
7. **单变量迭代** — eval → analyze bottleneck → decide/record → implement.
   Change **one variable at a time** so result deltas are attributable.

## 2. Decision discipline

- Every decision discussion — **what to choose AND what to reject** — must have a
  clear analysis trail, not just a conclusion: list options, trade-offs, and the
  reason for choosing/rejecting.
- **Discuss and get the user's confirmation first, then record.** Do not log an
  undecided option as if it were decided.

## 3. Persistent docs (live under `code/` so they ship inside `code.zip`)

| File | Purpose | Format (one entry per line/block) |
|---|---|---|
| `code/docs/planning.md` | Task understanding + failure modes + eval spec | prose, < 1 page |
| `code/docs/decisions.md` | Architectural / library choices | **Choice / Alternative / Reason** (rejected options go in Reason with why) |
| `code/docs/experiment_log.md` | Each eval run | **Hypothesis / Change / Result / Decision** |

`decisions.md` records *why we designed it this way*; `experiment_log.md` records
*what the data told us*. Keep them distinct.

## 4. Eval design principles

The 14 output fields are not homogeneous; the scorer handles them by type:

- **Exact-match (accuracy / per-class F1):** `claim_status`, `issue_type`,
  `object_part`, `evidence_standard_met`, `valid_image`, `severity`.
- **Set-overlap (P/R/F1 on the value set, not string equality):** `risk_flags`,
  `supporting_image_ids`.
- **Free-text (not auto-scored; optionally LLM-judged):** the two `*_reason`
  fields and `claim_status_justification`.
- **Headline metric:** `claim_status` accuracy (the core decision). Other fields
  are per-field breakdowns used to locate bottlenecks.
- Eval must be **cached** (key = image-set + prompt-version) so iteration is fast
  and cheap; this also feeds the required operational analysis in
  `evaluation/evaluation_report.md`.

## 5. Transcript logging

The user exports the HackerRank chat transcript himself. The agent does **NOT**
append per-turn entries to `~/hackerrank_orchestrate/log.txt` (this overrides
AGENTS.md §5), unless the user explicitly asks.

## 6. Submission boundary reminder

Only the `code/` directory is zipped into `code.zip`. Anything the AI Judge
should see (planning, decisions, experiments, README, eval report) must live
under `code/`. Repo-root files (CLAUDE.md, AGENTS.md) are not submitted.
