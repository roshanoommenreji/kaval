"""Where the approver token comes from (KAV-115, ADR-0073).

Two halves. The node's bootstrap step is cut out of infra/modules/node/user_data.sh.tftpl and run
with stand-ins for `aws` and `kubectl`: it must write the Secret `kaval-approver` when the token is
readable, and when it is NOT (the parameter is missing, the call fails, the value is empty) it must
not stop the bootstrap and must not write a Secret, because a gateway with no token is shut, which
is the safe state. And the Terraform: both environments make the token and let the node's role
read it."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "infra" / "modules" / "node" / "user_data.sh.tftpl"
needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")

TOKEN = "Zq" + "9" * 41


def _step() -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    start = text.index("if T=$(aws ssm get-parameter")
    end = text.index("unset T\n", start) + len("unset T\n")
    return text[start:end].replace("${name_prefix}", "kaval-test")


def _run(tmp_path: Path, aws_body: str) -> tuple[int, str, str, str]:
    """Run the step under the bootstrap's own `set -euo pipefail`. Returns (exit code, stdout,
    stderr, what kubectl was asked to do and given on stdin)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "kubectl.txt"
    stubs = {
        "aws": aws_body,
        # `create ... --dry-run -o yaml` prints a manifest; `apply -f -` reads it from stdin.
        "kubectl": (f'echo "kubectl $*" >> "{log.as_posix()}"\n'
                    f'if [ "$1" = apply ]; then cat >> "{log.as_posix()}"; '
                    'else echo "kind: Secret"; fi'),
    }
    for name, body in stubs.items():
        path = bin_dir / name
        path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8", newline="\n")
        path.chmod(0o755)
    script = tmp_path / "step.sh"
    script.write_text("set -euo pipefail\nREGION=ap-south-1\n" + _step() + 'echo finished\n',
                      encoding="utf-8", newline="\n")
    result = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, check=False,
        env={"PATH": f"{bin_dir.as_posix()}:/usr/bin:/bin", "HOME": str(tmp_path)})
    seen = log.read_text(encoding="utf-8") if log.exists() else ""
    return result.returncode, result.stdout, result.stderr, seen


@needs_bash
def test_a_readable_token_becomes_the_secret_the_gateway_reads(tmp_path: Path) -> None:
    code, out, _, seen = _run(tmp_path, f'echo "{TOKEN}"')
    assert code == 0 and "finished" in out
    assert "kubectl create secret generic kaval-approver -n kaval-test" in seen
    assert "--from-literal=APPROVER_TOKEN=" in seen and "kubectl apply -f -" in seen


@needs_bash
def test_it_asks_for_the_right_parameter_decrypted(tmp_path: Path) -> None:
    calls = tmp_path / "aws-args.txt"
    _run(tmp_path, f'echo "$@" > "{calls.as_posix()}"; echo "{TOKEN}"')
    asked = calls.read_text(encoding="utf-8")
    assert "--name /kaval/kaval-test/approver/token" in asked and "--with-decryption" in asked


@needs_bash
@pytest.mark.parametrize("aws_body", [
    "exit 255",                    # the call fails (parameter missing, no permission, no network)
    'echo ""',                     # it answers with nothing
    'echo "   "',                  # or only spaces
])
def test_an_unreadable_token_does_not_stop_the_bootstrap_and_writes_no_secret(
    tmp_path: Path, aws_body: str,
) -> None:
    code, out, _, seen = _run(tmp_path, aws_body)
    assert code == 0, "the bootstrap must carry on: a shut route is the safe state"
    assert "finished" in out and "approval route stays shut" in out
    assert "kaval-approver" not in seen


@needs_bash
def test_the_token_is_never_printed(tmp_path: Path) -> None:
    _, out, err, _ = _run(tmp_path, f'echo "{TOKEN}"')
    assert TOKEN not in out and TOKEN not in err


def test_the_step_sits_in_the_script_that_does_not_trace_commands() -> None:
    """`set -x` would print the token into the node's log. The script holding this step turns
    tracing off for its whole length (the comment on the secret-recreation script says so)."""
    text = TEMPLATE.read_text(encoding="utf-8")
    step_at = text.index("if T=$(aws ssm get-parameter")
    script_start = text.rindex("set -euo pipefail", 0, step_at)
    assert "set -x" not in text[script_start:step_at].replace("# `set -x`", "")


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_each_environment_makes_the_token_and_lets_the_node_read_it(env: str) -> None:
    text = (ROOT / "infra" / "envs" / env / "main.tf").read_text(encoding="utf-8")
    assert 'module "approver_token"' in text and "../../modules/approver-token" in text
    iam = text[text.index('module "iam"'):]
    assert "module.approver_token.parameter_arn" in iam.split("\n}\n", 1)[0]


def test_the_token_is_a_secure_string_with_no_special_characters() -> None:
    tf = (ROOT / "infra" / "modules" / "approver-token" / "main.tf").read_text(encoding="utf-8")
    assert 'type  = "SecureString"' in tf
    assert "special = false" in tf and "length  = 43" in tf
    assert "/approver/token" in tf
