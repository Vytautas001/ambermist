"""Reconnaissance tools — STUBS.

Each returns a not-implemented marker with the exact contract the Red Cell must
honour when binding it to a real range service. The model can call these to
express intent; until they are bound, the harness records the intent (valuable
for after-action review) and returns a clear 'not wired up' result rather than
inventing data, which would poison the exercise.
"""
from __future__ import annotations

from typing import Any

from . import tool


def _stub(tool_name: str, **echo: Any) -> dict[str, Any]:
    return {
        "status": "NOT_IMPLEMENTED",
        "tool": tool_name,
        "note": "Bind this to a sanctioned range service in tools/recon.py. "
                "Until then the harness records the intent but performs no action.",
        "requested": echo,
    }


@tool({
    "type": "function",
    "function": {
        "name": "enumerate_hosts",
        "description": "Request host enumeration for an IN-SCOPE network range. "
                       "Returns hosts the range service reports as reachable.",
        "parameters": {
            "type": "object",
            "properties": {
                "cidr": {"type": "string", "description": "target range, MUST be in scope"},
                "rationale": {"type": "string"},
            },
            "required": ["cidr", "rationale"],
        },
    },
})
def enumerate_hosts(cidr: str, rationale: str) -> dict[str, Any]:
    return _stub("enumerate_hosts", cidr=cidr, rationale=rationale)


@tool({
    "type": "function",
    "function": {
        "name": "inspect_service",
        "description": "Ask the range service what is listening on a host:port that "
                       "prior enumeration already reported as in-scope and reachable.",
        "parameters": {
            "type": "object",
            "properties": {
                "host": {"type": "string"},
                "port": {"type": "integer"},
            },
            "required": ["host", "port"],
        },
    },
})
def inspect_service(host: str, port: int) -> dict[str, Any]:
    return _stub("inspect_service", host=host, port=port)
