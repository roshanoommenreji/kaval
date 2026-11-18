"""The pull-request plan may read Terraform's record, so the way in is held shut (KAV-89, ADR-0102).

Two things are checked, each with a made-up broken version that must be refused:

* the role (infra/modules/plan-reader): trusted only for a pull request of this repository, no
  wildcard, and able only to read the two records, the plan settings and a few lookups. No write,
  no delete, no other bucket, no `*` action;
* the workflow (.github/workflows/terraform-plan.yml): triggered by `pull_request` and never
  `pull_request_target`, only for this repository's own pull requests, a minimal token, the
  role's address from a secret, the plan made without a refresh and without a lock, and its text
  kept out of the public log.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "infra" / "modules" / "plan-reader" / "main.tf"
WORKFLOW = ROOT / ".github" / "workflows" / "terraform-plan.yml"
STATE_ENV = ROOT / "infra" / "envs" / "state" / "main.tf"

# What a read-only plan may do. Anything outside this set fails the test below.
ALLOWED_ACTIONS = {
    "s3:ListBucket",
    "s3:GetObject",
    "ssm:GetParameter",
    "iam:GetOpenIDConnectProvider",
    "ec2:DescribeSubnets",
}


def strip_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def actions_in(tf: str) -> set[str]:
    """The actions the role's own policy allows (not the trust policy's sign-in action)."""
    found: set[str] = set()
    policy = strip_comments(tf).split('data "aws_iam_policy_document" "plan"', 1)[1]
    for block in re.findall(r"actions\s*=\s*\[([^\]]*)\]", policy):
        found |= set(re.findall(r'"([^"]+)"', block))
    return found


def trust_problems(tf: str) -> list[str]:
    """Everything wrong with the role's trust policy."""
    code = strip_comments(tf)
    trust = code.split('data "aws_iam_policy_document" "trust"', 1)[1].split("\nresource ", 1)[0]
    found: list[str] = []
    if "StringLike" in trust:
        found.append("trust uses StringLike (a wildcard could match more)")
    subs = re.findall(
        r'variable\s*=\s*"token\.actions\.githubusercontent\.com:sub".*?values\s*=\s*\[([^\]]*)\]',
        trust,
        re.S,
    )
    if len(subs) != 1:
        found.append(f"expected exactly one sub condition, found {len(subs)}")
    else:
        values = re.findall(r'"([^"]*)"', subs[0])
        if len(values) != 1 or not re.fullmatch(r"repo:\$\{[a-z_.]+\}:pull_request", values[0]):
            found.append(f"sub is not exactly this repository's pull_request: {values}")
        if "*" in subs[0]:
            found.append("sub contains a wildcard")
    if 'token.actions.githubusercontent.com:aud"' not in trust:
        found.append("no audience condition")
    return found


def policy_problems(tf: str) -> list[str]:
    """Everything wrong with what the role may do."""
    found = [f"action not allowed: {a}" for a in sorted(actions_in(tf) - ALLOWED_ACTIONS)]
    code = strip_comments(tf)
    if re.search(
        r"s3:GetObject.{0,200}?resources\s*=\s*\[\s*var\.state_bucket_arn\s*\]", code, re.S
    ):
        found.append("GetObject on the whole bucket")
    if "/*" in re.sub(r"parameter/[a-z/-]*\*|ami-amazon-linux-latest/\*", "", code):
        found.append("a wildcard resource path")
    return found


def test_the_trust_names_one_trigger_and_no_wildcard() -> None:
    assert trust_problems(MODULE.read_text(encoding="utf-8")) == []


def test_the_role_can_only_read() -> None:
    tf = MODULE.read_text(encoding="utf-8")
    assert policy_problems(tf) == []
    assert actions_in(tf) == ALLOWED_ACTIONS


def test_the_role_reads_only_the_named_records() -> None:
    tf = strip_comments(MODULE.read_text(encoding="utf-8"))
    assert 'resources = [for k in var.state_keys : "${var.state_bucket_arn}/${k}"]' in tf
    env = strip_comments(STATE_ENV.read_text(encoding="utf-8"))
    assert 'state_keys        = ["prod/terraform.tfstate", "state/terraform.tfstate"]' in env


