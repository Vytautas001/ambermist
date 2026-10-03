# The lab gateway

One host carries Tailscale for the whole lab and fronts the inference tier on the lab
network. Lab clients run no Tailscale and need no tailnet address.

```
lab client  --HTTP-->  gateway :8080  --WireGuard-->  ambermist-h200:8080  -->  llama.cpp :8081
(no Tailscale)         nginx, lab side only          (the tailnet)                (on the H200)
```

Why one gateway rather than Tailscale on every client:
[ADR 0006](adr/0006-lab-gateway-for-llm-access.md). The short version is that the lab's
addresses live inside `100.64.0.0/10`, the range Tailscale claims, so every machine that
joins has to disable its anti-spoofing rule. A gateway means one machine does, not all of them.

Current gateway: **kali-caldera** at `100.66.6.130`, tagged `tag:ambermist-consumer`.

## Setting it up

[`ops/lab/gateway.sh`](../ops/lab/gateway.sh) does the whole configuration and is safe to
rerun. Copy it to the gateway and run it as root:

```bash
scp ops/lab/gateway.sh amber@100.66.6.130:/tmp/gateway.sh
ssh amber@100.66.6.130 'sudo bash /tmp/gateway.sh'
```

It takes stages, defaulting to all four. Addresses are overridable with `LAB_IP`,
`LAB_CIDR`, `LAB_DNS`, `LAB_DOMAIN`, `H200` and `PORT`.

| Stage | What it does |
| --- | --- |
| `netfilter` | `tailscale set --netfilter-mode=off`. Required: Tailscale's anti-spoof rule drops every reply whose source is in `100.64.0.0/10`, which is the entire lab. See the ADR for what this costs. |
| `dns` | Writes `/etc/systemd/resolved.conf.d/ambermist-lab.conf` with the lab resolvers, points `/etc/resolv.conf` at the systemd-resolved stub, and sets `--accept-dns=true`. The stub is what makes tailscaled publish MagicDNS as a routing-only domain on `tailscale0` instead of overwriting `resolv.conf` wholesale. |
| `nginx` | Installs the `ambermist-gateway` site, removes the default site, enables and reloads nginx. |
| `verify` | Resolves the H200 and the lab domain, checks the listener, and fetches `/health` through the gateway. |

**Rerun `netfilter` and `dns` after any `tailscale up`.** That command resets prefs it isn't
given, so it silently undoes both.

## Using it from a lab client

Nothing to install. Point the client at the gateway instead of the MagicDNS name:

```
http://100.66.6.130:8080/v1
```

The client still sends `Authorization: Bearer $AMB_API_KEY`; the gateway passes it through
untouched and the H200 validates it. The model alias is `reasoner`, as on the H200.

```bash
curl --fail http://100.66.6.130:8080/health
curl --fail http://100.66.6.130:8080/v1/models -H "Authorization: Bearer $AMB_API_KEY"
```

Only `/health`, `/v1/chat/completions` and `/v1/models` are proxied; everything else is 404,
mirroring the H200's own nginx. Requests from outside `100.66.6.0/24` are refused. The
concurrency cap stays on the H200 (12 concurrent, then 429), so the whole lab shares it.

Logs: `/srv/logs/nginx/gateway.access.json.log`, JSON, no bodies and so no prompt content.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `502` from `/health` or `/v1/*` | The gateway cannot reach the H200. Usually the node is not joined to the tailnet: `tailscale status` lists no peer. Check the admin console for a stale device holding the name. |
| `404` on a `/v1` path | Not in the allowlist. Only `chat/completions` and `models` are proxied. |
| Connection refused from a client | Source outside `100.66.6.0/24`, or nginx is not running. |
| nginx fails to start after a reboot | It binds `100.66.6.130:8080` explicitly; if eth0's address changed, update `LAB_IP` and rerun the `nginx` stage. |

On the gateway itself:

| Symptom | Cause |
| --- | --- |
| Lab hosts unreachable, `1.1.1.1` fine | Tailscale's anti-spoof rule is back. Rerun the `netfilter` stage. `sudo iptables -L ts-input -v -n` shows the rule and its packet counter. |
| `nslookup` returns `REFUSED` | systemd-resolved has no upstream. Rerun the `dns` stage. |
| `resolvectl status` shows `resolv.conf mode: foreign` | tailscaled overwrote `/etc/resolv.conf` because it was not the stub symlink, and is now looping against systemd-resolved. Rerun the `dns` stage. |

A healthy gateway looks like this — note which link answers what:

```
$ resolvectl query google.com                     ... -- link: eth0
$ resolvectl query ambermist-h200.<tailnet>.ts.net ... -- link: tailscale0
```

## Status

Verified on 2026-10-01: netfilter, DNS split, the nginx site, the allowlist, the source
restriction and JSON logging. **Not verified:** the leg from the gateway to the H200, because
the node was not joined to the tailnet at the time. `/health` through the gateway returns 502
until it is.
