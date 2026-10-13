"""The node's start-up step that makes the `kaval-heartbeat` Secret never stops the node (KAV-83).

The step is cut out of infra/modules/node/user_data.sh.tftpl and run under the script's own shell
options (`set -euo pipefail`) with stand-ins for `aws` and `kubectl`. The point is the negative
cases: an address that is missing, unreadable or still the placeholder must give an EMPTY Secret
(the poller then sends nothing) and the script must carry on; only a real https address is kept.
Fake addresses are built from pieces so no secret scanner mistakes them for real ones."""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "infra" / "modules" / "node" / "user_data.sh.tftpl"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")

GOOD = "https://hc-ping." + "example/" + "-".join(["aaaa1111", "bbbb", "cccc"])


def _step() -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    start = text.index('HEARTBEAT_URL=""')
    end = text.index("unset HEARTBEAT_URL HB_VALUE") + len("unset HEARTBEAT_URL HB_VALUE")
    return text[start:end].replace("${name_prefix}", "kaval-test")


def _run(tmp_path: Path, aws_body: str) -> tuple[int, str]:
    """Run the step; return (exit code, the value the Secret was created with)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    out = tmp_path / "applied.txt"
    (bin_dir / "aws").write_text(f"#!/bin/bash\n{aws_body}\n", encoding="utf-8", newline="\n")
    (bin_dir / "kubectl").write_text(textwrap.dedent(f"""\
        #!/bin/bash
        if [ "$1" = create ]; then
          for a in "$@"; do case "$a" in --from-literal=HEARTBEAT_URL=*) v="${{a#*=}}"; echo "${{v#*=}}" ;; esac; done
        elif [ "$1" = apply ]; then
          cat > "{out.as_posix()}"
        fi
        """), encoding="utf-8", newline="\n")
    for name in ("aws", "kubectl"):
        (bin_dir / name).chmod(0o755)
    script = tmp_path / "step.sh"
    script.write_text(f"set -euo pipefail\nREGION=ap-south-1\n{_step()}\necho survived > "
                      f"{(tmp_path / 'done.txt').as_posix()}\n", encoding="utf-8", newline="\n")
    env = {"PATH": f"{bin_dir.as_posix()}:/usr/bin:/bin", "HOME": str(tmp_path)}
    result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True,
                            check=False)
    survived = (tmp_path / "done.txt").exists()
    applied = out.read_text(encoding="utf-8").strip() if out.exists() else "<no Secret applied>"
    return (0 if survived else result.returncode or 1), applied


def test_a_real_https_address_is_kept(tmp_path: Path) -> None:
    code, value = _run(tmp_path, f"echo {GOOD}")
    assert code == 0 and value == GOOD


@pytest.mark.parametrize("answer", ["echo REPLACE_ME", "echo None", "echo ''",
                                    "echo http://hc-ping.example/not-secure"])
def test_a_placeholder_or_non_https_value_gives_an_empty_secret(tmp_path: Path, answer: str) -> None:
    code, value = _run(tmp_path, answer)
    assert code == 0 and value == ""


@pytest.mark.parametrize("failure", ["exit 254", "echo AccessDenied >&2; exit 255"])
def test_a_missing_or_unreadable_parameter_does_not_stop_the_script(
    tmp_path: Path, failure: str,
) -> None:
    code, value = _run(tmp_path, failure)
    assert code == 0 and value == ""  # script went on to its next line; the Secret exists, empty
