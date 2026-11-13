"""`scripts/ops/dump-before-park.sh` is sourceable (KAV-134): nothing runs on source, so the
decision "did the dump finish?" and the guards around the park can be tested without AWS.
Skips without bash."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ops" / "dump-before-park.sh"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")

GOOD = """  dumping kaval...
  48K → s3://some-bucket/postgres/kaval-20261112T170000Z.dump
  done. A backup is not verified until you have restored one —
"""


def _sh(snippet: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    # bash by its full path: a test that empties PATH (to prove no `aws` is reached) must still
    # be able to start bash itself on Linux, where the lookup uses the child's PATH.
    bash = shutil.which("bash") or "bash"
    return subprocess.run(
        [bash, "-c", f'source "{SCRIPT.as_posix()}"; {snippet}'],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def test_the_script_has_no_syntax_errors_and_does_nothing_when_sourced() -> None:
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0
    out = _sh("true")
    assert out.returncode == 0 and out.stdout == ""


def test_a_finished_upload_counts() -> None:
    assert _sh(f"dump_succeeded '{GOOD}'").returncode == 0


@pytest.mark.parametrize(
    "log",
    [
        "",
        "  dumping kaval...\n",
        "  the dump cannot be read back by pg_restore -- NOT uploading it\n",
        "  done. but also NOT uploading it\n",
    ],
)
def test_anything_less_than_a_finished_upload_does_not_count(log: str) -> None:
    assert _sh(f"dump_succeeded '{log}'").returncode != 0


def test_the_bucket_name_is_hidden_from_what_is_printed() -> None:
    out = _sh(f"printf '%s' '{GOOD}' | hide_bucket")
    assert "some-bucket" not in out.stdout and "s3://<bucket>/postgres/" in out.stdout


def test_skipping_is_loud_and_asks_nothing_of_aws() -> None:
    out = _sh("SKIP_DUMP=1 main", env={"PATH": "/nonexistent"})
    assert out.returncode == 0 and "WITHOUT a fresh database dump" in out.stdout


def test_the_dump_runs_before_anything_is_scaled_down_and_never_after_the_database_stops() -> None:
    make = (ROOT / "Makefile").read_text(encoding="utf-8")
    down = make[make.index("\ndown:") : make.index(".PHONY: nuke")]
    dump = down.index("@bash scripts/ops/dump-before-park.sh")
    scale = down.index("&& terraform apply")
    pause = down.index("@bash scripts/ops/pause-database.sh")
    assert dump < down.index("echo 'app_node_desired_capacity") < scale < pause


def test_it_uses_the_units_own_log_not_an_old_one() -> None:
    body = SCRIPT.read_text(encoding="utf-8")
    assert "5 min ago" in body and '"set -e"' in body


def test_the_script_forces_utf8_so_the_backup_logs_arrow_survives_windows() -> None:
    """Found parking production on 2026-11-13 (KAV-148): backup.sh prints an arrow and an em dash;
    the AWS CLI on Windows could not write them with its default encoding, the log read back
    empty, and a good dump (396 KB, 'done.') was refused. It failed safe, but it must not happen."""
    text = SCRIPT.read_text(encoding="utf-8")
    main_body = text[text.index("main() {") :]
    assert "export PYTHONUTF8=1" in main_body
    assert "export PYTHONIOENCODING=utf-8" in main_body
    # Before the first AWS call, or it protects nothing.
    assert main_body.index("PYTHONUTF8") < main_body.index("aws ec2 describe-instances")
