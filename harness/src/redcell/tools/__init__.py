"""Tool registry for the adversary agent.

IMPORTANT — read before implementing:
Every tool here is a STUB. The harness supplies the schema (what the model may
ask for) and the dispatch plumbing; the exercise team binds each tool to their
OWN sanctioned range systems inside the exercise network, under the rules of
engagement in prompts/adversary.md. Nothing in this repository executes actions
against any system. Keeping the implementations as range-bound stubs is
deliberate: the intelligence lives in the model's planning, the effects live in
tooling the Red Cell controls and can audit.
"""
from __future__ import annotations

from typing import Any, Callable

# name -> (json-schema tool definition, handler)
_REGISTRY: dict[str, tuple[dict[str, Any], Callable[..., Any]]] = {}


def tool(schema: dict[str, Any]) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    name = schema["function"]["name"]

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        _REGISTRY[name] = (schema, fn)
        return fn

    return deco


def schemas() -> list[dict[str, Any]]:
    return [s for s, _ in _REGISTRY.values()]


def dispatch(name: str, arguments: dict[str, Any]) -> Any:
    if name not in _REGISTRY:
        return {"error": f"unknown tool {name!r}", "available": sorted(_REGISTRY)}
    _, fn = _REGISTRY[name]
    return fn(**arguments)


# Import stub modules so their @tool decorators register. Add your own here.
from . import recon, planning  # noqa: E402,F401
