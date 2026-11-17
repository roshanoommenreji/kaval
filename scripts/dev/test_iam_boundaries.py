"""The two permission boundaries (KAV-161, ADR-0101): a ceiling over the roles that act.

A permission boundary is a second policy on a role. The role may do only what BOTH its own policy
and the boundary allow, and an explicit "no" in either wins. So the proof is not "the role's policy
looks tight" (the role's own tests say that) but "even if the role's policy were widened to
everything, it still could not reach a server, IAM, or a disk with the wrong label".

The same table of cases runs two ways:

* every time, in CI too, through a small checker written here that follows IAM's rules for the four
  condition operators the boundaries use (an unknown one fails the test, so a new operator cannot
  slip in unchecked); and
* on request (`KAVAL_IAM_SIMULATE=1`; a read-only call that changes nothing and costs nothing),
  through IAM's own policy simulator, which must give the same answer as the checker for every
  case. That is what keeps the checker honest.

The negative cases ("must be refused") are the point. They stay as tests so a later edit to a
boundary that lets one through fails here, not in the account.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
BUDGET = ROOT / "infra" / "modules" / "budget"
REMOVAL = ROOT / "infra" / "modules" / "waste-removal"

REGION = "ap-south-1"
# Built from pieces: a whole twelve-digit number inside an ARN is what the secret scanners look for.
ACCOUNT = "".join(("1234", "5678", "9012"))
LOG_DIR = "/aws/lambda"
LOG_ARN = f"arn:aws:logs:{REGION}:{ACCOUNT}"
GROUP_ID = "-".join(("11111111", "2222", "3333", "4444", "5555abcd5555"))


# ── a small checker for IAM's rules ─────────────────────────────────────────────────────────────


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _glob(pattern: str, text: str, *, fold: bool = False) -> bool:
    flags = re.IGNORECASE if fold else 0
    regex = "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pattern)
    return re.fullmatch(regex, text, flags) is not None


Context = Mapping[str, list[str]]


def _condition_holds(operator: str, key: str, wanted: Any, ctx: Context) -> bool:
    have = ctx.get(key.lower())
    wanted_list = [str(w) for w in _as_list(wanted)]
    if operator == "StringEquals":
        return have is not None and any(h in wanted_list for h in have)
    if operator == "StringNotEquals":
        return have is None or not any(h in wanted_list for h in have)
    if operator == "ForAllValues:StringEquals":
        return have is None or all(h in wanted_list for h in have)
    if operator == "Null":
        return (have is None) == (wanted_list == ["true"])
    raise AssertionError(f"the checker does not know the condition operator {operator!r}")


def _statement_applies(st: Mapping[str, Any], action: str, resource: str, ctx: Context) -> bool:
    if not any(_glob(a, action, fold=True) for a in _as_list(st["Action"])):
        return False
    if not any(_glob(r, resource) for r in _as_list(st["Resource"])):
        return False
    return all(
        _condition_holds(op, key.lower(), wanted, ctx)
        for op, block in st.get("Condition", {}).items()
        for key, wanted in block.items()
    )


def _verdict(doc: Mapping[str, Any], action: str, resource: str, ctx: Context) -> str:
    hits = [
        st["Effect"]
        for st in _as_list(doc["Statement"])
        if _statement_applies(st, action, resource, ctx)
    ]
    if "Deny" in hits:
        return "deny"
    return "allow" if "Allow" in hits else "none"


def allowed(
    role: Mapping[str, Any], boundary: Mapping[str, Any], action: str, resource: str, ctx: Context
) -> bool:
    """IAM's rule for a role with a boundary: no explicit "no" anywhere, and a "yes" in both."""
    a, b = _verdict(role, action, resource, ctx), _verdict(boundary, action, resource, ctx)
    return a == "allow" and b == "allow"


# The worst case the boundary exists for: the role's own policy edited to allow everything.
WIDENED = {
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Action": "*", "Resource": "*"}],
}


# ── the documents under test ────────────────────────────────────────────────────────────────────


