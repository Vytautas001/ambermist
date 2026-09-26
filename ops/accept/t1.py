#!/usr/bin/env python3
"""T1: tool-call round trip against llama-server (plan section 8). Standard library only.

  AMB_API_KEY=... t1.py [--base http://127.0.0.1:8080] [--runs 5]

Results are written to ~/ambermist-runs/<date>/t1-<time>.json. Exit 0 iff T1 passes:
every run parses correctly, and at least 4 of 5 give the right tool arguments.
"""
import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request

MODEL = "reasoner"
TOOL = {
    "type": "function",
    "function": {
        "name": "get_station_temperature",
        "description": "Current air temperature at an ICAO weather station.",
        "parameters": {
            "type": "object",
            "properties": {"station_id": {"type": "string", "enum": ["EFHK", "EFTU"]}},
            "required": ["station_id"],
        },
    },
}
QUESTION = [{"role": "user", "content": "What is the temperature at EFHK?"}]
MARKUP = ("<tool_call>", "</tool_call>", "<think>", "</think>")


def http(base, method, path, key=None, body=None, stream=False):
    headers = {"Accept": "application/json"}
    data = None
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=300)
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode(errors="replace")
    if stream:
        return resp.status, resp
    with resp:
        return resp.status, resp.read().decode()


