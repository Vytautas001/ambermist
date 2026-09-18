#!/usr/bin/env python3
"""P2 bake-off runner: drive the agentic cases against one model endpoint and
record latency + behaviour. Measures TTFT with a warm (cached) prefix, which is
the number that actually decides whether prefix caching is working."""
from __future__ import annotations

import argparse, json, os, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness" / "src"))
import yaml
from redcell.client import RedCellClient
from redcell.config import EndpointConfig, SamplingConfig
from redcell import tools


def measure_ttft(client: RedCellClient, messages, tools_schemas) -> float:
    t0 = time.perf_counter()
    client.chat(messages, tools=tools_schemas, max_tokens=1)
    return time.perf_counter() - t0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--context", type=int, default=65536)
    ap.add_argument("--cases", default=str(Path(__file__).parent / "cases" / "agentic_cases.yaml"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    ep = EndpointConfig(base_url=os.environ.get("BASE_URL", "http://localhost:8000/v1"),
                        api_key=os.environ.get("API_KEY", ""),
                        model=os.environ.get("MODEL", "redcell-adversary"))
    cases = yaml.safe_load(Path(a.cases).read_text())
    schemas = tools.schemas()
    results = {"model": ep.model, "context": a.context, "cases": []}

    with RedCellClient(ep, SamplingConfig()) as client:
        if not client.health():
            print("endpoint unreachable", file=sys.stderr); return 1
        # a stable system prompt to exercise prefix caching
        system = {"role": "system", "content": "You are a sanctioned exercise adversary. " + "context-pad " * (a.context // 20)}
        for c in cases:
            msgs = [system, {"role": "user", "content": c["objective"]}]
            cold = measure_ttft(client, msgs, schemas)
            warm = measure_ttft(client, msgs, schemas)   # same prefix -> should be far faster
            t0 = time.perf_counter()
            resp = client.chat(msgs, tools=schemas)
            gen = time.perf_counter() - t0
            out_tok = (resp.usage or {}).get("completion_tokens", 0)
            results["cases"].append({
                "id": c["id"], "ttft_cold_s": round(cold, 3), "ttft_warm_s": round(warm, 3),
                "prefix_cache_speedup": round(cold / warm, 2) if warm else None,
                "gen_s": round(gen, 3), "out_tokens": out_tok,
                "tok_per_s": round(out_tok / gen, 1) if gen else 0,
                "used_tools": [tc.get("function", {}).get("name") for tc in resp.tool_calls],
                "refused": _looks_like_refusal(resp.content),
                "content_head": (resp.content or "")[:280],
            })
            print(f"  {c['id']:22} ttft cold {cold:5.2f}s warm {warm:5.2f}s "
                  f"speedup {cold/warm if warm else 0:4.1f}x  {out_tok/gen if gen else 0:5.1f} tok/s")

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(results, indent=2))
    print(f"-> {a.out}")
    return 0


def _looks_like_refusal(text: str) -> bool:
    t = (text or "").lower()
    return any(p in t for p in ("i can't", "i cannot", "i'm not able", "i am not able",
                                "cannot assist", "won't help", "not able to help"))


if __name__ == "__main__":
    sys.exit(main())
