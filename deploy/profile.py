"""Strict offline profile validation and deterministic llama-server argv rendering."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

import yaml

ROOT = Path(__file__).resolve().parent
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
SECRET_FIELD = re.compile(r"key|token|secret|password", re.I)
MODEL_KEYS = {
    "id", "repository", "revision", "format", "quantization", "entry_file",
    "size_bytes", "shards", "license", "base_model",
}
RUNTIME_KEYS = {
    "schema_version", "id", "engine", "source_repository", "source_commit",
    "source_commit_date_utc", "source_ref_kind", "image_digest", "build_image",
    "build_platform", "build_image_digest", "build_image_index_digest",
    "cuda_arch", "cmake_args_by_arch", "supported_flags", "verification",
}
VERIFICATION_KEYS = {
    "checked_utc", "method", "qwen4exp_merge_commit",
    "qwen4exp_merge_is_ancestor", "cuda_decode_issue", "cuda_decode_status",
    "cuda_decode_note",
}
HARDWARE_KEYS = {
    "id", "sku", "cuda_arch", "session_ceiling", "placement_args", "provisional",
}
BASE_FLAGS = [
    "--no-kv-unified", "--no-context-shift", "--fit", "off",
    "--flash-attn", "on", "--cache-type-k", "f16", "--cache-type-v", "f16",
    "--batch-size", "512", "--ubatch-size", "128", "--ctx-checkpoints", "2",
    "--cache-ram", "8192", "--jinja", "--metrics", "--slots",
]


def _keys(value: dict, expected: set[str], name: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{name}: missing or unknown keys: {set(value) ^ expected if isinstance(value, dict) else 'not a mapping'}")


def _no_secret_fields(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if SECRET_FIELD.search(str(key)):
                raise ValueError(f"secret-shaped profile field: {key}")
            _no_secret_fields(child)
    elif isinstance(value, list):
        for child in value:
            _no_secret_fields(child)


def _safe_file(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts and value.endswith(".gguf")


def load_profile(path: Path) -> dict:
    raw = path.read_bytes()
    value = yaml.safe_load(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected mapping")
    _no_secret_fields(value)
    return value


def validate_model(model: dict) -> None:
    _keys(model, MODEL_KEYS, "model")
    if (not HEX40.fullmatch(str(model["revision"]))
            or model["format"] != "gguf"
            or model["license"] != "qwen-community-1.0"
            or not isinstance(model["size_bytes"], int)
            or model["size_bytes"] <= 0
            or not isinstance(model["repository"], str)
            or "/" not in model["repository"]):
        raise ValueError("invalid model identity or size")
    shards = model["shards"]
    if not isinstance(shards, list) or not shards:
        raise ValueError("model needs shards")
    names = set()
    total = 0
    for shard in shards:
        _keys(shard, {"file", "size_bytes", "sha256"}, "shard")
        if (not _safe_file(shard["file"]) or shard["file"] in names
                or not isinstance(shard["size_bytes"], int) or shard["size_bytes"] <= 0
                or not HEX64.fullmatch(str(shard["sha256"]))):
            raise ValueError("invalid shard")
        names.add(shard["file"])
        total += shard["size_bytes"]
    if model["entry_file"] not in names or total != model["size_bytes"]:
        raise ValueError("entry file or shard total mismatch")


def validate_runtime(runtime: dict, *, require_commit: bool = True) -> None:
    _keys(runtime, RUNTIME_KEYS, "runtime")
    _keys(runtime["verification"], VERIFICATION_KEYS, "verification")
    if runtime["schema_version"] != 1 or runtime["engine"] != "llamacpp":
        raise ValueError("unsupported runtime")
    if runtime["source_repository"] != "https://github.com/ggml-org/llama.cpp":
        raise ValueError("unsupported source")
    if require_commit and not HEX40.fullmatch(str(runtime["source_commit"])):
        raise ValueError("llama.cpp commit has not been verified")
    if runtime["source_commit"] and not HEX40.fullmatch(str(runtime["source_commit"])):
        raise ValueError("invalid source commit")
    if (runtime["image_digest"] is not None
            or runtime["build_platform"] != "linux/amd64"
            or not str(runtime["build_image_digest"]).startswith("sha256:")
            or not HEX64.fullmatch(str(runtime["build_image_digest"])[7:])
            or not HEX64.fullmatch(str(runtime["build_image_index_digest"])[7:])):
        raise ValueError("invalid build image pin")
    if set(runtime["cuda_arch"]) != {90, 120}:
        raise ValueError("unsupported CUDA architectures")
    for arch in runtime["cuda_arch"]:
        if runtime["cmake_args_by_arch"].get(str(arch)) != [
            "-DGGML_CUDA=ON", f"-DCMAKE_CUDA_ARCHITECTURES={arch}",
        ]:
            raise ValueError("missing architecture build flags")


def validate_hardware(hardware: dict, runtime: dict) -> None:
    _keys(hardware, HARDWARE_KEYS, "hardware")
    allowed = {
        "h200": ("1H200.141S.44V", 90, 4),
        "rtxpro6000": ("1RTXPRO6000.30V", 120, 2),
    }
    if (hardware["id"] not in allowed
            or (hardware["sku"], hardware["cuda_arch"], hardware["session_ceiling"])
            != allowed.get(hardware["id"])
            or hardware["cuda_arch"] not in runtime["cuda_arch"]
            or not isinstance(hardware["provisional"], bool)):
        raise ValueError("unsupported hardware")
    args = hardware["placement_args"]
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise ValueError("placement must be an argv list")
    if args[:4] != ["--n-gpu-layers", "all", "--override-tensor", "per_layer_token_embd=CPU"]:
        raise ValueError("CPU PLE placement required")
    if hardware["id"] == "h200" and len(args) != 4:
        raise ValueError("unexpected H200 placement")
    if hardware["id"] == "rtxpro6000" and (args[4:] != ["--n-cpu-moe", "12"] or not hardware["provisional"]):
        raise ValueError("RTX offload must remain provisional")


def render_argv(model: dict, runtime: dict, hardware: dict, sessions: int) -> list[str]:
    validate_model(model)
    validate_runtime(runtime, require_commit=False)
    validate_hardware(hardware, runtime)
    if type(sessions) is not int or not 1 <= sessions <= hardware["session_ceiling"]:
        raise ValueError("sessions exceed hardware ceiling")
    argv = [
        "--model", f"/mnt/weights/qwen38/{model['id']}/{model['entry_file']}",
        "--alias", f"qwen38-{model['id']}", "--host", "127.0.0.1",
        "--port", "8001", *hardware["placement_args"],
        "--ctx-size", str(131072 * sessions), "--parallel", str(sessions),
        *BASE_FLAGS,
    ]
    flags = {arg for arg in argv if arg.startswith("--")}
    if not flags.issubset(set(runtime["supported_flags"])):
        raise ValueError("runtime does not support requested flag")
    return argv


def resolve(model_path: Path, runtime_path: Path, hardware_path: Path, sessions: int) -> dict:
    model = load_profile(model_path)
    runtime = load_profile(runtime_path)
    hardware = load_profile(hardware_path)
    validate_runtime(runtime)
    argv = render_argv(model, runtime, hardware, sessions)
    hashes = {
        key: hashlib.sha256(path.read_bytes()).hexdigest()
        for key, path in (
            ("model", model_path), ("runtime", runtime_path), ("hardware", hardware_path)
        )
    }
    record = {
        "status": "candidate", "profile_sha256": hashes,
        "model_id": model["id"], "runtime_commit": runtime["source_commit"],
        "sku": hardware["sku"], "sessions": sessions,
        "ctx_size": 131072 * sessions, "parallel": sessions, "argv": argv,
        "qualified_sessions": None,
    }
    record["record_sha256"] = hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return record
