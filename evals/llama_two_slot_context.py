#!/usr/bin/env python3
"""Measure two concurrent long-context llama.cpp chat requests.

The script uses llama-server's own template and tokenizer endpoints, prepares
two distinct synthetic conversations, then streams both requests concurrently.
It never submits tools or executes model generated actions.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import statistics
import time
import urllib.request
from typing import Any


PAD = "exercise observation: all monitored services remain nominal. "
SESSIONS = (
    {
        "name": "session-amber",
        "facts": (
            "SESSION AMBER opening marker: the exercise code is AMBER-731.",
            "SESSION AMBER middle marker: the affected service is ledger-amber.",
            "SESSION AMBER closing marker: the recovery phrase is cedar-lantern.",
        ),
        "question": "Return the three SESSION AMBER markers in order, exactly as recorded.",
    },
    {
        "name": "session-cobalt",
        "facts": (
            "SESSION COBALT opening marker: the exercise code is COBALT-284.",
            "SESSION COBALT middle marker: the affected service is archive-cobalt.",
            "SESSION COBALT closing marker: the recovery phrase is maple-comet.",
        ),
        "question": "Return the three SESSION COBALT markers in order, exactly as recorded.",
    },
)


class Server:
    def __init__(self, base_url: str, api_key: str, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.headers = {"Content-Type": "application/json"}
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers=self.headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.load(response)

    def get_json(self, path: str) -> Any:
        request = urllib.request.Request(self.base_url + path, headers=self.headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    def prompt_tokens(self, messages: list[dict[str, str]]) -> int:
        rendered = self.post_json("/apply-template", {"messages": messages})["prompt"]
        tokenized = self.post_json("/tokenize", {
            "content": rendered,
            "add_special": False,
            "parse_special": True,
        })
        return len(tokenized["tokens"])

    def prepare(self, name: str, facts: tuple[str, ...], target: int) -> tuple[list[dict[str, str]], int]:
        system = {
            "role": "system",
            "content": "You are participating in a synthetic, authorized exercise. "
            "Keep each session's facts separate and answer only from its conversation.",
        }
        question = next(item["question"] for item in SESSIONS if item["name"] == name)

        def messages(repeats: int) -> list[dict[str, str]]:
            first_gap = repeats // 2
            parts = [facts[0], PAD * first_gap, facts[1],
                     PAD * (repeats - first_gap), facts[2], question]
            return [system, {"role": "user", "content": "\n".join(parts)}]

        def count(repeats: int) -> tuple[int, list[dict[str, str]]]:
            turns = messages(repeats)
            return self.prompt_tokens(turns), turns

        # Find the closest repetition count without guessing tokenizer ratios.
        low, high = 0, max(1, target // 5)
        while count(high)[0] < target:
            high *= 2
        candidates: list[tuple[int, list[dict[str, str]]]] = []
        while low <= high:
            mid = (low + high) // 2
            measured, turns = count(mid)
            candidates.append((measured, turns))
            if measured < target:
                low = mid + 1
            elif measured > target:
                high = mid - 1
            else:
                break
        measured, turns = min(candidates, key=lambda item: abs(item[0] - target))
        if abs(measured - target) > 256:
            raise RuntimeError(f"Could not get {name} within 256 tokens of target {target}; got {measured}")
        return turns, measured

    def stream_chat(self, name: str, messages: list[dict[str, str]], max_tokens: int) -> dict[str, Any]:
        payload = {
            "model": "qwen38-test",
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        request = urllib.request.Request(
            self.base_url + "/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=self.headers,
            method="POST",
        )
        start = time.perf_counter()
        first_token: float | None = None
        content: list[str] = []
        usage: dict[str, Any] = {}
        finish_reason = ""
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta") or {}
                    text = delta.get("content") or ""
                    if text or delta.get("reasoning_content"):
                        first_token = first_token or time.perf_counter()
                    if text:
                        content.append(text)
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
        end = time.perf_counter()
        return {
            "session": name,
            "ttft_s": round(first_token - start, 3) if first_token else None,
            "duration_s": round(end - start, 3),
            "usage": usage,
            "finish_reason": finish_reason,
            "answer": "".join(content),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("LLAMA_BASE_URL", "http://127.0.0.1:8001"),
                        help="llama-server root URL (default: %(default)s)")
    parser.add_argument("--api-key", default=os.getenv("LLAMA_API_KEY", ""))
    parser.add_argument("--target-input-tokens", type=int, default=129_000)
    parser.add_argument("--max-tokens", type=int, default=1_024)
    parser.add_argument("--context-per-slot", type=int, default=131_072)
    parser.add_argument("--timeout", type=float, default=7_200)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    if args.target_input_tokens + args.max_tokens > args.context_per_slot:
        parser.error("target input plus max output must fit context-per-slot")

    server = Server(args.base_url, args.api_key, args.timeout)
    prepared: list[tuple[str, list[dict[str, str]], int]] = []
    for session in SESSIONS:
        messages, input_tokens = server.prepare(
            session["name"], session["facts"], args.target_input_tokens,
        )
        prepared.append((session["name"], messages, input_tokens))
        print(f"prepared {session['name']}: {input_tokens} input tokens", flush=True)

    start = time.perf_counter()
    slot_samples: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(server.stream_chat, name, messages, args.max_tokens)
                   for name, messages, _ in prepared]
        while not all(future.done() for future in futures):
            try:
                slots = server.get_json("/slots")
                if isinstance(slots, list):
                    busy = [slot.get("id") for slot in slots if slot.get("is_processing")]
                    contexts = {str(slot.get("id")): slot.get("n_ctx") for slot in slots}
                    slot_samples.append({"elapsed_s": round(time.perf_counter() - start, 2),
                                         "busy_slots": busy,
                                         "slot_context_tokens": contexts})
            except Exception as exc:  # report observability failures with the trial
                slot_samples.append({"elapsed_s": round(time.perf_counter() - start, 2),
                                     "error": f"{type(exc).__name__}: {exc}"})
            time.sleep(1)
        results = []
        for (name, _, _), future in zip(prepared, futures):
            try:
                results.append(future.result())
            except Exception as exc:  # keep the other session's measurements
                results.append({"session": name, "error": f"{type(exc).__name__}: {exc}"})
    wall_s = time.perf_counter() - start

    by_name = {result["session"]: result for result in results}
    for name, _, expected_tokens in prepared:
        result = by_name[name]
        if "error" in result:
            result["prepared_prompt_tokens"] = expected_tokens
            result["prompt_usage_matches"] = False
            print(f"{name}: ERROR {result['error']}", flush=True)
            continue
        reported = result["usage"].get("prompt_tokens")
        result["prepared_prompt_tokens"] = expected_tokens
        result["prompt_usage_matches"] = reported == expected_tokens
        generation_s = result["duration_s"] - (result["ttft_s"] or result["duration_s"])
        completion_tokens = result["usage"].get("completion_tokens")
        result["decode_tokens_per_s"] = (
            round(completion_tokens / generation_s, 2)
            if completion_tokens is not None and generation_s > 0 else None
        )
        print(
            f"{name}: prepared={expected_tokens} reported={reported} "
            f"TTFT={result['ttft_s']}s duration={result['duration_s']}s "
            f"completion={completion_tokens} decode={result['decode_tokens_per_s']} tok/s",
            flush=True,
        )

    # Replay each complete conversation and first answer, then ask for recall.
    for name, messages, _ in prepared:
        result = by_name[name]
        if "error" in result:
            continue
        history = messages + [
            {"role": "assistant", "content": result["answer"]},
            {"role": "user", "content": "Review the markers recorded earlier in this same conversation. "
             "Return the opening, middle, and closing marker exactly, in order."},
        ]
        follow_tokens = server.prompt_tokens(history)
        if follow_tokens + 128 > args.context_per_slot:
            result["follow_up_error"] = (
                f"replayed history plus 128 output tokens exceeds slot context: {follow_tokens}"
            )
            continue
        try:
            follow = server.stream_chat(name + "-follow-up", history, 128)
            result["follow_up"] = follow
            result["follow_up"]["prepared_prompt_tokens"] = follow_tokens
            result["follow_up"]["prompt_usage_matches"] = (
                follow.get("usage", {}).get("prompt_tokens") == follow_tokens
            )
        except Exception as exc:
            result["follow_up_error"] = f"{type(exc).__name__}: {exc}"
        print(
            f"{name} follow-up: prepared={follow_tokens} "
            f"reported={follow.get('usage', {}).get('prompt_tokens')} "
            f"TTFT={follow.get('ttft_s')}s duration={follow.get('duration_s')}s",
            flush=True,
        )

    durations = [result["duration_s"] for result in results if "duration_s" in result]
    report = {
        "model": "qwen38-test",
        "target_input_tokens": args.target_input_tokens,
        "max_tokens": args.max_tokens,
        "context_per_slot": args.context_per_slot,
        "concurrent_wall_s": round(wall_s, 3),
        "session_duration_median_s": round(statistics.median(durations), 3) if durations else None,
        "slots_reported_both_processing": any(
            len(set(sample.get("busy_slots", []))) >= 2 for sample in slot_samples
        ),
        "observed_slot_context_tokens": sorted({
            context for sample in slot_samples
            for context in sample.get("slot_context_tokens", {}).values()
            if isinstance(context, int)
        }),
        "slot_samples": slot_samples,
        "sessions": results,
    }
    with open(args.out, "w", encoding="utf-8") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    print(f"wrote {args.out}", flush=True)
    return 0 if all(
        result.get("prompt_usage_matches")
        and ("follow_up" not in result or result["follow_up"].get("prompt_usage_matches"))
        for result in results
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
