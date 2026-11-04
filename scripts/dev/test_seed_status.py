"""What the node's seed script writes in its status file (KAV-126, ADR-0039).

`make staging-up` prints that file as "Seed status". The script runs more than once: at first boot,
again in the permissions pass about 20 seconds later (KAV-85), and every six hours. The second run
finds the tables the first one made and used to overwrite "ok" with "skipped", so a fresh staging
that really was seeded read as if it had not been. These tests cut the script out of
infra/modules/node/user_data.sh.tftpl and run it with stand-ins for `curl`, `aws` and `psql`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "infra" / "modules" / "node" / "user_data.sh.tftpl"
needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")

# A fake restore.sh, which the real script downloads and runs. It "restores" or fails on request.
FAKE_RESTORE = r"""#!/bin/bash
[ "${FAKE_RESTORE:-ok}" = ok ] || exit 1
mkdir -p "$(dirname "$0")/../../.build"
printf '{"status":"ok","seconds":1,"rows":"0"}\n' > "$(dirname "$0")/../../.build/last-restore.json"
"""

STUBS = {
    "curl": r"""
case "$*" in
  *latest/api/token*) echo faketoken ;;
  *placement/region*) echo ap-south-1 ;;
  *restore.sh*|*anonymise.sql*)
    out=""; prev=""
    for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done
    case "$*" in
      *restore.sh*) cp "$FAKE_RESTORE_FILE" "$out" ;;
      *) echo "-- fake" > "$out" ;;
    esac ;;
esac
""",
    "aws": r"""
case "$*" in
  *"ec2 describe-instances"*) echo 10.0.0.5 ;;
  *"ssm get-parameter"*) echo fake-admin-password ;;
  *"s3 ls"*seed/LATEST*) [ "${FAKE_LATEST:-0}" = 1 ] ;;
  *"s3 ls"*seed/NONE*) [ "${FAKE_NONE:-0}" = 1 ] ;;
esac
""",
    "psql": 'echo "${FAKE_HAS:-none}"',
}


def _seed_script(state_dir: Path) -> str:
    """The `kaval-db-seed` script as the node receives it: Terraform's placeholders filled in and
    its status file pointed at a temporary folder."""
    text = TEMPLATE.read_text(encoding="utf-8")
    opener = "cat > /usr/local/sbin/kaval-db-seed <<'SEEDSCRIPT'\n"
    start = text.index(opener) + len(opener)
    body = text[start:text.index("\nSEEDSCRIPT\n", start)] + "\n"
    body = body.replace("${backup_bucket_name}", "test-bucket")
    body = body.replace("${name_prefix}", "kaval-test").replace("$${", "${")
    assert not re.search(r"\$\{[a-z_]+\}", body), "a Terraform placeholder this test does not fill"
    assert "/var/lib/kaval/seed-status.json" in body
    return body.replace("/var/lib/kaval", state_dir.as_posix())


class Node:
    """One fake node: a folder for the status file and stand-ins on the PATH. `run` is one run of
    the script, and may be repeated, as the node does."""

    def __init__(self, tmp_path: Path) -> None:
        self.state = tmp_path / "state"
        self.state.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        for name, body in STUBS.items():
            path = self.bin / name
            path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8", newline="\n")
            path.chmod(0o755)
        self.restore = tmp_path / "fake-restore.sh"
        self.restore.write_text(FAKE_RESTORE, encoding="utf-8", newline="\n")
        self.script = tmp_path / "seed.sh"
        self.script.write_text(_seed_script(self.state), encoding="utf-8", newline="\n")

    def run(self, **fake: str) -> int:
        env = {"PATH": f"{self.bin.as_posix()}:/usr/bin:/bin", "HOME": str(self.state),
               "FAKE_RESTORE_FILE": self.restore.as_posix(), **fake}
        done = subprocess.run(["bash", str(self.script)], capture_output=True, text=True,
                              check=False, env=env, timeout=60)
        return done.returncode

    def status(self) -> dict[str, str] | None:
        path = self.state / "seed-status.json"
        if not path.exists():
            return None
        data: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
        return data


@needs_bash
def test_an_empty_database_with_a_seed_is_seeded_and_says_ok(tmp_path: Path) -> None:
    node = Node(tmp_path)
    assert node.run(FAKE_HAS="none", FAKE_LATEST="1") == 0
    status = node.status()
    assert status is not None and status["status"] == "ok"


@needs_bash
def test_the_second_run_over_the_seeded_database_leaves_ok_in_place(tmp_path: Path) -> None:
    """The bug: the permissions pass runs the whole script again and used to write "skipped"."""
    node = Node(tmp_path)
    assert node.run(FAKE_HAS="none", FAKE_LATEST="1") == 0
    first = node.status()
    assert node.run(FAKE_HAS="public.signal", FAKE_LATEST="1") == 10  # it still does nothing
    assert node.status() == first
    assert first is not None and first["status"] == "ok"


@needs_bash
def test_a_resumed_node_with_tables_and_no_earlier_status_says_skipped(tmp_path: Path) -> None:
    """A parked staging comes back on a new node with no status file and a database with tables:
    "skipped" is true there, and is still what it says."""
    node = Node(tmp_path)
    assert node.status() is None
    assert node.run(FAKE_HAS="public.signal") == 10
    status = node.status()
    assert status is not None and status["status"] == "skipped"
    assert status["detail"] == "the database already has tables"


@needs_bash
def test_a_failed_seed_is_not_hidden_by_a_later_skip(tmp_path: Path) -> None:
    node = Node(tmp_path)
    assert node.run(FAKE_HAS="none", FAKE_LATEST="1", FAKE_RESTORE="fail") == 1
    failed = node.status()
    assert failed is not None and failed["status"] == "failed"
    assert node.run(FAKE_HAS="public.signal") == 10
    assert node.status() == failed


@needs_bash
def test_no_cleaned_copy_provided_says_skipped(tmp_path: Path) -> None:
    node = Node(tmp_path)
    assert node.run(FAKE_HAS="none", FAKE_NONE="1") == 10
    status = node.status()
    assert status is not None and status["status"] == "skipped"
    assert status["detail"] == "no cleaned copy was provided"


@needs_bash
def test_a_skip_is_replaced_by_a_real_outcome_later(tmp_path: Path) -> None:
    """Only a skip is protected from being overwritten, never a real result."""
    node = Node(tmp_path)
    assert node.run(FAKE_HAS="none", FAKE_NONE="1") == 10  # nothing to seed with yet
    skipped = node.status()
    assert skipped is not None and skipped["status"] == "skipped"
    assert node.run(FAKE_HAS="none", FAKE_LATEST="1") == 0  # the copy arrives, the node seeds
    status = node.status()
    assert status is not None and status["status"] == "ok"
