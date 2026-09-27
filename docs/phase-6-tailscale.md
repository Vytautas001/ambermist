# Phase 6: Tailscale (task for the coding agent)

Written 2026-09-27. Nothing here has been built or run.

Read [AGENTS.md](../AGENTS.md) and [LESSONS.md](../LESSONS.md) first. The design is in
[inference-tier-plan.md](inference-tier-plan.md) §2.6 ("the plan"). **This document wins
wherever the two differ.** The operator moved this phase ahead of Phases 4 and 5.

## Goal

Lab machines at another site call the API over Tailscale, encrypted, at a name that
survives rebuilds: `http://ambermist-h200:8080/v1`. Public port 8080 is closed, so the API
key can no longer be sent in plaintext over the internet. Everything else stays as it is.

## Where things stand

- Phases 1–3 run on a spot H200 in FIN-02 (see README.md and LESSONS.md). nginx listens on
  `0.0.0.0:8080` and proxies to llama-server on `127.0.0.1:8081`.
- The firewall comes from `infra/compute/boot.sh.tftpl` and admits `tcp/22` and `tcp/8080`
  from `admin_cidrs` only. The lab's public IP is already in `admin_cidrs`.
- No Prometheus, no SOPS, no `make` targets (LESSONS.md, "Decisions").

## Before you start

The operator provides:

- **A tailnet** with MagicDNS on, and the operator's login for `group:amb-admin`.
- **The tailnet policy** from `ops/tailnet-policy.hujson` (step 1), applied in the admin
  console. The default policy allows everything, so replace or narrow it; if the tailnet
  has other devices, merge these entries into it instead of replacing it.
- **A node auth key**, reusable, ephemeral, pre-approved, tagged `tag:ambermist`, 90-day
  expiry, in `.env` as `TS_AUTHKEY`, with its expiry date in a comment. Never print it.
- **Lab clients** running Tailscale, tagged `tag:ambermist-consumer`, each with the API
  key. They need outbound TCP 443 and UDP; nothing inbound.

## Scope

Build only these:

| File | Change |
| :-- | :-- |
| `ops/tailnet-policy.hujson` | New: the policy in step 1. |
| `infra/compute/boot.sh.tftpl` | The new ruleset (step 2). |
| `node/bootstrap.sh` | New `tailscale` stage (step 3); a retry in the `nginx` stage's health check (step 4). |
| `ops/provision.sh` | Push `TS_AUTHKEY`; run `tailscale` after `packages`; print the tailnet URL (step 5). |
| `.env.example` | `TS_AUTHKEY=` placeholder with a comment on the key's properties. |
| `README.md`, `LESSONS.md` | Step 8. |

Don't build these:

- nginx bound to the tailnet address, `ip_nonlocal_bind`, `/etc/ambermist/ts-ip` (plan
  §2.6). The firewall is enough: nginx keeps `0.0.0.0:8080`.
- `--close-public-ssh`: public `tcp/22` from `admin_cidrs` stays; provisioning uses it.
- A lab gateway or subnet router, Tailscale SSH, Tailscale API automation (device removal,
  IP pinning), key-expiry checks, `make` targets, SOPS, Prometheus, new test scripts.

## Steps

1. **Tailnet policy** (`ops/tailnet-policy.hujson`). The plan §2.6 policy without
   `tcp:9090`:

   ```hujson
   {
     "groups":    { "group:amb-admin": ["<operator login>"] },
     "tagOwners": {
       "tag:ambermist":          ["group:amb-admin"],
       "tag:ambermist-consumer": ["group:amb-admin"]
     },
     "grants": [
       { "src": ["tag:ambermist-consumer"], "dst": ["tag:ambermist"], "ip": ["tcp:8080"] },
       { "src": ["group:amb-admin"],        "dst": ["tag:ambermist"], "ip": ["tcp:22", "tcp:8080"] }
     ]
   }
   ```

   `tag:ambermist` is never a source, so the node can't open connections into the tailnet.
   Hand the file to the operator; you can't apply it.

2. **Firewall** (`boot.sh.tftpl`). Change the `input` chain's accept rules to:

   ```nft
   ip saddr @admin_pub tcp dport 22 accept          # SSH for provisioning
   iifname "tailscale0" tcp dport { 22, 8080 } accept # the tailnet policy decides who
   udp dport 41641 accept                            # tailscaled direct connections
   ```

   Public `tcp/8080` is gone. The tailnet carries IPv4 and IPv6; `iifname` covers both.
   tailscaled adds its own netfilter rules in the `ip`/`ip6` tables; a packet must pass
   those and `inet amb`. Never flush the ruleset.

   **The boot script's hash is in `terraform_data.identity`, so this change replaces the
   running instance on the next `apply`** (new public IP, re-provision ~10 min). Show the
   plan to the operator and get an OK before applying. If no spot H200 is free in FIN-02,
   stop and ask; never switch to on-demand on your own.

