# ambermist: instructions for coding assistants

Applies to every coding LLM (GPT/Codex, Claude, others). The latest user
request sets the scope; don't expand it.

The repository was reset on 2026-09-25. Read [LESSONS.md](LESSONS.md) before
starting work. Earlier code is at tag `archive/attempt-1`
(`git show archive/attempt-1:<path>`). Treat it as reference only; don't
restore it wholesale.

## Hard constraints

- **Fleet cap:** at most 1× H200, 1× H100, 2× RTX PRO 6000 GPUs, counting every
  held node. No other GPU families. Don't add hardware to make something fit.
- **Secrets:** never print or commit `.env`, keys, state, or secret-bearing
  output. Load credentials with `set -a; source .env; set +a`.
- **Held capacity:** don't destroy or replace running instances or the weights
  volume unless the user explicitly asks.

## Working rules

- Build the smallest thing that works, run it, then extend it. No speculative
  abstractions, dead code, or tests for code that doesn't exist.
- Don't claim something works unless it was run. Say what was verified and what wasn't.
- Commit only related work, and only when asked.

## WSL access to the lab over VPN

The user confirmed access worked after adding a host route through OpenConnect
(2026-10-03). The VPN connected but did not install a route to `100.66.6.130`;
traffic initially used WSL's default `eth0` route.
The instructions below route the full lab subnet `100.66.6.0/24`, as requested;
access to other machines in that subnet has not been verified.

Run in the WSL distribution that needs lab access. Reuse an existing VPN
connection instead of starting another. For a new connection:

```bash
sudo openconnect --background --protocol=gp \
  --authgroup=AmberMIST --user v.kasparavicius \
  https://gate.ambermist.lt
```

Enter passwords at the hidden prompts; portal and gateway may both ask.
`--background` returns the prompt after connecting. Record the printed PID
for disconnecting later. Inspect the tunnel and route:

```bash
ip -br addr
ip route get 100.66.6.130
```

The working session used `tun1` with address `172.22.11.71`; both may change.
After connecting, install the subnet route, replacing `tun1` with the actual
OpenConnect interface. `replace` also updates an existing route for this subnet:

```bash
sudo ip route replace 100.66.6.0/24 dev tun1
ip route show 100.66.6.0/24
ip route get 100.66.6.130
curl -v --noproxy '*' --connect-timeout 5 --max-time 10 \
  http://100.66.6.130:8080/health
```

The route should use the VPN interface. Keep it limited to `100.66.6.0/24`, not all of
`100.64.0.0/10`, which can conflict with Tailscale. Ping alone does not establish
HTTP access. 
The VPN and manual route do not survive WSL shutdown or Windows reboot.
Reconnect and check/add the route again. Another terminal in the same running
WSL distribution can reuse the connection. This command does not save the VPN
password or authentication cookie for the next launch: authenticate again.
Shell history may retain the command and username, but not passwords entered
at hidden prompts. Never put passwords or cookies in this file, command
arguments, or committed scripts.

To undo the manual subnet route, use `sudo ip route del 100.66.6.0/24 dev tun1`
with the actual interface. `No such process` means the route is already absent.
To disconnect, use `sudo kill -INT <PID>` with the recorded OpenConnect PID.
