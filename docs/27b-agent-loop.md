# 27B reasoner loops in VS Code agent mode (task for the coding agent)

Written 2026-10-04. Nothing here has been built or run. Facts below are marked with where
they come from; "unverified" means nobody has looked yet.

Read [AGENTS.md](../AGENTS.md) and [LESSONS.md](../LESSONS.md) first. **This document wins
wherever it differs from [test-tier-27b.md](test-tier-27b.md).**

## Goal

The 27B test tier (`MODEL_PROFILE=27b`) is used from VS Code chat as the custom model
"Ambermist reasoner (27B)". In agent mode it falls into loops. Make each request fit in one
llama-server slot, set the model card's sampling values as server defaults, and tell VS Code
the real limits. Measure before and after, and record what you find.

## Where things stand

**The client** (`C:\Users\vytka\AppData\Roaming\Code\User\chatLanguageModels.json`, read
2026-10-04): the `ambermist` entry is a `customendpoint`, `apiType: chat-completions`, model id
`reasoner`, URL `http://ambermist-h200:8080/v1`, `toolCalling: true`,
**`maxInputTokens: 128000`, `maxOutputTokens: 26000`**. The file also holds API keys for other
endpoints: **never print it, `cat` it, or commit it.**

**The server** ([node/serving.27b.conf](../node/serving.27b.conf), [llama-server.service](../node/etc/systemd/system/llama-server.service)):

- `LLAMA_CTX=65536`, `LLAMA_SLOTS=4`, `--no-kv-unified`, `--no-context-shift`. `--ctx-size` is
  the total for all slots, so **each request gets 16,384 tokens** for prompt, reasoning, and
  answer together. LESSONS.md recorded `n_ctx_seq = 16384` with exactly these flags on
  Flash-Next (2026-09-26 and 09-27).
- No sampling flags, so requests that don't set them get llama.cpp's built-in defaults
  (temperature 0.8, top-k 40, top-p 0.95, min-p 0.05 in `common/common.h`; confirm at the pin).
- VS Code believes it has 128k, so it never trims history. Copilot's agent system prompt plus
  tool schemas plus history plus file contents can easily pass 16k. Past the limit the request
  either gets 400 `exceed_context_size_error` or is cut off mid-reasoning with
  `finish_reason: "length"`, and VS Code retries. *(The mechanism is inferred, not observed.)*

**The model card** (`Qwen/Qwen3.8-27B`, read 2026-10-04; the GGUF repo's card gives no
sampling values; re-read both before step 2):

- Thinking mode: `temperature=1.0, top_p=0.95, top_k=20, min_p=0.0, presence_penalty=0.0,
  repetition_penalty=1.0`. Thinking is on by default.
- `presence_penalty` between 0 and 2 is the card's knob against endless repetition; values above
  1.5 may cause language mixing.
- Native context 262,144 tokens.
- "Qwen3.8 retains thinking blocks from all historical messages": the model expects to see its
  earlier reasoning in the history. Whether VS Code sends `reasoning_content` back on assistant
  messages is **unverified**. If it doesn't, the model sees tool calls without the reasoning
  that led to them, which fits the "Need use tool... Already did" pattern in LESSONS.md.

**The sizing doc is wrong.** [test-tier-27b.md](test-tier-27b.md) "What the model needs"
computes "4 GiB per 65,536-token slot, 16 GiB at `LLAMA_SLOTS=4`". With these flags the slots are
16,384 tokens each, not 65,536. The 64 KiB/token figure stands, so 16 GiB of KV buys 262,144
tokens in total.

**Test node:** `infra/compute-test/terraform.tfvars` holds `instance_type = "1RTXPRO6000.30V"`,
`use_spot = false` (96 GB, on-demand). It answers as `ambermist-h200` on the tailnet. Whether it
is running now is unknown. T1 passed twice on 2026-10-04 (`~/ambermist-runs/2026-10-04/`), but
T1 sends one short question, one tool, and `max_tokens: 4096`, so it doesn't exercise long
sessions.

