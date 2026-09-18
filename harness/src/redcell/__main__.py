"""CLI: run one adversary session, or health-check the endpoint."""
from __future__ import annotations

import argparse
import logging
import sys

from .config import Settings
from .client import RedCellClient
from .loop import run_session


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="redcell")
    ap.add_argument("-c", "--config", default="exercise.yaml", help="path to exercise config")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="run one adversary session for a team")
    p_run.add_argument("--team", required=True)
    p_run.add_argument("--objective", required=True, help="the engagement objective for this session")

    sub.add_parser("health", help="check the model endpoint is serving")
    sub.add_parser("prompt", help="print the rendered system prompt and exit")

    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.load(a.config)

    if a.cmd == "health":
        with RedCellClient(settings.endpoint, settings.sampling) as c:
            ok = c.health()
        print("endpoint healthy" if ok else "endpoint UNREACHABLE")
        return 0 if ok else 1

    if a.cmd == "prompt":
        from .loop import render_system_prompt
        print(render_system_prompt(settings.exercise))
        return 0

    if a.cmd == "run":
        team = settings.team(a.team)
        summary = run_session(settings, team, a.objective)
        print(f"session {summary['run_id']} ended: {summary.get('ended')} after {summary['turns']} turns")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
