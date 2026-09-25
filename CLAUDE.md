# ambermist — entry point for Claude Code

Read [AGENTS.md](AGENTS.md) for the shared instructions used by all coding
assistants, including GPT/Codex and Claude. Do not maintain a separate model or
hardware policy here.

The selected target is Qwen3.8-Flash-Next-Abliterated-GGUF on the capped Verda
fleet. Implementation is pending. Qwen3.5/vLLM is archived, and the eight-team
target is deferred (ADR 0005). Read [the implementation handoff](docs/IMPLEMENTATION-HANDOFF.md)
before undertaking a coding task, and keep documentation-only requests scoped
to documentation.
