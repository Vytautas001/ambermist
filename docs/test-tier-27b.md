# 27B test tier: first-time bring-up

Cheap test tier serving `orcarouter/Qwen3.8-27B-Uncensored-GGUF` (Q4_K_M, a single
16,810,714,496-byte file) on whichever GPU is available most cheaply. Separate stacks and a
separate volume from the production Flash-Next tier, which is never touched.

`ops/session.sh` covers the **production** tier only (it is hardcoded to `infra/compute` /
`infra/storage` and the prod SKUs). The test tier uses raw tofu, below.

> **Always pass `MODEL_PROFILE=27b` to `ops/provision.sh` here.** The default is `flash`;
> provisioning the test node without it installs the production profile, whose
> `VOLUME_BYTES` is 140 GiB and whose model is 119 GB.

## What the model needs

Read from the GGUF header, not assumed: `general.architecture = qwen35`, 65 blocks, full
attention every 4th layer (`full_attention_interval = 4`) with SSM state layers in between,
`key_length`/`value_length` 256, 4 KV heads, `nextn_predict_layers = 1`.

So KV is only paid on the ~16 full-attention layers:
`2 × 4 heads × 256 × 16 layers × 2 B = 64 KiB/token`. **This is KV per token in total, not
per slot** — the per-slot size is `LLAMA_CTX / LLAMA_SLOTS`, and `LLAMA_CTX` is the budget
summed across all slots. 16 GiB of KV buys **262,144 tokens in total**, however it's split:
4 slots of 65,536 and 2 slots of 131,072 both cost the same ~16 GiB. With 15.7 GiB of
weights, SSM state and graph overhead that is roughly **34–36 GiB**, which is why
`--min-vram 40` is the floor and a 48 GB card is the practical minimum.
*Confirmed 2026-10-04 on the 96 GB RTX PRO 6000: `LLAMA_CTX=262144`, `LLAMA_SLOTS=2` (2 ×
131,072/slot) measured 32,655 MiB (~31.9 GiB) total VRAM used after load — in the estimated
range.*

The pinned llama.cpp (`LLAMA_SHA=e9f824d8`) already supports `qwen35`, verified in
`src/llama-arch.cpp` and `src/llama-model.cpp`, so **no new pin and no extra rebuild**.
`build-llama.sh` detects the GPU arch itself, so any card works.

## 0. Prerequisites

```bash
cd ~/dev/ambermist
set -a; . .env; set +a
```

The HF licence for the 27B repo must be accepted by the `HF_TOKEN` account (done
2026-10-04). Without it every download returns 403.

## 1. Pick the cheapest machine that fits

```bash
python3 ops/fleet-check.py --pick-cheapest              # review the ranking
python3 ops/fleet-check.py --pick-cheapest --apply      # write the *-test tfvars
```

To constrain the choice rather than take the global cheapest — e.g. you want the 96 GB
Blackwell card specifically, or you will not accept an on-demand price:

```bash
python3 ops/fleet-check.py --pick-cheapest --prefer 1RTXPRO6000.30V --kind spot --apply
```

`--prefer` is repeatable, so `--prefer 1RTXPRO6000.30V --prefer 1A100.22V` picks the
cheaper of those two. `--kind spot` refuses to fall back to on-demand.

Writes `location` + `model_volume_size_gib` to `infra/storage-test/terraform.tfvars` and
`instance_type` + `use_spot` to `infra/compute-test/terraform.tfvars`.

Prices from the catalog are **USD** (the console quotes EUR). Availability moves minute to
minute — an A6000 at $0.333/h spot appeared and vanished within minutes of testing — so
re-run this immediately before applying. Check the chosen SKU is in the allow-list in
`infra/compute-test/variables.tf`; tofu rejects anything else, which is deliberate.

## 2. Create the volume (first time only)

```bash
tofu -chdir=infra/storage-test init
tofu -chdir=infra/storage-test plan      # expect: 1 to add, ambermist-model-test, 50 GiB
tofu -chdir=infra/storage-test apply
```

50 GiB is **Verda's NVMe block-volume minimum** (32 GiB is rejected with "Specified storage
size is too low"), ≈ **€8.70/month**. It is `keep_detached`, so it survives a spot
reclaim and a compute destroy. Unlike the prod volume it is **not** `prevent_destroy`:
re-downloading 16 GB is minutes, so it is disposable and may be recreated at a cheaper site.

## 3. Launch the instance

```bash
tofu -chdir=infra/compute-test init
tofu -chdir=infra/compute-test apply     # GPU billing starts here
tofu -chdir=infra/compute-test output
```

`owner`, `ssh_public_key_path` and `admin_cidrs` come from the `TF_VAR_*` entries in `.env`.
If `public_ip` is null right after create, refresh until it is set:

```bash
tofu -chdir=infra/compute-test apply -refresh-only
IP=$(tofu -chdir=infra/compute-test output -raw public_ip); echo "$IP"
```

## 4. Base provisioning

```bash
MODEL_PROFILE=27b ops/provision.sh "$IP" packages tailscale
```

## 5. Record VOLUME_BYTES (once per volume size)

`stage_disks` refuses to format unless exactly one blank disk matches an **exact** byte
size — the guard that stops it formatting the wrong disk. The size Verda presents is not the
size requested (prod asks for 128 GiB and gets 140 GiB), so it must be observed:

```bash
ssh -i "$SSH_KEY" root@"$IP" 'lsblk -dbnro NAME,SIZE,TYPE'
```

Put the blank disk's exact byte count into `VOLUME_BYTES` in `node/pins/model.27b.conf`,
then:

