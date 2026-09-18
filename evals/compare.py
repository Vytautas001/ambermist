#!/usr/bin/env python3
"""Side-by-side of bake-off result files. Flags the prefix-cache red line."""
import json, sys
from pathlib import Path

rows = []
for f in sys.argv[1:]:
    d = json.loads(Path(f).read_text())
    cs = d["cases"]
    avg = lambda k: round(sum(c.get(k) or 0 for c in cs) / max(len(cs), 1), 2)
    rows.append({
        "model": d["model"], "ctx": d["context"],
        "tok/s": avg("tok_per_s"), "ttft_warm": avg("ttft_warm_s"),
        "cache_speedup": avg("prefix_cache_speedup"),
        "refusals": sum(1 for c in cs if c.get("refused")),
    })

w = max((len(r["model"]) for r in rows), default=5)
print(f"{'model':{w}} {'ctx':>7} {'tok/s':>7} {'ttft_warm':>10} {'cache_x':>8} {'refusals':>9}")
for r in sorted(rows, key=lambda r: (-r["tok/s"])):
    flag = "  <-- PREFIX CACHE LIKELY BROKEN" if (r["cache_speedup"] or 0) < 2 else ""
    print(f"{r['model']:{w}} {r['ctx']:>7} {r['tok/s']:>7} {r['ttft_warm']:>10} "
          f"{r['cache_speedup']:>8} {r['refusals']:>9}{flag}")
