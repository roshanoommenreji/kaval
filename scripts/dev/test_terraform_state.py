"""Terraform's record of what exists stays in the remote bucket (KAV-86, ADR-0058).

Offline rules, no AWS: every environment names a remote backend with locking and its own key,
no state file is ever committed, the bucket's real name never enters git, and the state bucket
keeps its protections. Each rule has a checker that the tests also run against a broken input,
so a rule that stopped working would fail here rather than pass silently.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ENVS = ROOT / "infra" / "envs"


def real_envs() -> list[Path]:
    """Environment folders that hold Terraform (placeholders hold only .gitkeep)."""
    return sorted(p for p in ENVS.iterdir() if p.is_dir() and any(p.glob("*.tf")))


def uncommented(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def backend_problems(env: str, main_tf: str) -> list[str]:
    """What is wrong with one environment's backend block (empty list = fine)."""
    code = uncommented(main_tf)
    m = re.search(r'backend\s+"s3"\s*\{(.*?)\n\s*\}', code, re.S)
    if not m:
        return [f"{env}: no s3 backend block (its record would sit on one laptop)"]
    body = m.group(1)
    problems = []
    if not re.search(r"use_lockfile\s*=\s*true", body):
        problems.append(f"{env}: use_lockfile is not true (two runs could write at once)")
    if not re.search(r"encrypt\s*=\s*true", body):
        problems.append(f"{env}: encrypt is not true")
    key = re.search(r'key\s*=\s*"([^"]+)"', body)
    if not key or key.group(1) != f"{env}/terraform.tfstate":
        problems.append(f"{env}: key must be {env}/terraform.tfstate")
    if re.search(r"\bbucket\s*=", body):
        problems.append(f"{env}: the bucket name is written in git (it comes from tf-init.sh)")
    return problems


def tracked_state_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    return [f for f in out if re.search(r"\.tfstate($|\.)", f)]


def test_there_are_environments_to_check() -> None:
    names = {p.name for p in real_envs()}
    assert {"prod", "staging", "dev", "state"} <= names


@pytest.mark.parametrize("env", [p.name for p in real_envs()])
def test_every_environment_keeps_its_record_in_the_remote_bucket(env: str) -> None:
    text = (ENVS / env / "main.tf").read_text(encoding="utf-8")
    assert backend_problems(env, text) == []


@pytest.mark.parametrize("env", [p.name for p in real_envs()])
def test_the_terraform_version_supports_the_s3_lock(env: str) -> None:
    text = (ENVS / env / "main.tf").read_text(encoding="utf-8")
    m = re.search(r'required_version\s*=\s*">=\s*1\.(\d+)"', text)
    assert m and int(m.group(1)) >= 10, f"{env}: use_lockfile needs Terraform 1.10 or newer"


def test_the_checker_flags_a_local_backend() -> None:
    assert backend_problems("prod", 'terraform {\n  required_version = ">= 1.11"\n}\n')


def test_the_checker_flags_a_commented_out_backend() -> None:
    text = 'terraform {\n  # backend "s3" {\n  #   use_lockfile = true\n  # }\n}\n'
    assert backend_problems("prod", text)


def test_the_checker_flags_no_lock_and_a_wrong_key() -> None:
    text = (
        'terraform {\n  backend "s3" {\n    key = "staging/terraform.tfstate"\n'
        "    encrypt = true\n  }\n}\n"
    )
    problems = backend_problems("prod", text)
    assert any("use_lockfile" in p for p in problems)
    assert any("key must be" in p for p in problems)


def test_the_checker_flags_a_bucket_name_in_git() -> None:
    text = (
        'terraform {\n  backend "s3" {\n    bucket = "x"\n    key = "prod/terraform.tfstate"\n'
        "    encrypt = true\n    use_lockfile = true\n  }\n}\n"
    )
    assert any("bucket name" in p for p in backend_problems("prod", text))


def test_the_checker_accepts_a_good_block() -> None:
    text = (
        'terraform {\n  backend "s3" {\n    key = "prod/terraform.tfstate"\n'
        '    region = "ap-south-1"\n    encrypt = true\n    use_lockfile = true\n  }\n}\n'
    )
    assert backend_problems("prod", text) == []


def test_no_state_file_is_committed() -> None:
    assert tracked_state_files() == []


def test_the_real_bucket_name_is_not_in_any_committed_file() -> None:
    """kaval-tfstate- followed by the random suffix is the real name; the bare prefix is fine."""
    files = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    pattern = re.compile(r"kaval-tfstate-[0-9a-f]{8}")
    hits = []
    for f in files:
        path = ROOT / f
        if path.suffix in {".png", ".svg", ".lock", ".gz"} or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if pattern.search(text):
            hits.append(f)
    assert hits == [], f"the state bucket's real name is in git: {hits}"


def test_the_state_bucket_keeps_its_protections() -> None:
    code = uncommented((ENVS / "state" / "main.tf").read_text(encoding="utf-8"))
    for needle in (
        "prevent_destroy = true",
        'status = "Enabled"',  # versioning
        'sse_algorithm = "AES256"',
        "block_public_policy     = true",
        "aws:SecureTransport",
        "noncurrent_version_expiration",
    ):
        assert needle in code, f"state bucket lost: {needle}"
    assert "force_destroy = false" in code


def test_the_init_script_reads_the_name_from_parameter_store() -> None:
    text = (ROOT / "scripts" / "ops" / "tf-init.sh").read_text(encoding="utf-8")
    assert "/kaval/tfstate/bucket" in text
    assert "MSYS_NO_PATHCONV" in text
    assert "kaval-tfstate-" not in text
