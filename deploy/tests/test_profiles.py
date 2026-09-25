import copy
import re
import tempfile
import unittest
from pathlib import Path

import yaml

from deploy.profile import (
    BASE_FLAGS, ROOT, load_profile, render_argv, resolve, validate_model,
    validate_runtime,
)

PLAN = ROOT.parent / "docs" / "QWEN38-EXPERIMENT-PLAN.md"
RUNTIME = ROOT / "runtimes" / "llamacpp-cuda.yaml"


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.runtime = load_profile(RUNTIME)
        self.base = load_profile(ROOT / "models" / "qwen38-ud-q4kxl.yaml")
        self.abliterated = load_profile(ROOT / "models" / "qwen38-abliterated-q4.yaml")
        self.h200 = load_profile(ROOT / "hardware" / "h200.yaml")
        self.rtx = load_profile(ROOT / "hardware" / "rtxpro6000.yaml")

    def test_pins_equal_experiment_blocks(self):
        source = PLAN.read_text()
        blocks = re.findall(r"```yaml\n(.*?)\n```", source, re.S)
        by_id = {item["id"]: item for block in blocks if (item := yaml.safe_load(block)) and "id" in item}
        for model in (self.base, self.abliterated):
            self.assertEqual(model, by_id[model["id"]])
            validate_model(model)

    def test_argv_and_ceilings(self):
        self.assertEqual((self.h200["session_ceiling"], self.rtx["session_ceiling"]), (4, 2))
        for hardware, max_sessions in ((self.h200, 4), (self.rtx, 2)):
            for sessions in range(1, max_sessions + 1):
                args = render_argv(self.base, self.runtime, hardware, sessions)
                self.assertEqual(args[:8], [
                    "--model", f"/mnt/weights/qwen38/{self.base['id']}/{self.base['entry_file']}",
                    "--alias", f"qwen38-{self.base['id']}", "--host", "127.0.0.1", "--port", "8001",
                ])
                self.assertEqual(args[8:8 + len(hardware["placement_args"])], hardware["placement_args"])
                self.assertEqual(args[args.index("--ctx-size") + 1], str(131072 * sessions))
                self.assertEqual(args[args.index("--parallel") + 1], str(sessions))
                self.assertEqual(args[-len(BASE_FLAGS):], BASE_FLAGS)
            for bad in (0, max_sessions + 1, 1.5):
                with self.assertRaises(ValueError):
                    render_argv(self.base, self.runtime, hardware, bad)
        self.assertIn("--n-cpu-moe", self.rtx["placement_args"])
        self.assertTrue(self.rtx["provisional"])

    def test_malformed_models_and_secrets(self):
        for edit in (
            lambda m: m.update(extra=True),
            lambda m: m["shards"][0].update(sha256=""),
            lambda m: m.update(entry_file="other.gguf"),
            lambda m: m.update(size_bytes=1),
        ):
            model = copy.deepcopy(self.base)
            edit(model)
            with self.assertRaises(ValueError):
                validate_model(model)
        self.assertIn(self.abliterated["entry_file"],
                      render_argv(self.abliterated, self.runtime, self.h200, 1)[1])
        from deploy.profile import _no_secret_fields
        with self.assertRaises(ValueError):
            _no_secret_fields({"nested": {"api_token": "bad"}})

    def test_blank_commit_blocks_resolved_deployment(self):
        with self.assertRaisesRegex(ValueError, "commit"):
            validate_runtime(self.runtime)
        with self.assertRaisesRegex(ValueError, "commit"):
            resolve(
                ROOT / "models" / "qwen38-ud-q4kxl.yaml", RUNTIME,
                ROOT / "hardware" / "h200.yaml", 1,
            )

    def test_profile_fields_contain_no_secrets(self):
        from deploy.profile import _no_secret_fields
        for profile in (self.runtime, self.base, self.abliterated, self.h200, self.rtx):
            _no_secret_fields(profile)

    def test_resolved_record_changes_with_input(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime_path = Path(temp) / "runtime.yaml"
            model_path = ROOT / "models" / "qwen38-ud-q4kxl.yaml"
            hardware_path = ROOT / "hardware" / "h200.yaml"
            runtime = copy.deepcopy(self.runtime)
            runtime["source_commit"] = "e9f824d8c0f011662a742c9d15d4aa18a41e32c0"
            runtime_path.write_text(yaml.safe_dump(runtime))
            one = resolve(model_path, runtime_path, hardware_path, 1)
            two = resolve(model_path, runtime_path, hardware_path, 2)
            self.assertNotEqual(one["record_sha256"], two["record_sha256"])
            self.assertEqual(one["status"], "candidate")
            self.assertIsNone(one["qualified_sessions"])
            runtime["verification"]["method"] = "changed"
            runtime_path.write_text(yaml.safe_dump(runtime))
            changed = resolve(model_path, runtime_path, hardware_path, 1)
            self.assertNotEqual(one["record_sha256"], changed["record_sha256"])


if __name__ == "__main__":
    unittest.main()
