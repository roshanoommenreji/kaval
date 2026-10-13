"""`still_alive.py`: pings for a PARKED production and for nothing else (KAV-83, ADR-0052).

The rule is a decision table, so each row of it is a test, and the unsafe directions (pinging for a
server that is running, or when unsure) are the ones tested hardest. The address is built from
pieces so no secret scanner mistakes it for a real one."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest
import still_alive  # same folder; pytest puts it on the path

GROUP = "kaval-prod"
URL = "https://hc-ping." + "example/" + "-".join(
    ["aaaa1111", "bbbb", "cccc", "dddd", "eeee55556666"])
ENV = {"HEARTBEAT_URL": URL}


def group(desired: int, *states: str, name: str = GROUP) -> dict[str, Any]:
    return {"AutoScalingGroups": [{
        "AutoScalingGroupName": name, "DesiredCapacity": desired,
        "Instances": [{"InstanceId": f"i-{n:08x}", "LifecycleState": s}
                      for n, s in enumerate(states)],
    }]}


# ── the table ───────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("answer", "expected"), [
    (group(0), "parked"),
    (group(0, "Terminated"), "parked"),
    (group(0, "Terminating:Wait", "Terminating:Proceed"), "parked"),
    (group(1, "InService"), "running"),
    (group(1), "running"),                      # wants one, has none yet: coming up or failing
    (group(1, "Pending"), "running"),
    (group(0, "InService"), "running"),         # parking, not finished
    (group(0, "Terminating"), "running"),       # not in the "gone" list: wait until it is
])
def test_the_table(answer: object, expected: str) -> None:
    assert still_alive.state_of(answer, GROUP)[0] == expected


@pytest.mark.parametrize("answer", [
    None, {}, [], "parked", {"AutoScalingGroups": None}, {"AutoScalingGroups": []},
    group(0, name="some-other-group"),
    {"AutoScalingGroups": [{"AutoScalingGroupName": GROUP}]},
    {"AutoScalingGroups": [{"AutoScalingGroupName": GROUP, "DesiredCapacity": "0",
                            "Instances": []}]},
    {"AutoScalingGroups": [{"AutoScalingGroupName": GROUP, "DesiredCapacity": False,
                            "Instances": []}]},
    {"AutoScalingGroups": [{"AutoScalingGroupName": GROUP, "DesiredCapacity": 0}]},
    {"AutoScalingGroups": [group(0)["AutoScalingGroups"][0], group(0)["AutoScalingGroups"][0]]},
])
def test_anything_unclear_is_unknown_never_parked(answer: object) -> None:
    assert still_alive.state_of(answer, GROUP)[0] == "unknown"


# ── what main does with it ──────────────────────────────────────────────────────────────────

class _Sender:
    def __init__(self, result: str | None = None) -> None:
        self.result, self.sent = result, []  # type: ignore[var-annotated]

    def __call__(self, url: str) -> str | None:
        self.sent.append(url)
        return self.result


def _run(answer: object | Exception, sender: _Sender, environ: dict[str, str] | None = None,
         argv: list[str] | None = None) -> int:
    def reader(_group: str) -> object:
        if isinstance(answer, Exception):
            raise answer
        return answer

    return still_alive.main(argv or ["x", GROUP], environ=ENV if environ is None else environ,
                            reader=reader, sender=sender)


def test_parked_pings_once_and_exits_0(capsys: pytest.CaptureFixture[str]) -> None:
    sender = _Sender()
    assert _run(group(0), sender) == 0
    assert sender.sent == [URL]
    assert "pinged for the parked server" in capsys.readouterr().out


def test_running_sends_nothing_and_exits_0(capsys: pytest.CaptureFixture[str]) -> None:
    sender = _Sender()
    assert _run(group(1, "InService"), sender) == 0
    assert sender.sent == []
    assert "its own poller" in capsys.readouterr().out


@pytest.mark.parametrize("failure", [
    subprocess.CalledProcessError(254, "aws"), FileNotFoundError("aws"),
    json.JSONDecodeError("x", "", 0),
])
def test_an_aws_failure_sends_nothing_and_exits_2(failure: Exception) -> None:
    sender = _Sender()
    assert _run(failure, sender) == 2 and sender.sent == []


def test_an_unclear_answer_sends_nothing_and_exits_2() -> None:
    sender = _Sender()
    assert _run({"AutoScalingGroups": []}, sender) == 2 and sender.sent == []


def test_a_failed_ping_exits_1() -> None:
    assert _run(group(0), _Sender("HTTP 500")) == 1


@pytest.mark.parametrize("environ", [
    {}, {"HEARTBEAT_URL": ""}, {"HEARTBEAT_URL": "http://x.example/a"}])
def test_no_address_or_an_insecure_one_does_nothing_and_exits_2(environ: dict[str, str]) -> None:
    sender = _Sender()
    assert _run(group(0), sender, environ) == 2 and sender.sent == []


def test_wrong_arguments_exit_2() -> None:
    assert _run(group(0), _Sender(), argv=["x"]) == 2


def test_the_address_is_never_printed(capsys: pytest.CaptureFixture[str]) -> None:
    for answer, result in [(group(0), None), (group(0), "HTTP 500"), (group(1, "InService"), None),
                           ({}, None)]:
        _run(answer, _Sender(result))
    _run(group(0), _Sender(), {"HEARTBEAT_URL": "http://" + URL.removeprefix("https://")})
    out = capsys.readouterr().out
    assert URL not in out and "example" not in out