```bash
MODEL_PROFILE=27b ops/provision.sh "$IP" disks
```

## 6. Seed the weights manifest (once)

`fetch-model.sh` verifies against `node/pins/qwen38-27b-uncensored-q4km.sha256`, but that
manifest can only be produced from the downloaded file — so the first download is done by
hand and the manifest recorded from it.

```bash
M=/srv/models/qwen38-27b-uncensored-q4km
F=Qwen3.8-27B-Uncensored-Q4_K_M.gguf
R=fc437a3374c9977bdc339a8ec106dcd9a0357001

ssh -i "$SSH_KEY" root@"$IP" "install -d -m 0755 $M && curl --fail -L --retry 5 \
  --retry-all-errors -H '@/run/ambermist/hf-auth-header' -o $M/$F \
  https://huggingface.co/orcarouter/Qwen3.8-27B-Uncensored-GGUF/resolve/$R/$F"
```

Confirm the size is **exactly 16810714496** (that figure comes from the HF API, so a match
means the transfer is complete), then record the digest:

```bash
ssh -i "$SSH_KEY" root@"$IP" "stat -c %s $M/$F && sha256sum $M/$F" 
# then write one line "<sha256>  16810714496  Qwen3.8-27B-Uncensored-Q4_K_M.gguf" into
# node/pins/qwen38-27b-uncensored-q4km.sha256
```

If the size differs, delete the file and retry — do not record the digest.

## 7. Verify, build, gate, serve

```bash
MODEL_PROFILE=27b ops/provision.sh "$IP" model+build   # "model: have" then the sm_<arch> build
MODEL_PROFILE=27b ops/provision.sh "$IP" t0            # asserts general.architecture = qwen35
MODEL_PROFILE=27b ops/provision.sh "$IP" serve nginx
```

Check what actually fits before trusting the slot count:

```bash
ssh -i "$SSH_KEY" root@"$IP" 'nvidia-smi --query-gpu=memory.used,memory.total --format=csv'
```

If it will not fit, lower `LLAMA_CTX` and/or `LLAMA_SLOTS` in `node/serving.27b.conf` only —
never in the flash profile. What matters for whether a request fits is the **per-slot**
size, `LLAMA_CTX / LLAMA_SLOTS`, not `LLAMA_CTX` alone; lowering `LLAMA_SLOTS` without also
lowering `LLAMA_CTX` makes each slot *larger*, not smaller.

## VS Code client

VS Code's `chatLanguageModels.json` sets its own `maxInputTokens`/`maxOutputTokens` for the
`ambermist` entry and does not read them from the server. Those two must sum to less than
the per-slot size (`LLAMA_CTX / LLAMA_SLOTS`) above, with room left for template overhead —
reasoning counts as output. As of 2026-10-04, with a 131,072-token slot, the entry is set to
`maxInputTokens: 110000`, `maxOutputTokens: 16000` (see docs/27b-agent-loop.md step 4). Also
note: VS Code's agent mode has been observed sending its own `temperature` (0.1, overriding
the server's sampling default) on at least some requests; the client config has no field to
change this, so it isn't adjustable from this repo.

## 8. Acceptance

Public `tcp/8080` is **closed by the firewall** in `infra/compute-test/boot.sh.tftpl`: the
input chain is `policy drop` and 8080 is accepted only on `iifname "tailscale0"`, even
though nginx listens on `0.0.0.0:8080`. Pointing `--base` at `http://$IP:8080` times out by
design -- that timeout is phase 6's acceptance criterion, not a fault. Use the tailnet:

```bash
AMB_API_KEY="$AMB_API_KEY" python3 ops/accept/t1.py --base "http://ambermist-h200:8080" --runs 5
```

**The node answers to `ambermist-h200`, not `ambermist-test`.** `bootstrap.sh` hardcodes
`--hostname=ambermist-h200` in `stage_tailscale` and aborts if the registered name does not
match, so the test node takes the production name and its `tag:ambermist` grants. Benign
while prod is down; it will collide the next time the H200 is brought up.

With Tailscale running on **Windows**, WSL needs no tailscale of its own: WSL resolves the
tailnet through the Windows DNS proxy and the host forwards the CGNAT range. Verified
2026-10-04 -- `getent hosts ambermist-h200` returned 100.104.106.6 and `/health` returned
200 straight from WSL. If a future WSL release breaks that, fall back to an SSH tunnel
(SSH is admitted from `admin_cidrs`, and the tunnel exits on the node's loopback, which
`iif lo accept` covers):

```bash
ssh -i "$SSH_KEY" -N -L 8080:127.0.0.1:8080 root@"$IP" &   # leave running
AMB_API_KEY="$AMB_API_KEY" python3 ops/accept/t1.py --base "http://127.0.0.1:8080" --runs 5
```

**This is the real gate.** Tool-call parsing depends on the chat template, and this model's
is different from Flash-Next's; LESSONS.md already records plain-text-instead-of-tool-call
failures on this test. A 27B that fails T1 is not usable for testing.

## 9. Teardown

```bash
tofu -chdir=infra/compute-test destroy          # stops GPU billing; volume survives
tofu -chdir=infra/storage-test destroy          # only if you want the €6.40/month back
```

Confirm the production stack is undisturbed:

```bash
tofu -chdir=infra/storage plan -refresh=false   # expect: No changes
```

## Record afterwards

Into LESSONS.md: the SKU and price/h actually paid, load time, VRAM used, the slot count
that fit, the observed `VOLUME_BYTES`, and the T1 result.
