import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pytest
from redcell.client import ChatResponse, ModelError


def _resp(msg):
    return ChatResponse({"choices": [{"message": msg, "finish_reason": "tool_calls"}],
                         "usage": {}, "model": "redcell-adversary"})


def test_parses_tool_calls():
    r = _resp({"tool_calls": [{"id": "c1", "function": {"name": "enumerate_hosts",
              "arguments": '{"cidr": "10.20.0.0/16", "rationale": "recon"}'}}]})
    calls = r.parsed_tool_calls()
    assert calls[0][1] == "enumerate_hosts"
    assert calls[0][2]["cidr"] == "10.20.0.0/16"


def test_bad_tool_json_raises_clearly():
    r = _resp({"tool_calls": [{"id": "c1", "function": {"name": "x", "arguments": "{not json"}}]})
    with pytest.raises(ModelError):
        r.parsed_tool_calls()


def test_plain_text_turn_has_no_tools():
    r = ChatResponse({"choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}]})
    assert not r.wants_tools
    assert r.content == "hello"
