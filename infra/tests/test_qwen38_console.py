"""Offline consistency checks for the profile-driven OpenTofu phase."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from deploy.profile import ROOT, load_profile, render_argv

INFRA = Path(__file__).resolve().parents[1]
RUNTIME = load_profile(ROOT / "runtimes" / "llamacpp-cuda.yaml")


def evaluate(phase, gpu="h200", model="base", sessions=1):
    expression = (
        "jsonencode({roles = local.roles, fleet = local.fleet_gpu_counts, "
        "argv = local.qwen38_argv, commit = local.qwen38_runtime.source_commit})\n"
    )
    with tempfile.TemporaryDirectory() as directory:
        command = [
            "tofu", "console", f"-state={directory}/empty.tfstate", "-lock=false",
            f"-var-file=phases/{phase}.tfvars", f"-var=qwen38_gpu={gpu}",
            f"-var=qwen38_model={model}", f"-var=qwen38_sessions={sessions}",
            '-var=ssh_public_keys={test="ssh-ed25519 AAAA"}', "-no-color",
        ]
        result = subprocess.run(
            command, cwd=INFRA, input=expression, text=True, capture_output=True,
            check=True, env={**os.environ, "TF_IN_AUTOMATION": "1"},
        )
    return json.loads(json.loads(result.stdout.strip()))


class ConsoleTests(unittest.TestCase):
    def test_new_phase_matches_profile_argv_and_fleet(self):
        for gpu, hardware_name, ceiling, family in (
            ("h200", "h200", 4, "h200"),
            ("rtx", "rtxpro6000", 2, "rtxpro"),
        ):
            hardware = load_profile(ROOT / "hardware" / f"{hardware_name}.yaml")
            for model_name, model_file in (
                ("base", "qwen38-ud-q4kxl"),
                ("abliterated", "qwen38-abliterated-q4"),
            ):
                model = load_profile(ROOT / "models" / f"{model_file}.yaml")
                for sessions in range(1, ceiling + 1):
                    with self.subTest(gpu=gpu, model=model_name, sessions=sessions):
                        value = evaluate("qwen38", gpu, model_name, sessions)
                        self.assertEqual(value["argv"], render_argv(model, RUNTIME, hardware, sessions))
                        self.assertEqual(value["fleet"][family], 1)
                        self.assertEqual(sum(value["fleet"].values()), 1)
                        self.assertEqual(value["roles"]["node_a"]["sku"], hardware["sku"])
                        self.assertEqual(value["roles"]["node_a"]["runtime"], "llamacpp")
                        self.assertEqual(value["commit"], "")

    def test_legacy_phase_roles_stay_on_vllm(self):
        for phase in ("p0-bake", "live4"):
            with self.subTest(phase=phase):
                value = evaluate(phase)
                self.assertTrue(value["roles"])
                self.assertTrue(all("runtime" not in role for role in value["roles"].values()))


if __name__ == "__main__":
    unittest.main()
