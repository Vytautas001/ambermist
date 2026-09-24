"""Configuration: exercise-wide settings and per-team session settings."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator


class EndpointConfig(BaseModel):
    """Where the model lives. Point this at the LiteLLM router, never at a serving
    replica directly — the router is what enforces per-team admission."""

    base_url: str = Field(default_factory=lambda: os.environ.get(
        "REDCELL_BASE_URL", "http://localhost:4000/v1"))
    api_key: str = Field(default_factory=lambda: os.environ.get("REDCELL_API_KEY", ""))
    model: str = "redcell-adversary"
    timeout_s: float = 300.0          # agentic turns are long
    connect_timeout_s: float = 10.0
    max_retries: int = 3

    @field_validator("base_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")


class SamplingConfig(BaseModel):
    temperature: float = 0.7
    top_p: float = 0.9
    max_tokens: int = 2048
    # Qwen3.8 exposes a reasoning trace; keep it out of the transcript the Blue
    # Team ever sees, but DO keep it in the audit log for after-action review.
    include_reasoning_in_audit: bool = True


class ExerciseConfig(BaseModel):
    """Rules of engagement. These are rendered into the system prompt and are
    the authoritative statement of what the simulated adversary may do."""

    name: str = "UNNAMED-EXERCISE"
    scenario_id: str = "SCN-000"
    # Explicit scope. Anything not listed is out of bounds.
    in_scope_networks: list[str] = Field(default_factory=list)
    in_scope_systems: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    threat_actor_profile: str = "generic financially-motivated intrusion set"
    attack_phases: list[str] = Field(default_factory=lambda: [
        "reconnaissance", "initial-access", "execution", "persistence",
        "privilege-escalation", "defense-evasion", "credential-access",
        "discovery", "lateral-movement", "collection", "exfiltration", "impact",
    ])
    authorising_officer: str = ""
    authorisation_ref: str = ""

    @field_validator("in_scope_networks")
    @classmethod
    def _scope_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError(
                "in_scope_networks must not be empty. An adversary simulation "
                "without an explicit scope is not a sanctioned exercise."
            )
        return v


class TeamConfig(BaseModel):
    """One Blue Team's session. Each gets its own router key so usage, latency
    and the full transcript are attributable per team."""

    team_id: str
    display_name: str = ""
    api_key: str = ""                  # per-team virtual key from the router
    max_turns: int = 50                # hard stop on the agent loop
    max_parallel_tools: int = 4


class Settings(BaseModel):
    endpoint: EndpointConfig = Field(default_factory=EndpointConfig)
    sampling: SamplingConfig = Field(default_factory=SamplingConfig)
    exercise: ExerciseConfig
    teams: list[TeamConfig] = Field(default_factory=list)
    audit_dir: Path = Path("audit")

    @classmethod
    def load(cls, path: str | Path) -> "Settings":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)

    def team(self, team_id: str) -> TeamConfig:
        for t in self.teams:
            if t.team_id == team_id:
                return t
        raise KeyError(f"unknown team {team_id!r}; known: {[t.team_id for t in self.teams]}")
