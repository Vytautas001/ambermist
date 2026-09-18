"""Planning tools — pure reasoning aids, safe to run anywhere. These are real,
not stubs: they help the model structure an engagement without touching a range.
"""
from __future__ import annotations

from typing import Any

from . import tool

_NOTES: dict[str, list[str]] = {}


@tool({
    "type": "function",
    "function": {
        "name": "record_hypothesis",
        "description": "Record a hypothesis about the target environment or the "
                       "Blue Team's likely defensive posture, to reason over later.",
        "parameters": {
            "type": "object",
            "properties": {
                "phase": {"type": "string", "description": "current attack phase"},
                "hypothesis": {"type": "string"},
                "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            },
            "required": ["phase", "hypothesis"],
        },
    },
})
def record_hypothesis(phase: str, hypothesis: str, confidence: str = "medium") -> dict[str, Any]:
    _NOTES.setdefault(phase, []).append(f"[{confidence}] {hypothesis}")
    return {"ok": True, "recorded_for_phase": phase, "count": len(_NOTES[phase])}


@tool({
    "type": "function",
    "function": {
        "name": "list_hypotheses",
        "description": "List hypotheses recorded so far, optionally for one phase.",
        "parameters": {
            "type": "object",
            "properties": {"phase": {"type": "string"}},
        },
    },
})
def list_hypotheses(phase: str | None = None) -> dict[str, Any]:
    if phase:
        return {"phase": phase, "hypotheses": _NOTES.get(phase, [])}
    return {"all": _NOTES}
