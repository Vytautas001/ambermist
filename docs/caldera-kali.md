# CALDERA with a remote LLM through the lab gateway

Run these commands on the CALDERA host in Bash, as your normal user. If your terminal uses
Zsh or another shell, run `bash` once and wait for its prompt before pasting the
command blocks. The `bash` label on a Markdown code fence only controls syntax
highlighting; it does not select the shell. Kali adopted Zsh as its default for
new desktop installations in [2020.4](https://www.kali.org/blog/kali-linux-2020-4-release/).

This guide is for a fresh CALDERA installation on any lab host. That host does **not** join
Tailscale: it reaches the inference tier through the lab gateway, the one machine on the
tailnet. See [the lab gateway](lab-gateway.md) and
[ADR 0006](adr/0006-lab-gateway-for-llm-access.md).

The path is: browser on the CALDERA host → CALDERA with the MCP planning plugin → the lab
gateway at `100.66.6.130:8080` → Tailscale → nginx at `ambermist-h200:8080` → llama.cpp at
`127.0.0.1:8081` on the H200. The API base is `http://100.66.6.130:8080/v1`, and its model
alias is `reasoner`.

**The order of this guide is deliberate.** Part A installs CALDERA and tunes it locally
with no inference tier running at all. Part B connects it to the LLM, and only then do the
gateway and the H200 have to be up. The H200 bills at €3.728/h
([inference tier plan](inference-tier-plan.md)), so it is not worth creating the instance
until local installation and tuning pass. Nothing in Part A reaches
`http://100.66.6.130:8080` or needs the model.

Status: checked against repository configuration and upstream documentation;
installation has not been run end to end. The repository still marks its Phase 6
Tailscale deployment as unverified, and the gateway's leg to the H200 is unverified.

## Part A — local installation and tuning, no inference tier

Do all of this with the H200 destroyed. The lab gateway may be up or down; it is not used
here.

1. **Install CALDERA and the MCP planning plugin.**

   Both revisions below are commits, not branches, so this installs one exact tree
   each time. Neither is a release, and the 5.3.0 release cannot be used instead;
   [the pinned revisions](#the-pinned-revisions) explains why and how to re-check the
   pair before bumping either SHA. They are not an end-to-end tested pair: their
   dependency sets resolve, which is not the same as the result running.

   ```bash
   sudo apt update
   sudo apt install -y git build-essential python3-venv pipx \
     nodejs npm golang-go poppler-utils
   pipx install uv
   export PATH="$HOME/.local/bin:$PATH"

   git clone https://github.com/apache/caldera.git ~/caldera
   cd ~/caldera
   git checkout cb8d6a8160e0f4990af323a7c071d5e8482b42c7
   git submodule update --init --recursive

   git clone https://github.com/mitre/mcp.git plugins/mcp
   git -C plugins/mcp checkout 560c73851f2746b783b6479f044e2c6d94664c1d

   uv venv --python 3.12 --seed .venv
   source .venv/bin/activate
   uv pip install -r requirements.txt -r plugins/mcp/requirements.txt
   python -m spacy download en_core_web_lg
   uv pip check
   ```

   Stop if dependency installation or `uv pip check` fails. This uses
   [uv's isolated Python installation](https://docs.astral.sh/uv/guides/install-python/)
   and the [MCP installation procedure](https://github.com/mitre/mcp#installation).

2. **Create private CALDERA and LLM credentials.**

   This writes files only; no endpoint is contacted. `AMB_API_KEY` is the key the repo
   `.env` already holds, so it is available with the H200 down.

   In `~/caldera`, create a Python file with a text editor:

   ```bash
   cd ~/caldera
   nano setup_credentials.py
   ```

   Paste the following Python code into the editor. Copy only the code, starting
   with `import` at the beginning of the first line. This code block is at the
   left margin so it can also be copied from the Markdown source in an editor.

```python
import getpass
import os
import secrets
import shlex
from pathlib import Path

import yaml
from app.utility.config_util import hash_config_creds

main = Path("conf/local.yml")
env = Path("plugins/mcp/.env")
if main.exists() or env.exists():
    raise SystemExit("Existing configuration found; do not overwrite it.")

password = getpass.getpass("New CALDERA red password: ")
llm_key = getpass.getpass("Existing AMB_API_KEY: ")
if not password or not llm_key:
    raise SystemExit("Both values are required.")

os.umask(0o077)
config = yaml.safe_load(Path("conf/default.yml").read_text())
for name in ("api_key_red", "api_key_blue", "crypt_salt", "encryption_key"):
    config[name] = secrets.token_urlsafe(32)
caldera_key = config["api_key_red"]
config["users"] = {"red": {"red": password}}
config["host"] = "127.0.0.1"
config["port"] = 8888
config["plugins"] = ["magma", "sandcat", "stockpile", "mcp"]
for name, value in list(config.items()):
    if name.startswith("app.contact.") and isinstance(value, str):
        config[name] = value.replace("0.0.0.0", "127.0.0.1")
hash_config_creds(config)
main.write_text(yaml.safe_dump(config))

settings = {
    "MCP_LLM_API_BASE": "http://100.66.6.130:8080/v1",
    "MCP_LLM_API_KEY": llm_key,
    "CALDERA_URL": "http://127.0.0.1:8888",
    "CORE_CALDERA_API_KEY": caldera_key,
}
env.write_text("".join(
    f"{name}={shlex.quote(value)}\n" for name, value in settings.items()
))
print("Private configuration created.")
```

Save with `Ctrl+O`, Enter, then exit with `Ctrl+X`. Run it from `~/caldera`
with `.venv` active; the file command works from Bash or Zsh:

```bash
cd ~/caldera
source .venv/bin/activate
python setup_credentials.py
```

Enter a new CALDERA login password and the existing LLM bearer key,
`AMB_API_KEY`, at the hidden prompts. This is the same key every lab client holds; the
gateway passes the header through untouched and the H200 validates it, so the gateway
itself stores no credential. The script refuses to overwrite existing
configuration. CALDERA initially listens on loopback, including agent contacts.
Remote lab agents would need a reachable contact address configured separately.
Its hashed credentials and the plugin's saved API token follow the pinned
[CALDERA credential handling](https://github.com/apache/caldera/blob/cb8d6a8160e0f4990af323a7c071d5e8482b42c7/app/utility/config_util.py).

3. **Write the remote model configuration.**

   Another file edit, not a connection. The address recorded here is used in Part B.

   Open `~/caldera/plugins/mcp/conf/local.yml` in an editor and set:

   ```yaml
   llm:
     provider: openai_compatible
     model: openai/reasoner
     api_base: http://100.66.6.130:8080/v1
     offline: false
     temperature: 0.0
     max_tokens: 4096

   cti:
     offline: false
     timeout: 300
     context_budget_tokens: 4096
   ```

   This is the plugin's configuration, separate from CALDERA's root
   `conf/local.yml`. The `openai/` prefix selects the compatible provider adapter;
   the server model ID is `reasoner`. See [MCP configuration](https://github.com/mitre/mcp#configuration).

   The current [serving configuration](../node/serving.conf) provides 16,384
   tokens per session. These smaller budgets replace the plugin's 24,000-token
   defaults. Start with short requests: tool descriptions, history, and output
   must all fit, and longer planning workflows remain unqualified.

4. **Start CALDERA and tune it locally.**

   ```bash
   cd ~/caldera
   source .venv/bin/activate
   python server.py --build
   ```

   Open `http://127.0.0.1:8888` in the host's browser and log in as `red` with the
   password you chose. CALDERA's own features need no LLM: deploy a sandcat agent,
   browse abilities and adversary profiles, and run an operation end to end. Do that
   tuning now, while nothing is billing.

   Expect the MCP plugin to load and show its configuration pages with the endpoint
   unreachable — the api_base is read when a model call is made, not at startup. If the
   plugin instead refuses to load or the server fails to start without a reachable
   endpoint, record that and treat Part B as a prerequisite for the whole guide.

   **MLflow startup race (independent of the LLM endpoint).** The MCP plugin runs a
   bundled MLflow tracking server on `127.0.0.1:5000` and, in `plugins/mcp/hook.py`,
   waits only 10 seconds for it to bind. MLflow 3.x takes longer than that to start, so
   on a cold boot the plugin gives up, enables late, and fails to register its HTTP
   routes — the log shows `[MCP] MLflow did not start within 10s` followed by
   `Error enabling plugin=mcp, Cannot register a resource into frozen router`. The UI
   then loads but reports *"No workflows discovered"* even though discovery actually
   succeeded (the `author` and `plan_execute` workflows are in the log). This has
   nothing to do with the LLM endpoint or `plugins/mcp/conf/local.yml`.

   Two fixes. The one in use here: raise the wait in `plugins/mcp/hook.py` — in
   `_start_mlflow_server()`, change the start-wait loop from `for _ in range(10)` to
   `range(30)` (leave the earlier `range(10)` port-reclaim loop alone). This is a local
   edit to vendored plugin code and is reverted by a plugin reinstall, so keep a backup.
   The non-invasive alternative is to pre-start MLflow against the plugin's own store
   before CALDERA, so the plugin adopts it instead of restarting it into the race:

   ```bash
   python -m mlflow server \
     --backend-store-uri "sqlite:///$HOME/caldera/plugins/mcp/mlruns.db" \
     --default-artifact-root "$HOME/caldera/plugins/mcp/mlruns" \
     --host 127.0.0.1 --port 5000 &
   until curl -sf http://127.0.0.1:5000/health >/dev/null; do sleep 1; done
   ```

   A vanilla `mlflow server` with default storage does not work — the plugin checks the
   running server's artifact root, sees it is not this tree's `mlruns`, kills it, and
   restarts its own into the same 10s race.

   If MCP reports a CALDERA API 401, check `CORE_CALDERA_API_KEY`, which is
   separate from the LLM key, and is a local matter — no LLM involved. Subsequent
   starts use `python server.py`; rebuild when plugin UI assets change.
   See [MCP troubleshooting](https://github.com/mitre/mcp#troubleshooting).

   Do not move on until local installation and tuning pass.

## Part B — connect the LLM, once Part A passes

Everything below bills. Bring the inference tier up, do these steps in one sitting, and
destroy the compute stack afterwards.

5. **Bring up the inference tier and the gateway.**

   Create the H200 and join it to the tailnet
   ([Phase 6](phase-6-tailscale.md), [Phase 1](phase-1-first-light.md)), and make sure the
   gateway is configured ([the lab gateway](lab-gateway.md)). The gateway's own `verify`
   stage resolves the H200 and fetches `/health` through nginx, so run it first if the
   gateway has not been touched since the last `tailscale up`.

6. **Point the host at the lab gateway.**

   Nothing to install here: no Tailscale, no enrollment key, no MagicDNS. Confirm this
   host sits inside `100.66.6.0/24`, the range the gateway serves, and that the gateway
   answers:

   ```bash
   ip -4 addr show scope global | grep inet
   curl --fail --show-error --connect-timeout 10 --max-time 15 \
     http://100.66.6.130:8080/health
   ```

   Expect a healthy response. `502` means the gateway is reachable but the H200 is not —
   check that the node is joined to the tailnet. A refused connection means this host is
   outside the served subnet, or nginx on the gateway is not running. Resolve either
   before proceeding.

   If CALDERA runs on the gateway host itself, `http://ambermist-h200:8080/v1` also works
   there, straight over the tailnet. The gateway address works from anywhere in the lab
   and is the form used above and in the configuration.

7. **Verify an authenticated model response.**

   ```bash
   cd ~/caldera/plugins/mcp
   set +x
   set -a; source .env; set +a
   cd ../..
   source .venv/bin/activate

   python - <<'PY'
   import json
   import os
   import urllib.request

   base = os.environ["MCP_LLM_API_BASE"]
   headers = {
       "Authorization": "Bearer " + os.environ["MCP_LLM_API_KEY"],
       "Content-Type": "application/json",
   }

   def call(path, body=None):
       data = None if body is None else json.dumps(body).encode()
       request = urllib.request.Request(base + path, data=data, headers=headers)
       with urllib.request.urlopen(request, timeout=300) as response:
           return json.load(response)

   models = [model["id"] for model in call("/models")["data"]]
   print("Models:", models)
   if "reasoner" not in models:
       raise SystemExit("Expected model alias reasoner is missing.")
   result = call("/chat/completions", {
       "model": "reasoner",
       "messages": [{"role": "user", "content": "What is 6 times 7? Reply with the number."}],
       "max_tokens": 2048,
       "stream": False,
   })
   print("Answer:", result["choices"][0]["message"]["content"])
   PY
   ```

   Expect `reasoner` in the model list and an answer containing `42`. HTTP 401
   here means the LLM bearer key needs checking. This verifies connectivity and
   basic inference; it does not qualify the planning workflow.

8. **Use the planning plugin.**

   With CALDERA running, open **MCP → Global Model Configuration** and confirm the API
   base `http://100.66.6.130:8080/v1`, model `openai/reasoner`, and output budget
   `4096`. Start by asking it to list available CALDERA agents and summarize their
   capabilities. Then try a short planning request for your lab.

   **LiteLLM breaks the MCP stdio framing on the first completion call.** Starting an
   Author or Plan and Execute session can fail with repeated
   `ValidationError: ... Invalid JSON ... type=json_invalid` in the CALDERA log, quoting
   fragments like `19:20:18 - LiteLLM:INFO: utils.py:4382 -` or
   `LiteLLM completion() model=... provider = custom_openai`. This is unrelated to
   authentication or the gateway — the model call is actually going through. The MCP
   stdio transport requires the tool-server subprocess's stdout to carry nothing but
   JSON-RPC frames, but litellm's logger defaults to `LITELLM_LOG=DEBUG` and routes
   everything below `WARNING` to stdout by design
   (`litellm/_logging.py`'s `LevelRoutingStreamHandler`). `author.py`/`plan_execute.py`
   spawn `mcp_server.py` as a fresh interpreter (`StdioServerParameters(command=
   sys.executable, args=[mcp_server.py], ...)`), which never runs `hook.py`'s
   `logging.getLogger("LiteLLM").setLevel(logging.WARNING)` suppression — that only
   covers the parent process — so the subprocess logs at `DEBUG` straight onto the
   pipe the parent is reading as JSON-RPC.

   Fix: add `LITELLM_LOG=ERROR` to `plugins/mcp/.env`. `get_env()` in both workflow
   files builds the subprocess environment from `os.environ.copy()`, so this one line
   propagates to every spawned `mcp_server.py` and DSPy worker, not just the parent.
   Restart CALDERA afterwards so `hook.py`'s `load_dotenv()` picks it up.

9. **Tear down.**

   Run `tofu -chdir=infra/compute destroy` when you are done for the session. The model
   volume stays; the compute stack should not be left running between sittings. CALDERA
   itself can keep running — it falls back to Part A behaviour, where everything but the
   planning features still works.

## The pinned revisions

Step 1 installs `apache/caldera` at `cb8d6a8` and `mitre/mcp` at `560c738`. Both were
the tips of their default branches when this guide was written (2026-08-27 and
2026-09-03). Pinning commits rather than tracking the branches is what makes step 1
reproducible: two moving upstreams would otherwise turn a working install into a
resolver error with nothing to point at.

Neither is a release. CALDERA's newest release is 5.3.0, from 2025-04-24, some sixteen
months behind master, and nothing has been released since.

**Why not the 5.3.0 release.** The plugin never pins a package CALDERA core pins; it
takes a `>=` floor instead, a convention its
[requirements](https://github.com/mitre/mcp/blob/560c73851f2746b783b6479f044e2c6d94664c1d/requirements.txt)
states in its own header, so a plugin never contests a core-owned version. Two of those
five floors are out of reach of the release:

| Plugin floor | 5.3.0 | master `cb8d6a8` |
| --- | --- | --- |
| `aiohttp>=3.14.1` | `==3.10.11` — blocked | `==3.14.3` |
| `lxml>=6.1.0` | `~=4.9.1`, so `<4.10` — blocked | `==6.1.0` |
| `jinja2>=3.1.6` | `==3.1.6` | `==3.1.6` |
| `aiohttp-jinja2>=1.5.1` | `==1.5.1` | `==1.5.1` |
| `PyYAML>=6.0` | `==6.0.1` | `==6.0.1` |

The two blocked rows are unsatisfiable, so the resolver fails outright rather than
quietly installing something older. That is the whole of the dependency argument; the
other three floors are met by both.

**Why not 5.3.0 with an older plugin.** This route looks open and is not, for a reason
that has nothing to do with dependencies. Plugin revisions before
[`14dcd1e`](https://github.com/mitre/mcp/commit/14dcd1efa786) (2026-08-31) declare no
floors at all and do install against 5.3.0 — but `14dcd1e` is the commit that *added*
`api_base`. Before it the plugin talks to `api.openai.com` and cannot be pointed at the
lab gateway, which is the point of this guide. The floors and the one feature needed
here arrived in the same commit, so there is nothing to trade.

**Re-checking before a bump.** Both upstreams move, and a later core commit can drop
below a floor again. Diff the two floor lists before changing either SHA:

```bash
curl -s https://raw.githubusercontent.com/mitre/mcp/<mcp-ref>/requirements.txt \
  | grep '>='
curl -s https://raw.githubusercontent.com/apache/caldera/<caldera-ref>/requirements.txt \
  | grep -E 'aiohttp|lxml|jinja2|yaml'
```

Every `>=` floor in the first list must be met by the pin in the second. Read `~=X.Y.Z`
as `>=X.Y.Z,<X.(Y+1)`, which is what makes `lxml~=4.9.1` fall short of `lxml>=6.1.0`.
Three of the five floors are currently met *exactly* — `lxml`, `jinja2` and
`aiohttp-jinja2` — so a core commit that moves any of them down breaks the install even
though nothing is blocked today.

Matching floors is necessary and not sufficient: a transitive dependency neither file
names can still fail, which is why step 1 ends with `uv pip check`. That check, not this
one, is the authority on whether a pair installs.
