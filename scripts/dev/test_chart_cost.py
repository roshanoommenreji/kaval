"""The cost reader in the chart (KAV-92, ADR-0060): absent where not switched on, narrow where present.

Renders the real chart with `helm template`, so it needs helm; without it the tests skip. The reader
is switched on with `--set` here because no environment's own file turns it on yet: staging does
after the build that contains it has been pinned, production at its next bring-up."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy" / "charts" / "kaval"
pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")

ON = ("--set", "costReader.enabled=true")


def _helm(env: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["helm", "template", f"kaval-{env}", str(CHART), "-f",
         str(ROOT / "deploy" / "environments" / env / "values.yaml"), *extra],
        capture_output=True, text=True, encoding="utf-8",
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


@pytest.mark.parametrize("env", ["local", "staging", "prod"])
def test_where_it_is_not_switched_on_nothing_of_it_is_rendered(env: str) -> None:
    if env == "staging":
        pytest.skip("staging turns it on in the follow-up change, once the build is pinned")
    text = "\n".join(_render(env))
    assert "collector-cost" not in text and "cost-pull" not in text
    assert "kaval-cost.rules" not in text


def test_switched_on_it_is_a_deployment_a_role_and_a_scrape_target() -> None:
    docs = _render("staging", *ON)
    deployment = _find(docs, "Deployment", "kaval-staging-collector-cost")
    assert "kaval_collector.cost_explorer" in deployment
    assert "serviceAccountName: kaval-staging-cost" in deployment
    _find(docs, "Service", "kaval-staging-collector-cost-metrics")
    assert "job_name: kaval-collector-cost" in "\n".join(docs)


def test_it_may_get_one_configmap_by_name_and_nothing_else() -> None:
    role = _find(_render("staging", *ON), "Role", "kaval-staging-cost")
    assert re.findall(r"(?m)^\s+verbs: \[(.*)\]$", role) == ['"get"']
    assert re.findall(r"(?m)^\s+resources: \[(.*)\]$", role) == ['"configmaps"']
    assert "resourceNames: [kaval-staging-cost-pull]" in role
    for wider in ("list", "watch", "secrets", "pods", "create", "update", "delete", "patch"):
        assert wider not in role


def test_the_configmap_it_reads_is_the_one_the_servers_job_writes() -> None:
    deployment = _find(_render("staging", *ON), "Deployment", "kaval-staging-collector-cost")
    assert "kaval-staging-cost-pull" in deployment
    node = (ROOT / "infra" / "modules" / "node" / "user_data.sh.tftpl").read_text(encoding="utf-8")
    assert '"$NS-cost-pull"' in node and 'NS="${name_prefix}"' in node  # prefix == release name


def test_it_holds_no_aws_login_of_any_kind() -> None:
    docs = _render("staging", *ON)
    mine = "\n".join(_find(docs, k, n) for k, n in (
        ("Deployment", "kaval-staging-collector-cost"), ("Role", "kaval-staging-cost")))
    for word in ("AWS_", "aws_access", "aws-credentials", "eks.amazonaws.com", "role-arn"):
        assert word not in mine, word


def test_it_uses_the_collectors_database_role_and_nothing_stronger() -> None:
    deployment = _find(_render("staging", *ON), "Deployment", "kaval-staging-collector-cost")
    assert "kaval-postgres-collector" in deployment
    for stronger in ("kaval-postgres-admin", "kaval-postgres-executor", "kaval-postgres-gateway",
                     "kaval-postgres-agent"):
        assert stronger not in deployment


def test_it_runs_with_no_privileges_and_a_small_memory_limit() -> None:
    deployment = _find(_render("staging", *ON), "Deployment", "kaval-staging-collector-cost")
    assert 'drop: ["ALL"]' in deployment and "allowPrivilegeEscalation: false" in deployment
    assert re.search(r"(?m)^\s+memory: 128Mi$", deployment)
    assert "type: Recreate" in deployment


def test_it_is_watched_and_its_alarm_is_loaded() -> None:
    text = "\n".join(_render("staging", *ON))
    assert "kaval-cost.rules.yaml" in text
    for alert in ("KavalCostPullFailing", "KavalCostPullStale", "KavalCostReaderMissing"):
        assert alert in text


def test_the_alarm_is_not_loaded_where_the_reader_is_not() -> None:
    assert "kaval-cost.rules" not in "\n".join(_render("staging"))


def test_a_reader_without_prometheus_is_refused_at_render_time() -> None:
    done = _helm("staging", *ON, "--set", "prometheus.enabled=false")
    assert done.returncode != 0 and "needs prometheus.enabled" in done.stderr