**Unverified, and step 1 answers:** what the loop looks like (repeated reasoning text, or the
same tool called again and again); the prompt sizes VS Code sends; whether VS Code sets its own
`temperature`/`top_p`, which would override server defaults; whether it sends
`reasoning_content` back.

## Scope

| File | Change |
| :-- | :-- |
| `node/serving.27b.conf` | Slot size and sampling (step 2). |
| `chatLanguageModels.json` (Windows, not in the repo) | `maxInputTokens`, `maxOutputTokens` of the `ambermist` entry only (step 4). |
| `docs/test-tier-27b.md` | Fix the KV sizing, add the client limits (step 6). |
| `LESSONS.md` | Step 6. |

Don't build these: changes to `serving.flash.conf`, `model.*.conf`, or `llama-server.service`;
nginx changes; a proxy that injects sampling or `reasoning_content`; a chat template override;
a new llama.cpp pin; `--reasoning-budget` or turning thinking off; switching the model or quant;
committing request bodies or logs. If the fix here isn't enough, report it. A design session
decides the next step.

## Steps

0. **Node.** `tofu -chdir=infra/compute-test output`. If no instance is running, stop and ask the
   operator before `apply`: GPU billing starts there, and the SKU is on-demand. With a running
   node, set `IP=$(tofu -chdir=infra/compute-test output -raw public_ip)`. Every
   `ops/provision.sh` call in this doc takes `MODEL_PROFILE=27b`; without it, the flash profile
   is installed.

1. **Measure before changing anything.** The operator reproduces the loop in VS Code agent mode
   while you watch the node:

   ```bash
   ssh -i "$SSH_KEY" root@"$IP" 'tail -f /srv/logs/llama/server.log'
   ```

   Record for each request: prompt tokens and generated tokens (the per-task timing lines
   `prompt eval time … / N tokens` and `eval time … / M tokens`; the format at the pin is
   unverified), any `exceed_context_size` errors, and the stop reason. Ask the operator what
   the loop looked like. Also read the effective slot settings during a request:

   ```bash
   ssh -i "$SSH_KEY" root@"$IP" 'curl -s -H "Authorization: Bearer $(head -n1 /etc/ambermist/llama-api-keys)" \
     http://127.0.0.1:8081/slots | jq "[.[] | {id, n_ctx, params: (.params | {temperature, top_k, top_p, min_p, presence_penalty})}]"'
   ```

   If `jq` returns nulls, the field names differ at the pin; read the raw JSON. A busy slot
   shows the params VS Code sent, so this answers whether VS Code sets its own sampling.

   **Optional, one attempt:** to learn whether VS Code sends `reasoning_content` back, set
   `LLAMA_EXTRA_ARGS="-lv 5"` in `serving.27b.conf`, run `serve`, and look for a multi-turn
   request body in the log. If the body isn't there, record "unknown" and move on; don't add
   nginx body logging. Remove `-lv 5` before step 2. The log then holds the operator's chat, so
   don't copy it off the node.

2. **Server change.** First confirm the model's trained context:

   ```bash
   ssh -i "$SSH_KEY" root@"$IP" 'jq ".metadata[\"qwen35.context_length\"].value" /srv/logs/bootstrap/gguf-meta.json'
   ```

   It must be at least 131072 (the card says 262,144). Then set in `node/serving.27b.conf`:

   ```bash
   LLAMA_CTX=262144
   LLAMA_SLOTS=2
   LLAMA_EXTRA_ARGS="--temp 1.0 --top-k 20 --top-p 0.95 --min-p 0 --presence-penalty 0"
   ```

   - **2 × 131,072:** about 16 GiB of KV in total, the amount `test-tier-27b.md` already budgets.
     Two slots rather than one so VS Code's side requests (titles, summaries; likely, not
     verified) don't queue behind a long agent turn.
   - **The quotes are required.** `bootstrap.sh serve` sources this file with bash, where an
     unquoted value with spaces fails under `set -e`. systemd's `EnvironmentFile` strips the
     quotes, and the unit's unbraced `$LLAMA_EXTRA_ARGS` splits the value into separate
     arguments (LESSONS.md, Phases 2 and 3).
   - Use the sampling values from the card as you re-read it, if they differ from the above.
   - Rewrite the comment block in the file so it states the per-slot size
     (`LLAMA_CTX / LLAMA_SLOTS`) and where the sampling values come from.

