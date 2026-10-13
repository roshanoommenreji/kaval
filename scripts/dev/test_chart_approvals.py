"""The approval timings in the chart (KAV-82, ADR-0048): prod keeps the real ones, staging a
short set so a test can watch a request expire, and the gateway and executor both get them.

Renders the real chart with `helm template`, so it needs helm; without it the tests skip."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy" / "charts" / "kaval"
pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def _env_values(env: str) -> dict[str, str]:
    out = subprocess.run(
        ["helm", "template", f"kaval-{env}", str(CHART), "-f",
         str(ROOT / "deploy" / "environments" / env / "values.yaml")],
        check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout
    return dict(re.findall(
        r'(?m)^\s+- name: ((?:EXECUTOR_)?APPROVAL_\w+)\n\s+value: "([^"]+)"', out,
    ))


def test_prod_waits_15_30_and_240_minutes_and_trusts_an_approval_for_an_hour() -> None:
    assert _env_values("prod") == {
        "APPROVAL_REMINDER_MINUTES": "15",
        "APPROVAL_ESCALATE_MINUTES": "30",
        "APPROVAL_EXPIRE_MINUTES": "240",
        "EXECUTOR_APPROVAL_MAX_AGE_MINUTES": "60",
    }


def test_staging_is_short_so_a_test_can_watch_a_request_expire() -> None:
    values = _env_values("staging")
    assert (values["APPROVAL_REMINDER_MINUTES"], values["APPROVAL_ESCALATE_MINUTES"],
            values["APPROVAL_EXPIRE_MINUTES"]) == ("3", "7", "15")


def test_staging_still_gives_a_game_day_time_to_approve() -> None:
    # The slowest human step measured so far was 273 s.
    assert float(_env_values("staging")["APPROVAL_EXPIRE_MINUTES"]) * 60 > 273 * 2
