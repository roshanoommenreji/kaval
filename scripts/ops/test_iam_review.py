"""The access review's decisions (KAV-154), on made-up policies: no AWS call."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

import iam_review as ir  # noqa: E402


def allow(sid: str, action: Any, resource: Any, **extra: Any) -> dict[str, Any]:
    return {"Sid": sid, "Effect": "Allow", "Action": action, "Resource": resource, **extra}


def test_a_narrow_policy_has_nothing_wide() -> None:
    doc = {"Statement": [allow("Put", "ssm:PutParameter", "arn:aws:ssm:region:acct:parameter/x")]}
    assert ir.wide_statements(doc) == []


def test_a_service_wildcard_action_is_flagged() -> None:
    doc = {"Statement": [allow("All", "ec2:*", "arn:aws:ec2:region:acct:volume/v")]}
    assert ir.wide_statements(doc) == [("action", "All", "ec2:*")]


def test_everything_is_flagged() -> None:
    assert ir.wide_statements({"Statement": [allow("Any", "*", "*")]})[0][0] == "action"


def test_an_action_aws_can_narrow_on_every_resource_is_flagged() -> None:
    # The brake's real shape: scaling a group may name the group, and here it does not.
    doc = {
        "Statement": [
            allow(
                "Scale",
                ["autoscaling:UpdateAutoScalingGroup", "autoscaling:DescribeAutoScalingGroups"],
                "*",
            )
        ]
    }
    assert ir.wide_statements(doc) == [("resource", "Scale", "autoscaling:UpdateAutoScalingGroup")]


def test_an_action_aws_cannot_narrow_is_listed_as_known_not_as_a_fault() -> None:
    doc = {"Statement": [allow("Look", ["ec2:DescribeVolumes", "ec2:DescribeSnapshots"], "*")]}
    assert [w[0] for w in ir.wide_statements(doc)] == ["resource-known"]


def test_the_registry_login_token_is_known() -> None:
    assert (
        ir.wide_statements({"Statement": [allow("Auth", "ecr:GetAuthorizationToken", "*")]})[0][0]
        == "resource-known"
    )


def test_a_condition_makes_a_star_resource_acceptable() -> None:
    doc = {
        "Statement": [
            allow(
                "Put",
                "cloudwatch:PutMetricData",
                "*",
                Condition={"StringEquals": {"cloudwatch:namespace": "X"}},
            )
        ]
    }
    assert ir.wide_statements(doc) == []
    doc = {
        "Statement": [
            allow(
                "Del",
                "ec2:DeleteVolume",
                "*",
                Condition={"StringEquals": {"ec2:ResourceTag/Project": "kaval"}},
            )
        ]
    }
    assert ir.wide_statements(doc) == []


def test_a_deny_is_never_a_finding() -> None:
    doc = {"Statement": [{"Sid": "No", "Effect": "Deny", "Action": "*", "Resource": "*"}]}
    assert ir.wide_statements(doc) == []


def test_one_statement_not_in_a_list_is_read() -> None:
    assert ir.wide_statements({"Statement": allow("Any", "*", "*")})[0][0] == "action"


def test_an_account_number_is_masked() -> None:
    # Built from pieces: no realistic account id sits in the file (the secret scanners).
    number = "".join(["1234", "5678", "9012"])
    assert ir.mask(f"arn:aws:iam::{number}:role/x") == "arn:aws:iam::<account>:role/x"
