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

Run in Bash inside WSL.

1. Check the VPN PID and interfaces. Reuse an existing connection:

   ```bash
   pgrep -x openconnect
   ip -br -4 addr
   ```

2. If no OpenConnect process is running, connect and enter passwords when asked:

   ```bash
   sudo openconnect --background --protocol=gp \
     --authgroup=AmberMIST --user v.kasparavicius \
     https://gate.ambermist.lt
   ```

   Check again with `pgrep -x openconnect` and `ip -br -4 addr`.
   Match the IP in OpenConnect's `Configured as` message to the interface.
   **Do not assume `tun1`: the latest session used `tun0`.**
   The background PID is also printed as `Continuing in background; pid ...`.

3. Enter that interface name to route the lab subnet:

   ```bash
   read -r -p "VPN interface from the check above (e.g. tun0): " VPN_IF
   sudo ip route replace 100.66.6.0/24 dev "$VPN_IF"
   ip route get 100.66.6.130
   ```

   The result must show the selected VPN interface. A default route through
   that interface already carries lab traffic, even without an explicit `/24`.

After WSL shutdown/reboot, repeat these steps; passwords and routes are not saved
by this setup. To disconnect, check `pgrep -x openconnect`, then run
`sudo kill -INT <PID>` using the correct VPN PID.
