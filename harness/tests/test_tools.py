import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from redcell import tools


def test_registry_populated():
    names = {s["function"]["name"] for s in tools.schemas()}
    assert {"enumerate_hosts", "record_hypothesis"} <= names


def test_recon_is_stub_not_fabricated():
    out = tools.dispatch("enumerate_hosts", {"cidr": "10.20.0.0/16", "rationale": "r"})
    assert out["status"] == "NOT_IMPLEMENTED", "recon must not fabricate range data"


def test_unknown_tool_is_handled():
    out = tools.dispatch("does_not_exist", {})
    assert "error" in out


def test_planning_tool_actually_runs():
    tools.dispatch("record_hypothesis", {"phase": "recon", "hypothesis": "h", "confidence": "low"})
    got = tools.dispatch("list_hypotheses", {"phase": "recon"})
    assert any("h" in x for x in got["hypotheses"])