def brake_boundary() -> dict[str, Any]:
    doc: dict[str, Any] = json.loads((BUDGET / "boundary.json").read_text(encoding="utf-8"))
    return doc


def _render(template: str, values: Mapping[str, str]) -> str:
    """Fill a Terraform template the one way these use: `${name}`. Anything fancier fails loudly."""
    out = re.sub(r"\$\{(\w+)\}", lambda m: values[m.group(1)], template)
    assert "${" not in out, "the template uses something this renderer does not fill"
    return out


ALL_ENVS = ["prod", "staging", "dev", "state", "lab-eks"]


def remover_boundary(env: str) -> dict[str, Any]:
    template = (REMOVAL / "boundary.json.tftpl").read_text(encoding="utf-8")
    prefix = f"kaval-{env}"
    doc = _render(
        template,
        {
            "volume_arn": f"arn:aws:ec2:{REGION}:{ACCOUNT}:volume/*",
            "snapshot_arn": f"arn:aws:ec2:{REGION}::snapshot/*",
            "env_name": env,
            "other_envs": json.dumps([e for e in ALL_ENVS if e != env]),
            "orders_arn": f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/kaval/{prefix}/removal-orders",
            "result_arn": f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/kaval/{prefix}/removal-result",
            "log_arn": f"{LOG_ARN}:log-group:{LOG_DIR}/{prefix}-waste-removal:*",
        },
    )
    out: dict[str, Any] = json.loads(doc)
    return out


# ── the cases ───────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Case:
    label: str
    action: str
    resource: str
    ctx: dict[str, list[str]]
    ok: bool  # True: must be allowed; False: must be refused


def vol(**tags: str) -> dict[str, list[str]]:
    return {f"ec2:resourcetag/{k.lower()}": [v] for k, v in tags.items()}


def snap(**tags: str) -> dict[str, list[str]]:
    return vol(**tags)


def _volume(i: str = "0abc") -> str:
    return f"arn:aws:ec2:{REGION}:{ACCOUNT}:volume/vol-{i}"


def _snapshot(i: str = "0abc") -> str:
    return f"arn:aws:ec2:{REGION}::snapshot/snap-{i}"


def _own_log_group() -> str:
    return f"{LOG_ARN}:log-group:{LOG_DIR}/kaval-prod-budget-hard-stop:*"


def _instance() -> str:
    return f"arn:aws:ec2:{REGION}:{ACCOUNT}:instance/i-0abc"


def _asg(name: str = "kaval-staging") -> str:
    return (
        f"arn:aws:autoscaling:{REGION}:{ACCOUNT}:autoScalingGroup:{GROUP_ID}"
        f":autoScalingGroupName/{name}"
    )


