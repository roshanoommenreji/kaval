"""Safe removal in the chart (KAV-95, ADR-0063): absent where off, narrow where present.

Renders the real chart with `helm template`, so it needs helm; without it the tests skip. The rights
are the point: the executor gets ONE ConfigMap it may read and patch, the cost reader's pod gets
four it may only read, and nothing else in the cluster changes."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy" / "charts" / "kaval"
pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")

ON = (
    "--set",
    "costReader.enabled=true",
    "--set",
    "costReader.wasteScan.enabled=true",
    "--set",
    "removal.enabled=true",
)


def _helm(env: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "helm",
            "template",
            f"kaval-{env}",
            str(CHART),
            "-f",
            str(ROOT / "deploy" / "environments" / env / "values.yaml"),
            *extra,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _render(env: str, *extra: str) -> list[str]:
    done = _helm(env, *extra)
    assert done.returncode == 0, done.stderr
    return [doc for doc in re.split(r"(?m)^---\s*$", done.stdout) if doc.strip()]


def _find(docs: list[str], kind: str, name: str) -> str:
    for doc in docs:
        if re.search(rf"(?m)^kind: {kind}$", doc) and re.search(rf"(?m)^  name: {name}$", doc):
            return doc
    raise AssertionError(f"no {kind} named {name}")


@pytest.mark.parametrize("env", ["local"])
def test_where_it_is_not_switched_on_nothing_of_it_is_rendered(env: str) -> None:
    text = "\n".join(_render(env))
    assert "removal-orders" not in text and "KAVAL_REMOVAL_ORDERS" not in text
    assert "removal-result" not in text
    assert "kaval-removal.rules" not in text and "executor-orders" not in text
    assert "--removal-result" not in text
    assert 'name: KAVAL_REMOVAL_ENABLED\n              value: "false"' in text


def test_staging_and_production_have_it_on_and_local_does_not() -> None:
    for env in ("staging", "prod"):
        text = "\n".join(_render(env))
        assert f"kaval-{env}-removal-orders" in text, env
        assert 'name: KAVAL_REMOVAL_ENABLED\n              value: "true"' in text, env
        assert 'name: KAVAL_REMOVAL_ENABLED\n              value: "false"' not in text, env
    assert "removal-orders" not in "\n".join(_render("local"))


def test_switched_on_the_executor_may_get_and_patch_one_configmap_by_name_only() -> None:
    docs = _render("staging", *ON)
    role = _find(docs, "Role", "kaval-staging-executor-orders")
    assert "resourceNames: [kaval-staging-removal-orders]" in role
    assert 'resources: ["configmaps"]' in role and 'verbs: ["get", "patch"]' in role
    rules = role.split("rules:")[1]  # the comment above the rule names verbs it does not grant
    for verb in ("create", "delete", "list", "watch", "update", "deletecollection", '"*"'):
        assert verb not in rules, verb
    binding = _find(docs, "RoleBinding", "kaval-staging-executor-orders")
    assert "name: kaval-staging-executor\n" in binding.replace("\r\n", "\n")


def test_the_only_right_the_executor_gains_in_its_own_namespace_is_that_one() -> None:
    docs = _render("staging", *ON)
    in_own = [
        d
        for d in docs
        if re.search(r"(?m)^kind: RoleBinding$", d)
        and re.search(r"(?m)^  namespace: default$", d)
        and "name: kaval-staging-executor\n" in d.replace("\r\n", "\n")
    ]
    assert len(in_own) == 1 and "name: kaval-staging-executor-orders" in in_own[0]


def test_the_orders_configmap_is_made_empty_and_the_executor_is_told_where_it_is() -> None:
    docs = _render("staging", *ON)
    cm = _find(docs, "ConfigMap", "kaval-staging-removal-orders")
    assert "orders.json: '{\"orders\":[]}'" in cm
    executor = _find(docs, "Deployment", "kaval-staging-executor")
    assert 'name: KAVAL_REMOVAL_ENABLED\n              value: "true"' in executor
    assert "fieldPath: metadata.namespace" in executor
    assert (
        'name: KAVAL_REMOVAL_ORDERS\n              value: "kaval-staging-removal-orders"'
        in executor
    )


def test_the_cost_reader_reads_the_answers_and_the_orders_and_cannot_write_either() -> None:
    docs = _render("staging", *ON)
    role = _find(docs, "Role", "kaval-staging-cost")
    for name in ("cost-pull", "waste-scan", "removal-result", "removal-orders"):
        assert f"kaval-staging-{name}" in role, name
    assert 'verbs: ["get"]' in role
    for verb in ("create", "patch", "update", "delete", "list", "watch"):
        assert verb not in role.split("rules:")[1], verb
    deployment = _find(docs, "Deployment", "kaval-staging-collector-cost")
    assert '- "--removal-result"\n            - "kaval-staging-removal-result"' in deployment
    assert '- "--removal-orders"\n            - "kaval-staging-removal-orders"' in deployment


def test_the_planner_in_the_diagnoser_is_told_the_switch_and_the_grace() -> None:
    docs = _render("staging", "--set", "diagnoser.enabled=true", *ON)
    diagnoser = _find(docs, "Deployment", "kaval-staging-diagnoser")
    assert 'name: KAVAL_REMOVAL_ENABLED\n              value: "true"' in diagnoser
    assert 'name: KAVAL_REMOVAL_GRACE_HOURS\n              value: "1"' in diagnoser


def test_the_alarm_for_an_unanswered_order_is_loaded_only_where_removal_is_on() -> None:
    on = "\n".join(_render("staging", *ON))
    assert "kaval-removal.rules.yaml" in on and "KavalRemovalOrderUnanswered" in on
    assert "/etc/prometheus/kaval-removal.rules.yaml" in on
    off = "\n".join(_render("staging", "--set", "removal.enabled=false"))
    assert "KavalRemovalOrderUnanswered" not in off


def test_removal_without_the_waste_scan_being_read_refuses_to_render() -> None:
    done = _helm(
        "staging",
        "--set",
        "costReader.enabled=true",
        "--set",
        "costReader.wasteScan.enabled=false",
        "--set",
        "removal.enabled=true",
    )
    assert done.returncode != 0
    assert "removal.enabled needs costReader.wasteScan.enabled" in done.stderr


def test_removal_without_the_cost_reader_refuses_to_render() -> None:
    done = _helm("local", "--set", "removal.enabled=true")
    assert done.returncode != 0
    assert "removal.enabled needs costReader" in done.stderr


def test_the_chart_never_gives_the_cluster_an_aws_login_for_removal() -> None:
    text = "\n".join(_render("staging", *ON))
    for word in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        assert word not in text, word


def test_the_grace_in_each_environment_equals_the_workers() -> None:
    """The planner asks after the grace; the worker refuses the delete step if it is early."""
    variables = (ROOT / "infra" / "modules" / "waste-removal" / "variables.tf").read_text(
        encoding="utf-8"
    )
    found = re.search(r'variable "grace_hours".*?default\s*=\s*(\d+)', variables, re.S)
    assert found is not None
    worker_default = float(found.group(1))
    staging_tf = (ROOT / "infra" / "envs" / "staging" / "main.tf").read_text(encoding="utf-8")
    found = re.search(r'waste_removal".*?grace_hours\s*=\s*(\d+)', staging_tf, re.S)
    assert found is not None
    worker_staging = float(found.group(1))
    prod_tf = (ROOT / "infra" / "envs" / "prod" / "main.tf").read_text(encoding="utf-8")
    prod_block = prod_tf.split('module "waste_removal"')[1].split("}")[0]
    assert "grace_hours" not in prod_block  # production keeps the module's default
    values = (CHART / "values.yaml").read_text(encoding="utf-8")
    found = re.search(r"removal:.*?graceHours:\s*(\d+)", values, re.S)
    assert found is not None
    chart_default = float(found.group(1))
    staging_values = (ROOT / "deploy" / "environments" / "staging" / "values.yaml").read_text(
        encoding="utf-8"
    )
    found = re.search(
        r"removal:\s*\n(?:#.*\n)*(?:\s*enabled:.*\n)?\s*graceHours:\s*(\d+)", staging_values
    )
    assert found is not None
    staging_chart = float(found.group(1))
    prod_values = (ROOT / "deploy" / "environments" / "prod" / "values.yaml").read_text(
        encoding="utf-8"
    )
    found = re.search(
        r"(?m)^removal:\s*\n(?:#.*\n)*(?:\s*enabled:.*\n)?\s*graceHours:\s*(\d+)", prod_values
    )
    assert found is not None
    prod_chart = float(found.group(1))
    assert chart_default == worker_default == prod_chart == 168
    assert staging_chart == worker_staging == 1
