#!/usr/bin/env python3
"""Authenticated P1 smoke test; never prints credentials or executes tools."""

import json
import os
import time
import urllib.error
import urllib.request


def main():
    base = os.environ.get("VLLM_BASE_URL", "http://127.0.0.1:8000/v1").rstrip("/")
    key = os.environ["VLLM_API_KEY"]
    if not key:
        raise SystemExit("VLLM_API_KEY must not be empty")
    model = os.environ.get("VLLM_MODEL", "redcell-adversary")

    def request(path, payload=None, authenticated=True):
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(
            base + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=180) as response:
            return json.load(response)

    try:
        request("/models", authenticated=False)
    except urllib.error.HTTPError as exc:
        assert exc.code == 401, f"Expected 401, got {exc.code}"
    else:
        raise AssertionError("Unauthenticated model discovery was accepted")
    print("PASS: unauthenticated requests rejected", flush=True)

    models = request("/models")
    assert model in [item["id"] for item in models["data"]], models
    print(f"PASS: model discovery: {model}", flush=True)

    settings = {
        "model": model,
        "temperature": 0,
        "max_tokens": 128,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    start = time.monotonic()
    result = request("/chat/completions", {
        **settings,
        "messages": [{"role": "user", "content": "What is 6 times 7? Reply only with the number."}],
    })
    answer = result["choices"][0]["message"]["content"].strip()
    assert answer == "42", result
    print(f"PASS: inference returned {answer!r} in {time.monotonic() - start:.2f}s; "
          f"usage={result.get('usage')}", flush=True)

    result = request("/chat/completions", {
        **settings,
        "messages": [{"role": "user", "content": "Call report_status with status P1_OK now."}],
        "tools": [{
            "type": "function",
            "function": {
                "name": "report_status",
                "description": "Report the supplied status for this smoke test.",
                "parameters": {
                    "type": "object",
                    "properties": {"status": {"type": "string"}},
                    "required": ["status"],
                    "additionalProperties": False,
                },
            },
        }],
        "tool_choice": "auto",
    })
    calls = result["choices"][0]["message"].get("tool_calls") or []
    assert len(calls) == 1, result
    function = calls[0]["function"]
    assert function["name"] == "report_status", result
    assert json.loads(function["arguments"]) == {"status": "P1_OK"}, result
    print("PASS: automatic tool call parsed correctly (no tool executed)", flush=True)


if __name__ == "__main__":
    main()