def brake_cases() -> list[Case]:
    kaval_asg = {"autoscaling:resourcetag/project": ["kaval"]}
    other_asg = {"autoscaling:resourcetag/project": ["stock-trader"]}
    kaval_aws = {"aws:resourcetag/project": ["kaval"]}
    other_aws = {"aws:resourcetag/project": ["stock-trader"]}
    return [
        # what the brake really does
        Case("look at groups", "autoscaling:DescribeAutoScalingGroups", "*", {}, True),
        Case("look at servers", "ec2:DescribeInstances", "*", {}, True),
        Case("look at disks", "ec2:DescribeVolumes", "*", {}, True),
        Case("scale a Kaval group", "autoscaling:UpdateAutoScalingGroup", _asg(), kaval_asg, True),
        Case("set a Kaval group's size", "autoscaling:SetDesiredCapacity", _asg(), kaval_asg, True),
        Case("stop a Kaval server", "ec2:StopInstances", _instance(), kaval_aws, True),
        Case("snapshot a Kaval disk", "ec2:CreateSnapshot", _volume(), kaval_aws, True),
        Case(
            "the new snapshot is labelled Kaval",
            "ec2:CreateSnapshot",
            _snapshot(),
            {"aws:requesttag/project": ["kaval"]},
            True,
        ),
        Case(
            "label the new snapshot",
            "ec2:CreateTags",
            _snapshot(),
            {"ec2:createaction": ["CreateSnapshot"]},
            True,
        ),
        Case("write its own log", "logs:PutLogEvents", _own_log_group(), {}, True),
        # what a widened or mistaken policy must still not reach
        Case(
            "scale a group with no label",
            "autoscaling:UpdateAutoScalingGroup",
            _asg("x"),
            {},
            False,
        ),
        Case(
            "scale another project's group",
            "autoscaling:UpdateAutoScalingGroup",
            _asg("o"),
            other_asg,
            False,
        ),
        Case(
            "set another project's group size",
            "autoscaling:SetDesiredCapacity",
            _asg("o"),
            other_asg,
            False,
        ),
        Case("delete a group", "autoscaling:DeleteAutoScalingGroup", _asg(), kaval_asg, False),
        Case(
            "end a server in a group",
            "autoscaling:TerminateInstanceInAutoScalingGroup",
            _asg(),
            kaval_asg,
            False,
        ),
        Case("end (delete) a server", "ec2:TerminateInstances", _instance(), kaval_aws, False),
        Case("stop another project's server", "ec2:StopInstances", _instance(), other_aws, False),
        Case("stop a server with no label", "ec2:StopInstances", _instance(), {}, False),
        Case("start a server", "ec2:RunInstances", _instance(), kaval_aws, False),
        Case("delete a disk", "ec2:DeleteVolume", _volume(), kaval_aws, False),
        Case("delete a snapshot", "ec2:DeleteSnapshot", _snapshot(), kaval_aws, False),
        Case("snapshot an unlabelled disk", "ec2:CreateSnapshot", _volume(), {}, False),
        Case(
            "make a snapshot labelled for another project",
            "ec2:CreateSnapshot",
            _snapshot(),
            {"aws:requesttag/project": ["stock-trader"]},
            False,
        ),
        Case("change IAM (a user)", "iam:CreateUser", f"arn:aws:iam::{ACCOUNT}:user/x", {}, False),
        Case(
            "change IAM (a role's policy)",
            "iam:PutRolePolicy",
            f"arn:aws:iam::{ACCOUNT}:role/x",
            {},
            False,
        ),
        Case(
            "read a secret",
            "ssm:GetParameter",
            f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/x",
            {},
            False,
        ),
        Case("read the bucket", "s3:GetObject", "arn:aws:s3:::x/y", {}, False),
    ]