def test_a_wildcard_trust_is_refused() -> None:
    good = MODULE.read_text(encoding="utf-8")
    bad = good.replace(
        'test     = "StringEquals"\n      variable = "token.actions.githubusercontent.com:sub"',
        'test     = "StringLike"\n      variable = "token.actions.githubusercontent.com:sub"',
    )
    assert any("StringLike" in p for p in trust_problems(bad))
    main_branch = good.replace(":pull_request", ":ref:refs/heads/main")
    assert trust_problems(main_branch)


def test_a_role_that_can_write_is_refused() -> None:
    good = MODULE.read_text(encoding="utf-8")
    bad = good.replace(
        'actions   = ["s3:GetObject"]', 'actions   = ["s3:GetObject", "s3:PutObject"]'
    )
    assert any("s3:PutObject" in p for p in policy_problems(bad))
    wide = good.replace('actions   = ["s3:GetObject"]', 'actions   = ["s3:*"]')
    assert policy_problems(wide)


# --- the workflow --------------------------------------------------------------------------------


def load() -> dict[str, Any]:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    return doc


def triggers(doc: dict[Any, Any]) -> set[str]:
    on = doc.get("on", doc.get(True))  # YAML reads a bare `on` as the boolean True
    return set(on) if isinstance(on, dict) else {str(on)}


def workflow_problems(doc: dict[str, Any], raw: str) -> list[str]:
    found: list[str] = []
    if triggers(doc) != {"pull_request"}:
        found.append(f"triggers must be exactly pull_request, got {sorted(triggers(doc))}")
    if doc.get("permissions") != {}:
        found.append("the workflow-level token is not empty")
    job = doc["jobs"]["plan"]
    if job.get("permissions") != {"contents": "read", "id-token": "write"}:
        found.append(
            f"job permissions are not contents:read + id-token:write: {job.get('permissions')}"
        )
    cond = str(job.get("if", ""))
    if "head.repo.full_name == github.repository" not in cond:
        found.append("the job does not refuse a pull request from a fork")
    login = [
        s
        for s in job["steps"]
        if str(s.get("uses", "")).startswith("aws-actions/configure-aws-credentials@")
    ]
    if len(login) != 1 or login[0]["with"]["role-to-assume"] != "${{ secrets.AWS_PLAN_ROLE_ARN }}":
        found.append("the role address must come from the secret AWS_PLAN_ROLE_ARN")
    run = "\n".join(str(s.get("run", "")) for s in job["steps"])
    for needed in ("-refresh=false", "-lock=false"):
        if needed not in run:
            found.append(f"the plan lacks {needed}")
    if "terraform apply" in run or "-auto-approve" in run:
        found.append("the workflow applies")
    if re.search(r"terraform plan[^\n]*\|\s*tee|cat plan\.log", run):
        found.append("the plan's own text reaches the log")
    if "plan_summary.py" not in run:
        found.append("the summary is not built by plan_summary.py")
    for uses in re.findall(r"uses:\s*(\S+)", raw):
        if not re.search(r"@[0-9a-f]{40}$", uses):
            found.append(f"action not pinned to a full commit: {uses}")
    if "pull_request_target" in raw.replace("never `pull_request_target`", ""):
        found.append("pull_request_target appears")
    return found


def test_the_workflow_is_held_shut() -> None:
    assert workflow_problems(load(), WORKFLOW.read_text(encoding="utf-8")) == []


def test_the_workflow_plans_prod_and_state_only() -> None:
    assert load()["jobs"]["plan"]["strategy"]["matrix"]["env"] == ["prod", "state"]


@pytest.mark.parametrize(
    ("old", "new", "expect"),
    [
        ("  pull_request:\n    paths:", "  pull_request_target:\n    paths:", "triggers"),
        ("permissions: {}\n\n# A newer", "permissions: write-all\n\n# A newer", "token"),
        ("head.repo.full_name == github.repository && ", "", "fork"),
        ("-refresh=false", "", "-refresh=false"),
        (
            "secrets.AWS_PLAN_ROLE_ARN }}\n          mask",
            "vars.AWS_PLAN_ROLE_ARN }}\n          mask",
            "secret",
        ),
        ("terraform show -json plan.tfplan > plan.json", "cat plan.log", "log"),
    ],
)
def test_a_loosened_workflow_is_refused(old: str, new: str, expect: str) -> None:
    raw = WORKFLOW.read_text(encoding="utf-8")
    assert old in raw, f"the test's edit no longer matches the workflow: {old!r}"
    bad = raw.replace(old, new)
    found = workflow_problems(yaml.safe_load(bad), bad)
    assert any(expect in p for p in found), found
