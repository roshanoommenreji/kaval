"""`scripts/ops/chaos.sh` is sourceable (KAV-79), so its small decisions can be tested without AWS:
nothing runs on source, and the helpers answer. Skips without bash."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "ops" / "chaos.sh"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")


def _sh(snippet: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", "-c", f'source "{SCRIPT.as_posix()}"; {snippet}'],
                          capture_output=True, text=True, encoding="utf-8")


@pytest.mark.parametrize(("experiment", "victim"), [("oom", "chaos-oomer"),
                                                    ("badrelease", "chaos-badrelease"),
                                                    ("slow", "chaos-slowpoke"),
                                                    ("disk", "chaos-diskfill")])
def test_each_experiment_has_its_own_victim(experiment: str, victim: str) -> None:
    out = _sh(f"victim_of {experiment}")
    assert out.returncode == 0 and out.stdout.strip() == victim


def test_an_unknown_experiment_is_refused() -> None:
    out = _sh("victim_of nonsense")
    assert out.returncode != 0 and "unknown experiment" in out.stderr


def test_the_script_has_no_syntax_errors_and_does_nothing_when_sourced() -> None:
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0
    out = _sh("true")
    assert out.returncode == 0 and out.stdout == ""


def test_the_victims_match_the_runner() -> None:
    from kaval_executor import chaos
    assert _sh("victim_of oom").stdout.strip() == chaos.VICTIM_NAME
    assert _sh("victim_of badrelease").stdout.strip() == chaos.RELEASE_VICTIM_NAME
    assert _sh("victim_of slow").stdout.strip() == chaos.SLOW_VICTIM_NAME
    assert _sh("victim_of disk").stdout.strip() == chaos.DISK_VICTIM_NAME


def test_the_by_hand_switch_reaches_the_pod_that_files_the_proposal() -> None:
    """KAV-87: `CHAOS_BY_HAND=1 make chaos-propose` did nothing because only the experiment's name
    was passed into the pod, so the script there always answered with the worker's proposal."""
    stub = 'need_node() { :; }; pod_run() { printf "%s" "$4"; }'
    on = _sh(f'{stub}; CHAOS_BY_HAND=1 cmd_propose oom')
    assert on.returncode == 0 and '"name":"CHAOS_BY_HAND","value":"1"' in on.stdout
    off = _sh(f'{stub}; unset CHAOS_BY_HAND; cmd_propose oom')
    assert off.returncode == 0 and '"name":"CHAOS_BY_HAND","value":"0"' in off.stdout