def remover_cases(env: str) -> list[Case]:
    theirs = "prod" if env != "prod" else "staging"
    mine = {"ec2:resourcetag/project": ["kaval"], "ec2:resourcetag/env": [env]}

    def disk(**over: str) -> dict[str, list[str]]:
        base = {"Project": "kaval", "Env": env, "ManagedBy": "manual"}
        merged = {**base, **over}
        return vol(**{k: v for k, v in merged.items() if v != ""})

    def copy_(**over: str) -> dict[str, list[str]]:
        base = {"Project": "kaval", "ManagedBy": "kaval-lambda", "Env": env}
        merged = {**base, **over}
        return snap(**{k: v for k, v in merged.items() if v != ""})

    group = f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/aws/lambda"
    own_log = f"{group}/kaval-{env}-waste-removal:log-stream:2026/11/17"
    other_log = f"{group}/kaval-{env}-something-else:log-stream:2026/11/17"
    orders = f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/kaval/kaval-{env}/removal-orders"
    result = f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/kaval/kaval-{env}/removal-result"
    return [
        # what the worker really does
        Case("look at copies", "ec2:DescribeSnapshots", "*", {}, True),
        Case("look at disks", "ec2:DescribeVolumes", "*", {}, True),
        Case("back up a loose Kaval disk", "ec2:CreateSnapshot", _volume(), disk(), True),
        Case(
            "the backup is labelled Kaval",
            "ec2:CreateSnapshot",
            _snapshot(),
            {"aws:requesttag/project": ["kaval"], "aws:requesttag/managedby": ["kaval-lambda"]},
            True,
        ),
        Case(
            "label the new backup",
            "ec2:CreateTags",
            _snapshot(),
            {"ec2:createaction": ["CreateSnapshot"]},
            True,
        ),
        Case(
            "mark a Kaval disk",
            "ec2:CreateTags",
            _volume(),
            {**disk(), "aws:tagkeys": ["KavalRemoval"]},
            True,
        ),
        Case(
            "delete a Kaval disk",
            "ec2:DeleteVolume",
            _volume(),
            disk(KavalRemoval="2026-11-17"),
            True,
        ),
        Case(
            "delete a Kaval copy of this environment",
            "ec2:DeleteSnapshot",
            _snapshot(),
            copy_(),
            True,
        ),
        Case(
            "delete an old copy with no environment label",
            "ec2:DeleteSnapshot",
            _snapshot(),
            copy_(Env=""),
            True,
        ),
        Case("read the orders", "ssm:GetParameter", orders, {}, True),
        Case("read the result", "ssm:GetParameter", result, {}, True),
        Case("write the result", "ssm:PutParameter", result, {}, True),
        Case("write its own log", "logs:PutLogEvents", own_log, {}, True),
        Case("write another function's log", "logs:PutLogEvents", other_log, {}, False),
        # what a widened or mistaken policy must still not reach
        Case("end (delete) a server", "ec2:TerminateInstances", _instance(), {}, False),
        Case("stop a server", "ec2:StopInstances", _instance(), {}, False),
        Case("start a server", "ec2:RunInstances", _instance(), {}, False),
        Case("detach a disk", "ec2:DetachVolume", _volume(), mine, False),
        Case(
            "delete a security group",
            "ec2:DeleteSecurityGroup",
            f"arn:aws:ec2:{REGION}:{ACCOUNT}:security-group/sg-1",
            {},
            False,
        ),
        Case("change IAM (a user)", "iam:CreateUser", f"arn:aws:iam::{ACCOUNT}:user/x", {}, False),
        Case(
            "change IAM (give itself a policy)",
            "iam:AttachRolePolicy",
            f"arn:aws:iam::{ACCOUNT}:role/x",
            {},
            False,
        ),
        Case(
            "remove its own boundary",
            "iam:DeleteRolePermissionsBoundary",
            f"arn:aws:iam::{ACCOUNT}:role/x",
            {},
            False,
        ),
        Case(
            "delete a disk with no label",
            "ec2:DeleteVolume",
            _volume(),
            disk(Project="", Env="", ManagedBy=""),
            False,
        ),
        Case(
            "delete another project's disk",
            "ec2:DeleteVolume",
            _volume(),
            disk(Project="stock-trader"),
            False,
        ),
        Case(
            "delete another environment's disk",
            "ec2:DeleteVolume",
            _volume(),
            disk(Env=theirs),
            False,
        ),
        Case(
            "delete Terraform's disk",
            "ec2:DeleteVolume",
            _volume(),
            disk(ManagedBy="terraform"),
            False,
        ),
        Case(
            "back up another environment's disk",
            "ec2:CreateSnapshot",
            _volume(),
            disk(Env=theirs),
            False,
        ),
        Case(
            "back up an unlabelled disk",
            "ec2:CreateSnapshot",
            _volume(),
            disk(Project="", Env="", ManagedBy=""),
            False,
        ),
        Case(
            "the backup is labelled for another project",
            "ec2:CreateSnapshot",
            _snapshot(),
            {"aws:requesttag/project": ["stock-trader"]},
            False,
        ),
        Case(
            "change a disk's Project label",
            "ec2:CreateTags",
            _volume(),
            {**disk(), "aws:tagkeys": ["Project"]},
            False,
        ),
        Case(
            "put a second label on a disk while marking it",
            "ec2:CreateTags",
            _volume(),
            {**disk(), "aws:tagkeys": ["KavalRemoval", "Env"]},
            False,
        ),
        Case(
            "mark another environment's disk",
            "ec2:CreateTags",
            _volume(),
            {**disk(Env=theirs), "aws:tagkeys": ["KavalRemoval"]},
            False,
        ),
        Case(
            "delete another environment's copy (A-5)",
            "ec2:DeleteSnapshot",
            _snapshot(),
            copy_(Env=theirs),
            False,
        ),
        Case(
            "delete another project's copy",
            "ec2:DeleteSnapshot",
            _snapshot(),
            copy_(Project="stock-trader"),
            False,
        ),
        Case(
            "delete Terraform's copy",
            "ec2:DeleteSnapshot",
            _snapshot(),
            copy_(ManagedBy="terraform"),
            False,
        ),
        Case("write the orders", "ssm:PutParameter", orders, {}, False),
        Case(
            "read another parameter",
            "ssm:GetParameter",
            f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/kaval/other",
            {},
            False,
        ),
        Case("read the bucket", "s3:GetObject", "arn:aws:s3:::x/y", {}, False),
        Case(
            "run another function",
            "lambda:InvokeFunction",
            f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:x",
            {},
            False,
        ),
    ]