3. **`tailscale` stage** (`node/bootstrap.sh`), after `packages`, safe to rerun:
   - Install `tailscale` from Tailscale's apt repo for noble:
     `https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg` →
     `/usr/share/keyrings/tailscale-archive-keyring.gpg`, and
     `https://pkgs.tailscale.com/stable/ubuntu/noble.tailscale-keyring.list` →
     `/etc/apt/sources.list.d/tailscale.list`. No `curl | sh`.
   - If `tailscale status --json | jq -r .BackendState` is `Running`, skip joining.
     Otherwise run
     `tailscale up --auth-key=file:/run/ambermist/ts-authkey --hostname=ambermist-h200 --accept-dns=false`.
     The key never goes in argv. If the installed version doesn't accept `file:`, stop and
     report.
   - Delete `/run/ambermist/ts-authkey` after a successful join.
   - `tailscale status --json | jq -r .Self.DNSName` must start with `ambermist-h200.`.
     Anything else (e.g. `ambermist-h200-1`) means a stale device holds the name: fail with
     a message telling the operator to remove it in the admin console, then rerun.
   - Print the DNS name and `tailscale ip -4`.

4. **`nginx` stage.** Replace the single `curl` after `reload-or-restart` with a retry of
   up to 10 s. Reload is asynchronous, so on a new node the check can run before nginx
   has opened 8080.

5. **`provision.sh`:**
   - Require `TS_AUTHKEY` (from `.env`, like `AMB_API_KEY`). Push it on stdin to
     `/run/ambermist/ts-authkey`, root, 0600. Never in argv, tfvars, state, or the boot
     script.
   - Default stages: `packages tailscale disks model+build t0 serve nginx`.
   - At the end, print `http://<DNS name without the trailing dot>:8080/v1` and the short
     form `http://ambermist-h200:8080/v1`. Drop the public-URL line.

6. **Apply and provision**, after the operator's OK:

   ```bash
   set -a; source .env; set +a
   python3 ops/fleet-check.py --pick-site          # exit 1 = another H200: stop
   tofu -chdir=infra/compute plan                  # expect the instance to be replaced
   tofu -chdir=infra/compute apply
   tofu -chdir=infra/compute apply -refresh-only   # until public_ip is set
   ops/provision.sh "$(tofu -chdir=infra/compute output -raw public_ip)"
   python3 ops/fleet-check.py                      # report any detached ambermist-h200-os
   ```

7. **Check it works** (commands, no new scripts):
   - On the node: `nft list table inet amb` shows the step 2 rules; `ss -ltn` is unchanged
     (nginx `0.0.0.0:8080`, llama-server `127.0.0.1:8081`).
   - From the workstation: `nc -vz -w 5 <public_ip> 8080` times out; `nc -vz -w 5
     <public_ip> 22` connects.
   - From a lab client: `curl http://ambermist-h200:8080/health` → 200, and one chat
     completion with the key returns an answer. `nc -vz -w 5 ambermist-h200 22` fails
     (consumers get 8080 only).
   - `tailscale ping ambermist-h200` from a lab client: record whether the path is direct
     or via DERP.

8. **Record and document.**
   - LESSONS.md, marked *verified* and dated: Tailscale version; whether `--auth-key=file:`
     worked; join time; direct or DERP from the lab; whether tailnet traffic needed
     anything beyond the step 2 rules; the device name.
   - README.md: the API is `http://ambermist-h200:8080/v1` on the tailnet; public 8080 is
     closed. Teardown becomes `ssh root@<public_ip> tailscale logout` **before**
     `tofu -chdir=infra/compute destroy`, so the ephemeral device frees the name at once.
     After a spot reclaim, remove the stale device in the admin console before
     re-provisioning.

## Stop and report; don't work around

- Another H200 exists, or no spot H200 is free at the volume's site.
- The node registers under any name other than `ambermist-h200`.
- `tailscale up` can't read the key from a file.
- Tailnet traffic to 8080 is dropped with the step 2 rules in place. Report both rulesets;
  don't open 8080 publicly or disable a firewall to make it work.

## Done when

- A lab client gets 200 from `http://ambermist-h200:8080/health` and an answer from a chat
  completion, and public `tcp/8080` times out.
- LESSONS.md and README.md are updated as in step 8.
- The operator has been told about any orphaned OS volume.
