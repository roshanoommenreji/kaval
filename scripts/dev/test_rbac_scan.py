"""The permission scan finds what it claims to find, and stays quiet on a tight chart."""

from __future__ import annotations

from typing import Any

import rbac_scan


def workload(name: str, account: str | None = None, mount: bool | None = None) -> dict[str, Any]:
    pod: dict[str, Any] = {"containers": []}
    if account:
        pod["serviceAccountName"] = account
    if mount is not None:
        pod["automountServiceAccountToken"] = mount
    return {"kind": "Deployment", "metadata": {"name": name}, "spec": {"template": {"spec": pod}}}


def role(name: str, rules: list[dict[str, Any]]) -> dict[str, Any]:
    return {"kind": "Role", "metadata": {"name": name, "namespace": "demo"}, "rules": rules}


def binding(role_name: str, account: str) -> dict[str, Any]:
    return {
        "kind": "RoleBinding",
        "metadata": {"name": role_name, "namespace": "demo"},
        "roleRef": {"kind": "Role", "name": role_name},
        "subjects": [{"kind": "ServiceAccount", "name": account, "namespace": "kaval"}],
    }


TIGHT = [
    {"kind": "ServiceAccount", "metadata": {"name": "executor"}},
    role("executor", [{"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "delete"]}]),
    binding("executor", "executor"),
    workload("executor", account="executor"),
    workload("agent", mount=False),
]


def test_a_tight_chart_has_no_findings() -> None:
    result = rbac_scan.review(TIGHT)
    assert not result.serious
    assert result.spare_mounts == []
    assert ("Deployment/agent", "default", "no", False) in result.workloads


def test_a_wildcard_is_a_serious_finding() -> None:
    docs = [
        *TIGHT,
        role("wide", [{"apiGroups": ["*"], "resources": ["*"], "verbs": ["*"]}]),
        binding("wide", "executor"),
    ]
    result = rbac_scan.review(docs)
    assert result.serious and len(result.wildcards) == 1


def test_a_rule_that_reads_secrets_is_a_serious_finding() -> None:
    docs = [
        *TIGHT,
        role("peek", [{"apiGroups": [""], "resources": ["secrets"], "verbs": ["get"]}]),
        binding("peek", "executor"),
    ]
    result = rbac_scan.review(docs)
    assert result.serious and len(result.store_rules) == 1


def test_a_token_mounted_for_an_account_with_no_role_is_reported_but_not_serious() -> None:
    result = rbac_scan.review([*TIGHT, workload("gateway")])
    assert result.spare_mounts == [
        "Deployment/gateway mounts the token of 'default', which is bound to no role"
    ]
    assert not result.serious


def test_a_cron_job_is_read_like_any_other_workload() -> None:
    cron = {
        "kind": "CronJob",
        "metadata": {"name": "chaos"},
        "spec": {
            "jobTemplate": {"spec": {"template": {"spec": {"serviceAccountName": "executor"}}}}
        },
    }
    result = rbac_scan.review([*TIGHT, cron])
    assert ("CronJob/chaos", "executor", "yes", True) in result.workloads