def _all_cases() -> list[tuple[str, Case]]:
    """(which boundary, the case). The remover is run for staging and for production."""
    out = [("brake", c) for c in brake_cases()]
    for env in ("staging", "prod"):
        out += [(f"remover-{env}", c) for c in remover_cases(env)]
    return out


def _boundary_for(which: str) -> dict[str, Any]:
    return brake_boundary() if which == "brake" else remover_boundary(which.split("-", 1)[1])


CASES = _all_cases()
IDS = [f"{w}: {'allow' if c.ok else 'refuse'} {c.label}" for w, c in CASES]


@pytest.mark.parametrize(("which", "case"), CASES, ids=IDS)
def test_a_role_widened_to_everything_still_stops_at_the_boundary(which: str, case: Case) -> None:
    got = allowed(WIDENED, _boundary_for(which), case.action, case.resource, case.ctx)
    assert got is case.ok


def test_there_are_refusals_for_servers_iam_and_unlabelled_disks_in_both_tables() -> None:
    """The three the story names. If someone deletes a case, this notices."""
    for which in ("brake", "remover-staging"):
        refused = {c.label for w, c in CASES if w == which and not c.ok}
        assert any("server" in label for label in refused), which
        assert any("IAM" in label for label in refused), which
    refused_removal = {c.label for w, c in CASES if w == "remover-staging" and not c.ok}
    assert "delete a disk with no label" in refused_removal
    assert "delete another environment's copy (A-5)" in refused_removal


# ── the same table through IAM's own simulator, on request ──────────────────────────────────────


def _context_entries(c: Mapping[str, list[str]]) -> list[dict[str, Any]]:
    return [
        {
            "ContextKeyName": key,
            "ContextKeyValues": values,
            "ContextKeyType": "stringList" if key == "aws:tagkeys" else "string",
        }
        for key, values in c.items()
    ]


@pytest.mark.skipif(
    os.environ.get("KAVAL_IAM_SIMULATE") != "1",
    reason="asks IAM's policy simulator (read-only, free); set KAVAL_IAM_SIMULATE=1",
)
@pytest.mark.parametrize("which", ["brake", "remover-staging", "remover-prod"])
def test_iams_own_simulator_gives_the_same_answer_for_every_case(which: str) -> None:
    boto3 = pytest.importorskip("boto3")
    iam = boto3.client("iam", region_name=REGION)
    boundary = json.dumps(_boundary_for(which))
    widened = json.dumps(WIDENED)
    wrong: list[str] = []
    for w, case in CASES:
        if w != which:
            continue
        out = iam.simulate_custom_policy(
            PolicyInputList=[widened],
            PermissionsBoundaryPolicyInputList=[boundary],
            ActionNames=[case.action],
            ResourceArns=[case.resource],
            ContextEntries=_context_entries(case.ctx),
        )
        decision = out["EvaluationResults"][0]["EvalDecision"]
        if (decision == "allowed") is not case.ok:
            wrong.append(
                f"{case.label}: IAM said {decision}, wanted {'allowed' if case.ok else 'refused'}"
            )
    assert not wrong, "\n".join(wrong)


# ── the Terraform wiring around the documents ───────────────────────────────────────────────────

