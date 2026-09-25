# ambermist: instructions for coding assistants

Applies to every coding LLM (GPT/Codex, Claude, others). The latest user
request sets the scope; don't expand it.

The repository was reset on 2026-09-25. Read [LESSONS.md](LESSONS.md) before
starting work. Earlier code is at tag `archive/attempt-1`
(`git show archive/attempt-1:<path>`). Treat it as reference only; don't
restore it wholesale.

## Hard constraints

- **Fleet cap:** at most 1× H200, 1× H100, 2× RTX PRO 6000 GPUs, counting every
  held node. No other GPU families. Don't add hardware to make something fit.
- **Secrets:** never print or commit `.env`, keys, state, or secret-bearing
  output. Load credentials with `set -a; source .env; set +a`.
- **Held capacity:** don't destroy or replace running instances or the weights
  volume unless the user explicitly asks.

## Working rules

- Build the smallest thing that works, run it, then extend it. No speculative
  abstractions, dead code, or tests for code that doesn't exist.
- Don't claim something works unless it was run. Say what was verified and what wasn't.
- Commit only related work, and only when asked.
