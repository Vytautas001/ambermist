# CALDERA on Kali with a remote LLM over Tailscale

Run these commands on Kali in Bash, as your normal user. If your terminal uses
Zsh or another shell, run `bash` once and wait for its prompt before pasting the
command blocks. The `bash` label on a Markdown code fence only controls syntax
highlighting; it does not select the shell. Kali adopted Zsh as its default for
new desktop installations in [2020.4](https://www.kali.org/blog/kali-linux-2020-4-release/).

This guide is for a fresh CALDERA installation and a Kali device that has not
joined Tailscale yet. The H200 must already be running and joined to the same
tailnet.

The path is: browser on Kali → CALDERA with the MCP planning plugin → Tailscale →
nginx at `ambermist-h200:8080` → llama.cpp at `127.0.0.1:8081` on the H200. The API base is
`http://ambermist-h200:8080/v1`, and its model alias is `reasoner`.

Status: checked against repository configuration and upstream documentation;
installation and connectivity have not been run on Kali. The repository still
marks its Phase 6 Tailscale deployment as unverified.

1. **Prepare the predefined Tailscale key.**

   **Kali must use a dedicated Tailscale consumer enrollment key, not an admin key.**
   Create it for the H200's tailnet with only `tag:ambermist-consumer`. Do not
   reuse keys intended for administrator devices or H200 nodes. An administrator
   may generate this key, but Kali must enroll with the consumer identity and
   permissions.

   Enable **Pre-approved** if device approval is required. Disable **Ephemeral**
   for a persistent Kali machine. Use a one-off key for one machine; enable
   **Reusable** only for multiple enrollments. If Tailnet Lock is enabled, the
   key also needs the required signature. These settings permit enrollment
   without a browser login. See [Tailscale auth keys](https://tailscale.com/docs/features/access-control/auth-keys).

   The [infrastructure README](../README.md#prerequisites) specifies that its
   `TS_AUTHKEY` enrolls H200 nodes tagged `tag:ambermist`. Keep that key separate. The
   [repository policy](../ops/tailnet-policy.hujson) grants the Kali consumer tag
   access to the H200 on TCP 8080. If an auth key appeared in chat or shared logs,
   revoke it and use a replacement. Revoking an auth key does not disconnect
   devices already enrolled with it.

   Save only the Kali enrollment key in a private file on Kali:

   ```bash
   set +x
   umask 077
   mkdir -p ~/.config/ambermist-kali
   chmod 700 ~/.config/ambermist-kali
   touch ~/.config/ambermist-kali/.env
   chmod 600 ~/.config/ambermist-kali/.env
   nano ~/.config/ambermist-kali/.env
   ```

   In the editor, add this entry with your predefined consumer key. Do not paste
   the real value into a shell command or this manual:

   ```dotenv
   TS_AUTHKEY='REPLACE_WITH_KALI_CONSUMER_AUTH_KEY'
   ```

2. **Install Tailscale and join with the key.**

   If Tailscale is not installed:

   ```bash
   sudo apt update
   sudo apt install -y curl ca-certificates
   curl --fail --show-error --silent --location https://tailscale.com/install.sh | sh
   sudo systemctl enable --now tailscaled
   ```

   Register Kali. The subshell loads the private `.env`, writes a temporary file
   with mode 0600, and removes that temporary file afterward. Only its path is
   passed to Tailscale:

   ```bash
   (
     set +x
     set -e
     umask 077
     cd ~/.config/ambermist-kali
     set -a; source .env; set +a
     : "${TS_AUTHKEY:?Set TS_AUTHKEY in the Kali .env first}"
     auth_key_file=$(mktemp)
     trap 'rm -f -- "$auth_key_file"' EXIT
     printf '%s' "$TS_AUTHKEY" > "$auth_key_file"
     unset TS_AUTHKEY
     sudo tailscale up \
       --auth-key="file:$auth_key_file" \
       --hostname=kali-caldera \
       --accept-dns=true
   )
   ```

   The key supplies the tag automatically. The `file:` syntax is documented in
   [tailscale up](https://tailscale.com/docs/reference/tailscale-cli/up).
   Confirm in the admin console that Kali has `tag:ambermist-consumer`, the H200
   has `tag:ambermist`, and the repository's grant is applied.

   If Kali is already enrolled, check its identity and tags before changing
   them. Supplying another auth key to an already authenticated device does not
   establish that it switched identity.

   Test the actual API port; a successful Tailscale ping alone is insufficient:

   ```bash
   tailscale ping ambermist-h200
   curl --fail --show-error --connect-timeout 10 --max-time 15 \
     http://ambermist-h200:8080/health
   ```

   Expect a healthy response. A hostname error points to MagicDNS; a timeout
   points to node availability, access policy, or firewall configuration. HTTP
   503 can mean the model is loading. Resolve this before proceeding. Normal
   reboots use the saved device identity and do not require the enrollment key.

3. **Install CALDERA and the MCP planning plugin.**

   These development revisions avoid the known dependency mismatch between
   CALDERA 5.3.0 and the newer MCP plugin. They are not an end-to-end tested pair.
   See the pinned [CALDERA requirements](https://github.com/apache/caldera/blob/cb8d6a8160e0f4990af323a7c071d5e8482b42c7/requirements.txt)
   and [MCP requirements](https://github.com/mitre/mcp/blob/560c73851f2746b783b6479f044e2c6d94664c1d/requirements.txt).

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

4. **Create private CALDERA and LLM credentials.**

   From `~/caldera`, with `.venv` active, run the following. Enter a new CALDERA
   login password and the existing LLM bearer key, `AMB_API_KEY`, at the hidden
   prompts. The Tailscale auth key is only for device enrollment; it is not the
   LLM API key. This script refuses to overwrite existing configuration.

   ```bash
   python - <<'PY'
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
       "MCP_LLM_API_BASE": "http://ambermist-h200:8080/v1",
       "MCP_LLM_API_KEY": llm_key,
       "CALDERA_URL": "http://127.0.0.1:8888",
       "CORE_CALDERA_API_KEY": caldera_key,
   }
   env.write_text("".join(
       f"{name}={shlex.quote(value)}\n" for name, value in settings.items()
   ))
   print("Private configuration created.")
   PY
   ```

   CALDERA initially listens on loopback, including agent contacts. Remote lab
   agents would need a reachable contact address configured separately. Its
   hashed credentials and the plugin's saved API token follow the pinned
   [CALDERA credential handling](https://github.com/apache/caldera/blob/cb8d6a8160e0f4990af323a7c071d5e8482b42c7/app/utility/config_util.py).

5. **Configure the remote model.**

   Open `~/caldera/plugins/mcp/conf/local.yml` in an editor and set:

   ```yaml
   llm:
     provider: openai_compatible
     model: openai/reasoner
     api_base: http://ambermist-h200:8080/v1
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

6. **Verify an authenticated model response.**

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

7. **Start CALDERA.**

   ```bash
   cd ~/caldera
   source .venv/bin/activate
   python server.py --build
   ```

   Open `http://127.0.0.1:8888` in Kali's browser and log in as `red` with the
   password you chose. In **MCP → Global Model Configuration**, confirm the API
   base `http://ambermist-h200:8080/v1`, model `openai/reasoner`, and output budget
   `4096`. Start by asking it to list available CALDERA agents and summarize
   their capabilities. Then try a short planning request for your lab.

   If MCP reports a CALDERA API 401, check `CORE_CALDERA_API_KEY`, which is
   separate from the LLM key. Subsequent starts use `python server.py`; rebuild
   when plugin UI assets change. See [MCP troubleshooting](https://github.com/mitre/mcp#troubleshooting).
