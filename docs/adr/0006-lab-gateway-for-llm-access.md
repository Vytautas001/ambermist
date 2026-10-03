# 0006. A lab gateway reaches the LLM, not every lab client

Status: **Accepted** (2026-10-01). Reverses a non-goal recorded in
[phase-6-tailscale.md](../phase-6-tailscale.md) ("Don't build these: a lab gateway or subnet
router"). Numbering continues from the ADRs at `archive/attempt-1`, which end at 0005.

## Context

Phase 6 put the API on the tailnet and said every lab client joins Tailscale itself, tagged
`tag:ambermist-consumer`. That assumption does not survive contact with this lab's addressing.

The lab LAN is `100.66.6.0/24` and its resolvers are `100.100.100.26` and `100.100.100.28`.
All of that sits inside `100.64.0.0/10`, the range Tailscale claims for the tailnet. On
joining, tailscaled installs an anti-spoofing rule:

```
-A ts-input -s 100.64.0.0/10 ! -i tailscale0 -j DROP
```

Every reply from the lab LAN therefore arrives with a source address Tailscale drops.
Measured on the Kali client 2026-10-01: the default gateway and both resolvers were
100% unreachable while `1.1.1.1` answered normally over the same path, and the rule's
packet counter advanced with each dropped reply. Routing was never at fault —
`ip route get 100.100.100.26` correctly returned `via 100.66.6.200 dev eth0`.

The only setting that lets the two coexist is `--netfilter-mode=off`, which removes the
anti-spoofing protection for the whole range. Under the per-client model, **every lab
machine** must carry that downgrade. A second cost compounds it: tailscaled takes over
routes, netfilter chains and `/etc/resolv.conf`, and the lab's hosts run network-aware
tooling that contends for exactly those. One such collision cost a full debugging session.

## Decision

One host acts as the lab's gateway to the inference tier. It runs Tailscale on the tailnet
side and nginx on the lab side; lab clients speak plain HTTP to it and run no Tailscale.

- **Application-layer proxy, not a subnet router.** `proxy_pass` to the MagicDNS name,
  re-resolved per request through the systemd-resolved stub.
- **The gateway stores no credential.** Clients send their own `Authorization` header and
  the H200 validates it, unchanged.
- **Access control is the lab subnet.** nginx binds the lab address and allows
  `100.66.6.0/24` only; the tailnet side stays outbound.
- **Kali holds the role for now.** It is already enrolled, configured and verified, and has
  been up continuously since installation on 2026-08-24, so it behaves as a long-lived host
  rather than a reverted one.
- **The configuration is a script**, [`ops/lab/gateway.sh`](../../ops/lab/gateway.sh), not
  hand edits, so restoring the gateway after a rebuild is one command.

## Consequences

Good:

- Exactly one host disables its anti-spoofing rule, instead of every lab machine.
- Lab clients are untouched by Tailscale: no CGNAT collision, no MagicDNS, no resolver
  takeover, nothing to re-fix when a network tool rewrites their routes.
- One device in the tailnet, so the policy in `ops/tailnet-policy.hujson` needs only the
  gateway tagged `tag:ambermist-consumer`.
- The H200's address may change on rebuild without touching any client, because only the
  gateway resolves the MagicDNS name.

Bad, and accepted deliberately:

- **The lab leg is plaintext HTTP.** Only gateway→H200 is WireGuard-encrypted. In a lab that
  deliberately hosts compromised machines, prompts, responses and client API keys cross the
  LAN in clear. Mitigate with TLS on the gateway when that matters.
- **Per-client identity is lost at the tailnet.** The H200 sees one source. Attribution moves
  to the gateway's access log, `/srv/logs/nginx/gateway.access.json.log`.
- **Concurrency is shared.** The H200 caps at 12 concurrent and then returns 429; the whole
  lab now shares that budget as one client. The cap stays upstream so there is one place
  that owns the policy.
- **The gateway is a single point of failure** for lab LLM access.
- **Spoof protection is gone for the whole `100.64.0.0/10` range on the gateway**, not just
  the lab's part of it. Narrowing it to a custom chain that exempts only `100.66.6.0/24`
  and `100.100.100.0/24` is possible and was not done.

## Alternatives rejected

- **Per-client Tailscale** (the Phase 6 design): multiplies the netfilter downgrade across
  every machine and puts tailscaled on hosts whose tooling fights it.
- **Tailscale subnet router:** the natural instinct, and wrong here. The H200's tailnet
  address does not survive a rebuild, so an advertised `/32` goes stale; anything broader
  means advertising a prefix inside `100.64.0.0/10`, which is also the clients' own LAN
  range. Subnet routers also need the netfilter rules this host must disable.
- **A dedicated minimal gateway VM:** better isolation, but a CALDERA host has to be built
  either way, and putting CALDERA on the new machine is strictly less work than moving the
  already-working Tailscale configuration off Kali. Revisit if Kali starts being reverted
  from snapshots, or if a tool on it breaks tailscaled's routing.
- **The gateway injecting `AMB_API_KEY`:** concentrates the credential in one place, but
  that place would be the lab's attack box, with the broadest tooling and exposure. Clients
  keeping their own key preserves the model in README and leaves the gateway with no secret
  worth stealing.
