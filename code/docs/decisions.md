# Decisions — Architecture & Library Choices (Choice / Alternative / Reason)

- **Working conventions home = `CLAUDE.md`** / Alt: AGENTS.md, memory-only / Reason: CLAUDE.md is auto-loaded by Claude Code each session and is version-controlled, so it reliably enforces agent behavior. Rejected AGENTS.md (it is the challenge's given canonical contract — mixing our process into it pollutes the contract). Rejected memory-only (not in repo, not portable across machines, can be dropped in long-conversation summarization). Memory kept only as redundant fallback.
- **Persistent docs live under `code/docs/`** / Alt: repo-root `docs/` / Reason: only `code/` is zipped into `code.zip`, so docs under `code/docs/` reach the AI Judge; repo-root docs would be excluded from submission.