def chat(base, key, body):
    """Return (message, finish_reason, done_seen) for a streaming or plain request."""
    if not body.get("stream"):
        status, text = http(base, "POST", "/v1/chat/completions", key, body)
        if status != 200:
            raise RuntimeError(f"HTTP {status}: {text[:300]}")
        choice = json.loads(text)["choices"][0]
        return choice["message"], choice["finish_reason"], None
    status, resp = http(base, "POST", "/v1/chat/completions", key, body, stream=True)
    if status != 200:
        raise RuntimeError(f"HTTP {status}: {resp[:300]}")
    content, reasoning, calls, finish, done = None, None, {}, None, False
    with resp:
        for raw in resp:
            line = raw.decode().strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                done = True
                break
            choice = json.loads(payload)["choices"][0]
            delta = choice.get("delta", {})
            if delta.get("content"):
                content = (content or "") + delta["content"]
            if delta.get("reasoning_content"):
                reasoning = (reasoning or "") + delta["reasoning_content"]
            for tc in delta.get("tool_calls") or []:
                slot = calls.setdefault(tc.get("index", 0), {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                slot["id"] = tc.get("id") or slot["id"]
                fn = tc.get("function") or {}
                slot["function"]["name"] += fn.get("name") or ""
                slot["function"]["arguments"] += fn.get("arguments") or ""
            finish = choice.get("finish_reason") or finish
    msg = {"role": "assistant", "content": content}
    if reasoning is not None:
        msg["reasoning_content"] = reasoning
    if calls:
        msg["tool_calls"] = [calls[i] for i in sorted(calls)]
    return msg, finish, done


def tool_round(base, key, stream):
    """Both turns of the tool-call exchange. Returns a dict of results for one run."""
    r = {"parse_ok": False, "args_ok": False, "problems": []}
    try:
        msg, finish, done = chat(base, key, {"model": MODEL, "messages": QUESTION, "tools": [TOOL],
                                             "tool_choice": "auto", "max_tokens": 4096, "stream": stream})
    except Exception as exc:
        r["problems"].append(f"turn 1 failed: {exc}")
        return r
    calls = msg.get("tool_calls") or []
    r["content_type"] = "null" if msg.get("content") is None else ("empty" if msg["content"] == "" else "text")
    r["reasoning_content_present"] = bool(msg.get("reasoning_content"))
    if stream and not done:
        r["problems"].append("stream did not end with [DONE]")
    if finish != "tool_calls":
        r["problems"].append(f"finish_reason={finish!r}")
    if len(calls) != 1:
        r["problems"].append(f"{len(calls)} tool calls")
    if any(m in (msg.get("content") or "") for m in MARKUP):
        r["problems"].append("template markup in content")
    if len(calls) != 1:
        r["content"] = msg.get("content")
        r["reasoning_tail"] = (msg.get("reasoning_content") or "")[-400:]
        return r
    call = calls[0]
    if call.get("function", {}).get("name") != "get_station_temperature":
        r["problems"].append(f"tool name {call.get('function', {}).get('name')!r}")
    if not call.get("id"):
        r["problems"].append("empty tool call id")
    try:
        args = json.loads(call["function"]["arguments"])
        r["arguments"] = args
        r["args_ok"] = args == {"station_id": "EFHK"}
    except (ValueError, KeyError):
        r["problems"].append("arguments are not JSON")
        return r

    tool_msg = {"role": "tool", "tool_call_id": call.get("id", ""), "content": json.dumps({"celsius": 17.5})}
    try:
        msg2, finish2, done2 = chat(base, key, {"model": MODEL, "messages": QUESTION + [msg, tool_msg], "tools": [TOOL],
                                                "max_tokens": 4096, "stream": stream})
    except Exception as exc:
        r["problems"].append(f"turn 2 failed: {exc}")
        return r
    if stream and not done2:
        r["problems"].append("turn 2 stream did not end with [DONE]")
    if finish2 != "stop":
        r["problems"].append(f"turn 2 finish_reason={finish2!r}")
    if msg2.get("tool_calls"):
        r["problems"].append("turn 2 returned tool calls")
    if "17.5" not in (msg2.get("content") or ""):
        r["problems"].append("turn 2 content lacks 17.5")
    if any(m in (msg2.get("content") or "") for m in MARKUP):
        r["problems"].append("template markup in turn 2 content")
    r["parse_ok"] = not r["problems"]
    return r


def arithmetic(base, key):
    try:
        msg, finish, _ = chat(base, key, {"model": MODEL, "max_tokens": 2048, "stream": False,
                                          "messages": [{"role": "user", "content": "What is 6×7? Reply with the number only."}]})
    except Exception as exc:
        return {"ok": False, "problems": [str(exc)]}
    return {"ok": "42" in (msg.get("content") or ""), "content": msg.get("content"), "finish_reason": finish,
            "reasoning_content_present": bool(msg.get("reasoning_content"))}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8080")
    ap.add_argument("--runs", type=int, default=5)
    args = ap.parse_args()
    key = os.environ.get("AMB_API_KEY")
    if not key:
        sys.exit("AMB_API_KEY not set")

    out = {"base": args.base, "started": datetime.datetime.now(datetime.timezone.utc).isoformat(), "checks": {}}
    status, text = http(args.base, "POST", "/v1/chat/completions", None,
                        {"model": MODEL, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1})
    err_type = None
    try:
        err_type = json.loads(text).get("error", {}).get("type")
    except ValueError:
        pass
    out["checks"]["no_key_401"] = status == 401 and err_type == "authentication_error"
    status, _ = http(args.base, "GET", "/health")
    out["checks"]["health_200"] = status == 200
    status, text = http(args.base, "GET", "/v1/models", key)
    try:
        ids = [m.get("id") for m in json.loads(text).get("data", [])] if status == 200 else []
    except ValueError:
        ids = []
    out["checks"]["models_lists_reasoner"] = MODEL in ids

    out["arithmetic"] = [arithmetic(args.base, key) for _ in range(args.runs)]
    out["tool_nonstream"] = [tool_round(args.base, key, False) for _ in range(args.runs)]
    out["tool_stream"] = [tool_round(args.base, key, True) for _ in range(args.runs)]

    def summary(runs):
        return {"parse_ok": sum(r["parse_ok"] for r in runs), "args_ok": sum(r["args_ok"] for r in runs), "runs": len(runs)}

    out["summary"] = {"tool_nonstream": summary(out["tool_nonstream"]), "tool_stream": summary(out["tool_stream"]),
                      "arithmetic_ok": sum(r["ok"] for r in out["arithmetic"])}
    n = args.runs
    need_args = min(4, n)
    ok = all(out["checks"].values()) and out["summary"]["arithmetic_ok"] == n
    for name in ("tool_nonstream", "tool_stream"):
        s = out["summary"][name]
        ok = ok and s["parse_ok"] == n and s["args_ok"] >= need_args
    out["pass"] = ok

    day = datetime.date.today().isoformat()
    directory = os.path.expanduser(f"~/ambermist-runs/{day}")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"t1-{datetime.datetime.now().strftime('%H%M%S')}.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(json.dumps({"checks": out["checks"], "summary": out["summary"], "pass": ok}, indent=2))
    print(f"wrote {path}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
