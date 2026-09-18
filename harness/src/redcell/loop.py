"""The agent loop: system prompt -> model -> tool calls -> results -> repeat."""
from __future__ import annotations

import importlib.resources as res
from typing import Any

from . import tools
from .audit import AuditLog
from .client import RedCellClient, ModelError
from .config import ExerciseConfig, Settings, TeamConfig


def _bullet(items: list[str]) -> str:
    return "\n".join(f"- {x}" for x in items) if items else "- (none specified)"


def render_system_prompt(ex: ExerciseConfig) -> str:
    import re
    tmpl = res.files("redcell.prompts").joinpath("adversary.md").read_text(encoding="utf-8")
    # Strip HTML authoring comments so notes-to-self never reach the model.
    tmpl = re.sub(r"<!--.*?-->\n?", "", tmpl, flags=re.DOTALL)
    in_scope = _bullet(
        [f"network: {n}" for n in ex.in_scope_networks]
        + [f"system: {s}" for s in ex.in_scope_systems]
    )
    return (
        tmpl
        .replace("{{exercise_name}}", ex.name)
        .replace("{{scenario_id}}", ex.scenario_id)
        .replace("{{authorising_officer}}", ex.authorising_officer or "(unspecified)")
        .replace("{{authorisation_ref}}", ex.authorisation_ref or "(unspecified)")
        .replace("{{threat_actor_profile}}", ex.threat_actor_profile)
        .replace("{{in_scope_block}}", in_scope)
        .replace("{{out_of_scope_block}}", _bullet(ex.out_of_scope))
        .replace("{{attack_phases}}", ", ".join(ex.attack_phases))
    )


def run_session(settings: Settings, team: TeamConfig, objective: str,
                run_id: str | None = None) -> dict[str, Any]:
    """Drive one adversary session for one Blue Team until it stops or hits the
    turn cap. Returns a summary; the full transcript is in the audit log."""
    system = render_system_prompt(settings.exercise)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": objective},
    ]

    with AuditLog(settings.audit_dir, team.team_id, run_id) as audit, \
         RedCellClient(settings.endpoint, settings.sampling,
                       api_key_override=team.api_key or None) as client:
        audit.write("session_start", objective=objective,
                    exercise=settings.exercise.name, scenario=settings.exercise.scenario_id)

        for turn in range(1, team.max_turns + 1):
            try:
                resp = client.chat(messages, tools=tools.schemas())
            except ModelError as e:
                audit.write("model_error", turn=turn, error=str(e))
                # feed the error back so the model can recover (e.g. bad tool JSON)
                messages.append({"role": "user",
                                 "content": f"[harness] previous step failed: {e}. Adjust and continue."})
                continue

            audit.write("assistant_turn", turn=turn, content=resp.content,
                        reasoning=resp.reasoning if settings.sampling.include_reasoning_in_audit else "",
                        tool_calls=resp.tool_calls, usage=resp.usage,
                        finish_reason=resp.finish_reason)

            messages.append({"role": "assistant", "content": resp.content or "",
                             "tool_calls": resp.tool_calls or None})

            if not resp.wants_tools:
                if resp.finish_reason in ("stop", "length"):
                    audit.write("session_end", turn=turn, reason=resp.finish_reason)
                    return {"run_id": audit.run_id, "turns": turn,
                            "final": resp.content, "ended": resp.finish_reason}
                continue

            for call_id, name, args in resp.parsed_tool_calls():
                result = tools.dispatch(name, args)
                audit.write("tool_result", turn=turn, tool=name, args=args, result=result)
                messages.append({"role": "tool", "tool_call_id": call_id,
                                 "content": _as_text(result)})

        audit.write("session_end", turn=team.max_turns, reason="max_turns")
        return {"run_id": audit.run_id, "turns": team.max_turns, "ended": "max_turns"}


def _as_text(result: Any) -> str:
    import json
    return result if isinstance(result, str) else json.dumps(result, default=str)