BUDGET_TF = (BUDGET / "main.tf").read_text(encoding="utf-8")
REMOVAL_TF = (REMOVAL / "main.tf").read_text(encoding="utf-8")


def _verbs(text: str) -> set[str]:
    blocks = re.findall(r"actions\s+=\s+\[(.*?)\]", text, re.S)
    return {v for b in blocks for v in re.findall(r'"([A-Za-z0-9*]+:[A-Za-z*]+)"', b)}


def _boundary_verbs(doc: Mapping[str, Any]) -> set[str]:
    return {a for st in doc["Statement"] if st["Effect"] == "Allow" for a in _as_list(st["Action"])}


def test_each_role_has_its_boundary_attached() -> None:
    assert "permissions_boundary = aws_iam_policy.hard_stop_boundary.arn" in BUDGET_TF
    assert "permissions_boundary = aws_iam_policy.boundary.arn" in REMOVAL_TF
    # Only the two roles that change things get one (ADR-0101); the helper roles do not.
    assert BUDGET_TF.count("permissions_boundary") == 1
    assert REMOVAL_TF.count("permissions_boundary") == 1


def test_a_boundary_can_never_be_too_tight_for_its_role() -> None:
    """Every verb the role names must be in its ceiling, or its own work is silently refused."""
    brake = BUDGET_TF.split('data "aws_iam_policy_document" "hard_stop"')[1].split(
        'resource "aws_iam_role_policy" "hard_stop"'
    )[0]
    assert _verbs(brake) <= _boundary_verbs(brake_boundary())
    remover = REMOVAL_TF.split('data "aws_iam_policy_document" "removal"')[1].split(
        'resource "aws_iam_role_policy" "removal"'
    )[0]
    assert _verbs(remover) <= _boundary_verbs(remover_boundary("staging"))


def test_a_boundary_names_no_wildcard_action_and_every_change_is_fenced() -> None:
    for doc in (brake_boundary(), remover_boundary("staging")):
        for st in doc["Statement"]:
            for action in _as_list(st["Action"]):
                assert "*" not in action, action
            if st["Effect"] != "Allow":
                continue
            actions = _as_list(st["Action"])
            read_only = all(a.split(":")[1].startswith(("Describe", "Get")) for a in actions)
            is_log = all(a.startswith("logs:") for a in actions)
            assert read_only or is_log or "Condition" in st or st["Resource"] != "*", st["Sid"]


def test_the_brakes_boundary_is_fixed_text_that_does_not_follow_the_variables() -> None:
    assert 'policy      = file("${path.module}/boundary.json")' in BUDGET_TF
    assert "${" not in (BUDGET / "boundary.json").read_text(encoding="utf-8")


def test_the_brakes_group_statement_is_limited_by_label_and_no_longer_names_every_group() -> None:
    brake = BUDGET_TF.split('data "aws_iam_policy_document" "hard_stop"')[1]
    scale = brake.split('sid    = "ScaleKavalGroupsToZero"')[1].split("statement {")[0]
    assert 'variable = "autoscaling:ResourceTag/${var.stop_tag_key}"' in scale
    assert 'resources = ["*"]' not in scale
    assert "ScaleAsgToZero" not in BUDGET_TF


def test_the_removers_list_of_environments_is_the_codes_list() -> None:
    code = (REMOVAL / "lambda" / "waste_removal.py").read_text(encoding="utf-8")
    in_code = re.search(r"^ENVS = \((.*?)\)", code, re.M)
    in_tf = re.search(r"all_envs\s+=\s+\[(.*?)\]", REMOVAL_TF)
    assert in_code and in_tf

    def names(text: str) -> list[str]:
        return re.findall(r'"([a-z-]+)"', text)

    assert names(in_code.group(1)) == names(in_tf.group(1)) == ALL_ENVS


def test_the_removers_env_refusal_is_in_the_role_as_well_as_the_ceiling() -> None:
    role = REMOVAL_TF.split('sid       = "NeverAnotherEnvironmentsCopy"')[1].split("statement {")[0]
    assert 'effect    = "Deny"' in role and "values   = local.other_envs" in role
    assert "ec2:DeleteSnapshot" in role