3. **Deploy and check the server.**

   ```bash
   MODEL_PROFILE=27b ops/provision.sh "$IP" serve
   ssh -i "$SSH_KEY" root@"$IP" 'nvidia-smi --query-gpu=memory.used,memory.total --format=csv'
   ```

   - `/slots` (command from step 1) shows 2 slots, `n_ctx` 131072 each, and the step 2 sampling
     values on an idle slot.
   - T1 still passes:
     `AMB_API_KEY="$AMB_API_KEY" python3 ops/accept/t1.py --base "http://ambermist-h200:8080" --runs 5`.
   - One long request, run as an inline script and not committed: a prompt of roughly 60,000
     tokens of filler text ending in a short question, `max_tokens: 2048`. Expect 200, not 400.
     Record the prompt token count it reports.

4. **Client limits.** In `/mnt/c/Users/vytka/AppData/Roaming/Code/User/chatLanguageModels.json`,
   change only the `ambermist` entry: `maxInputTokens: 110000`, `maxOutputTokens: 16000`. The two
   must add up to less than the slot (131,072), with room for template overhead; reasoning counts
   as output. Make a targeted edit (e.g. a short Python script that loads the JSON, changes the two
   fields, and writes it back with the same indentation). Don't print the file. Ask the operator to
   reload VS Code.

5. **Retest in VS Code.** The operator repeats the step 1 task while you watch the log and
   `/slots` again. Record the same numbers as in step 1.

   If it still loops while prompts are well under the slot and requests end normally, raise
   `--presence-penalty` to 1.0, then 1.5, repeating step 3 (serve, T1) and this step each time.
   Don't go above 1.5 (card: language mixing). Still looping at 1.5: put it back to 0 and stop,
   see below.

6. **Record and document.**
   - LESSONS.md, a dated section for the 27B test tier, marked *verified* or *unverified*:
     everything from step 1 (loop shape, prompt sizes, stop reasons, whether VS Code sends
     sampling and `reasoning_content`); `context_length` from the GGUF; VRAM used at 2 × 131,072;
     T1 and long-request results; the step 5 outcome and the final `presence_penalty`. Add the
     test-tier facts `test-tier-27b.md` "Record afterwards" asks for, where they're known.
   - `test-tier-27b.md`: fix "What the model needs" (per-slot size is `LLAMA_CTX / LLAMA_SLOTS`;
     16 GiB of KV buys 262,144 tokens in total, so 2 × 131,072). Fix the step 7 note on lowering
     slots so it talks about per-slot size. Add a short "VS Code client" note: the client's
     `maxInputTokens + maxOutputTokens` must stay under the per-slot size, with the values from
     step 4.

## Stop and report; don't work around

- No test node is running and launching one needs the operator's OK.
- `context_length` is below 131072.
- `serve` fails or VRAM doesn't fit at 2 × 131,072. Try once with `LLAMA_CTX=131072`
  (2 × 65,536) and client limits `maxInputTokens: 50000`, `maxOutputTokens: 12000`. If that fails
  too, stop.
- T1 fails after step 2. Put the sampling back to the defaults (keep the slot change), run
  `serve`, and run T1 again. Report either way.
- VS Code sends its own sampling values that override the server's. Record them; don't build
  anything to override them.
- Still looping at `presence_penalty` 1.5.
- Any plan or change that touches the production stacks or the flash profile.

## Done when

- `node/serving.27b.conf` sets 2 slots of 131,072 tokens (or the fallback) and the card's
  sampling values, with a correct comment. The flash profile is unchanged.
- On the node, `/slots` shows the new slot size and sampling, and T1 passes.
- The VS Code entry's limits fit the slot.
- The operator's retest either no longer loops, or the report says it still does and with
  what numbers.
- LESSONS.md and `test-tier-27b.md` are updated as in step 6.
