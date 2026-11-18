"""The plan summary prints names, never values (KAV-89): the repository is public."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import plan_summary as ps

# Built from pieces: a whole twelve-digit number is what the secret scanners look for.
ACCOUNT = "".join(("1234", "5678", "9012"))
PASSWORD = "-".join(("hunter", "two", "swordfish"))
EMAIL = "someone@" + "example.invalid"
BUCKET = "kaval-tfstate-" + "abcd1234"


def rc(
    address: str, actions: list[str], before: Any = None, after: Any = None, unknown: Any = None
) -> dict[str, Any]:
    return {
        "address": address,
        "change": {
            "actions": actions,
            "before": before,
            "after": after,
            "after_unknown": unknown or {},
        },
    }


def test_no_changes_says_so() -> None:
    plan = {"resource_changes": [rc("aws_x.a", ["no-op"]), rc("data.aws_y.b", ["read"])]}
    out = ps.summarise(plan, "prod")
    assert "No changes" in out
    assert "aws_x.a" not in out


def test_actions_are_named_and_counted() -> None:
    plan = {
        "resource_changes": [
            rc("aws_a.one", ["create"], None, {"name": "n"}),
            rc("aws_a.two", ["update"], {"size": 1}, {"size": 2}),
            rc("aws_a.three", ["delete", "create"], {"ami": "x"}, {"ami": "y"}),
            rc("aws_a.four", ["delete"], {"name": "n"}, None),
        ]
    }
    out = ps.summarise(plan, "state")
    assert "1 to add, 1 to change, 1 to replace, 1 to destroy." in out
    # the worst news comes first
    assert (
        out.index("aws_a.four")
        < out.index("aws_a.three")
        < out.index("aws_a.two")
        < out.index("aws_a.one")
    )


def test_a_change_lists_the_setting_names_only() -> None:
    plan = {
        "resource_changes": [
            rc(
                "aws_db.main",
                ["update"],
                {"password": PASSWORD, "size": 1, "same": "s"},
                {"password": PASSWORD + "2", "size": 2, "same": "s"},
                {"arn": True, "id": False},
            )
        ]
    }
    out = ps.summarise(plan, "prod")
    assert "arn, password, size" in out
    assert PASSWORD not in out
    assert "same" not in out


def test_account_numbers_are_hidden_even_in_an_address() -> None:
    plan = {"resource_changes": [rc(f"aws_s3_bucket.b-{ACCOUNT}", ["create"], None, {})]}
    out = ps.summarise(plan, "prod")
    assert ACCOUNT not in out
    assert "<account>" in out


def test_strings_the_caller_names_are_hidden() -> None:
    plan = {"resource_changes": [rc(f"aws_x.{BUCKET}", ["update"], {"a": EMAIL}, {"a": "b"})]}
    out = ps.summarise(plan, "prod", [BUCKET, EMAIL, ""])
    assert BUCKET not in out
    assert EMAIL not in out


def test_a_pipe_cannot_break_the_table() -> None:
    plan = {"resource_changes": [rc('aws_x.a["p|q"]', ["create"], None, {})]}
    assert "p\\|q" in ps.summarise(plan, "prod")


def test_a_long_plan_is_cut_and_says_so() -> None:
    plan = {
        "resource_changes": [
            rc(f"aws_x.r{i:04d}", ["create"], None, {}) for i in range(ps.MAX_ROWS + 5)
        ]
    }
    out = ps.summarise(plan, "prod")
    assert "5 more not shown" in out
    assert f"aws_x.r{ps.MAX_ROWS:04d}" not in out


def test_the_command_line_reads_a_file(tmp_path: Path, capsys: Any, monkeypatch: Any) -> None:
    f = tmp_path / "plan.json"
    f.write_text(
        json.dumps({"resource_changes": [rc("aws_x.a", ["create"], None, {})]}), encoding="utf-8"
    )
    monkeypatch.setenv("PLAN_SUMMARY_MASKS", "aws_x\n")
    assert ps.main(["plan_summary.py", "prod", str(f)]) == 0
    out = capsys.readouterr().out
    assert "1 to add" in out
    assert "aws_x" not in out  # the masks from the environment are applied


def test_wrong_usage_is_refused(capsys: Any) -> None:
    assert ps.main(["plan_summary.py"]) == 2
