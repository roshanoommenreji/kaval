"""The node's "still alive" step sends a datapoint only on clear evidence (KAV-83, ADR-0053).

The script is cut out of infra/modules/node/user_data.sh.tftpl and run with stand-ins for `curl`,
`kubectl` and `aws`. What matters is the negative side: the health page unreachable, no finish time,
a stale one, one from the future, a number that is not a number, or a different service's line must
all send NOTHING, because silence is what the alarm is built to notice. Only a poller that finished
a pass in the last 150 seconds produces a datapoint, with exactly the namespace, metric and
dimension the alarm reads."""

from __future__ import annotations

import shutil
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "infra" / "modules" / "node" / "user_data.sh.tftpl"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")

METRIC = "kaval_loop_last_success_timestamp_seconds"


def _script() -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    start_marker = "cat > /usr/local/sbin/kaval-heartbeat <<'HBSCRIPT'\n"
    start = text.index(start_marker) + len(start_marker)
    end = text.index("\nHBSCRIPT\n", start) + 1
    return text[start:end].replace("${name_prefix}", "kaval-test")


def _page(poller: str | None, other: str | None = None) -> str:
    lines = ["# HELP " + METRIC + " when the last pass finished", "# TYPE " + METRIC + " gauge"]
    if other is not None:
        lines.append(f'{METRIC}{{service="gateway"}} {other}')
    if poller is not None:
        lines.append(f'{METRIC}{{service="collector-prometheus"}} {poller}')
    lines.append('kaval_loop_interval_seconds{service="collector-prometheus"} 30.0')
    return "\n".join(lines) + "\n"


def _run(tmp_path: Path, page: str | None) -> tuple[int, str, list[str]]:
    """Run the script. page None = the kubectl call fails. Returns (exit code, stderr, aws calls)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "aws-calls.txt"
    page_file = tmp_path / "page.txt"
    page_file.write_text(page or "", encoding="utf-8", newline="\n")
    stubs = {
        "curl": 'echo ap-south-1',
        "kubectl": 'exit 1' if page is None else f'cat "{page_file.as_posix()}"',
        "aws": f'echo "$@" >> "{calls.as_posix()}"',
    }
    for name, body in stubs.items():
        path = bin_dir / name
        path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8", newline="\n")
        path.chmod(0o755)
    script = tmp_path / "heartbeat.sh"
    script.write_text(_script(), encoding="utf-8", newline="\n")
    result = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, check=False,
        env={"PATH": f"{bin_dir.as_posix()}:/usr/bin:/bin", "HOME": str(tmp_path)})
    sent = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return result.returncode, result.stderr, sent


def test_a_poller_that_just_finished_a_pass_sends_exactly_the_datapoint_the_alarm_reads(
    tmp_path: Path,
) -> None:
    code, err, sent = _run(tmp_path, _page(f"{time.time() - 20:.9e}"))  # scientific, as Prometheus writes
    assert (code, err) == (0, "")  # and nothing is printed when all is well
    assert sent == ["cloudwatch put-metric-data --namespace Kaval/Heartbeat --metric-name StillAlive "
                    "--dimensions Environment=kaval-test --unit Count --value 1"]


def test_a_plain_decimal_finish_time_works_too(tmp_path: Path) -> None:
    code, _, sent = _run(tmp_path, _page(f"{time.time() - 100:.3f}"))
    assert code == 0 and len(sent) == 1


@pytest.mark.parametrize(("label", "poller"), [
    ("stale", lambda: f"{time.time() - 400:.1f}"),
    ("just over the limit", lambda: f"{time.time() - 200:.1f}"),
    ("from the future", lambda: f"{time.time() + 500:.1f}"),
    ("not a number", lambda: "NaN"),
    ("garbage", lambda: "abc"),
    ("empty", lambda: ""),
])
def test_an_unclear_or_old_finish_time_sends_nothing(tmp_path: Path, label: str, poller) -> None:
    code, err, sent = _run(tmp_path, _page(poller()))
    assert code == 1 and sent == [], label
    assert "no heartbeat" in err


def test_a_page_with_no_poller_line_sends_nothing_even_if_another_service_is_fresh(
    tmp_path: Path,
) -> None:
    code, err, sent = _run(tmp_path, _page(None, other=f"{time.time() - 5:.1f}"))
    assert code == 1 and sent == [] and "no finish time" in err


def test_an_unreachable_health_page_sends_nothing(tmp_path: Path) -> None:
    code, err, sent = _run(tmp_path, None)
    assert code == 1 and sent == [] and "could not be read" in err


def test_the_step_is_wired_as_a_two_minute_timer() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "OnUnitActiveSec=2min" in text
    assert "systemctl enable --now kaval-heartbeat.timer" in text
    # the alarm reads the same names the script writes
    alarm = (ROOT / "infra" / "modules" / "still-alive" / "main.tf").read_text(encoding="utf-8")
    assert 'namespace   = "Kaval/Heartbeat"' in alarm and 'metric_name = "StillAlive"' in alarm
    assert textwrap.dedent("Environment = var.name_prefix") in alarm
