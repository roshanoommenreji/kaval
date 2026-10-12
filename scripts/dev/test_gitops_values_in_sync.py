"""The two copies of each environment's settings must be the same (KAV-81).

Flux deploys what is inside `deploy/gitops/<env>/helmrelease.yaml` (`spec.values`), while people
(and the chart tests and CI's render) read `deploy/environments/<env>/values.yaml`. They are kept in
step by hand, and when they were not, a staging change (the disk line, the probe target) rendered
fine in every test and never reached the cluster: the first slow-service game day found nothing
because the live poller had no target. This test fails the moment they differ."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def _differences(live: object, written: object, path: str = "") -> list[str]:
    if isinstance(live, dict) and isinstance(written, dict):
        out: list[str] = []
        for key in sorted(set(live) | set(written)):
            here = f"{path}{key}"
            if key not in live:
                out.append(f"{here}: only in deploy/environments (never deployed)")
            elif key not in written:
                out.append(f"{here}: only in the HelmRelease (not in the environment file)")
            else:
                out += _differences(live[key], written[key], f"{here}.")
        return out
    return [] if live == written else [f"{path.rstrip('.')}: {live!r} (deployed) != {written!r}"]


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_the_deployed_settings_are_the_written_settings(env: str) -> None:
    live = yaml.safe_load(
        (ROOT / "deploy" / "gitops" / env / "helmrelease.yaml").read_text(encoding="utf-8")
    )["spec"]["values"]
    written = yaml.safe_load(
        (ROOT / "deploy" / "environments" / env / "values.yaml").read_text(encoding="utf-8"))
    assert _differences(live, written) == []


def test_the_comparison_notices_a_missing_and_a_different_setting() -> None:
    assert _differences({"a": {"b": 1}}, {"a": {"b": 1, "c": 2}}) == [
        "a.c: only in deploy/environments (never deployed)"]
    assert _differences({"a": {"b": 1}}, {"a": {"b": 2}}) == ["a.b: 1 (deployed) != 2"]
