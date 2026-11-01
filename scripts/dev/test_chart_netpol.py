"""The chart's network rules close the gateway to every pod but the one that needs it (KAV-115,
ADR-0073).

Renders the real chart with `helm template`; skipped without helm (CI's helm job renders it too).
The live proof is staging: a pod in the demo namespace cannot reach the gateway, and the node's
own health checks and smoke test still can."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy" / "charts" / "kaval"
needs_helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
COMPONENT = "app.kubernetes.io/component"


def _render(env: str) -> list[dict[str, Any]]:
    out = subprocess.run(
        ["helm", "template", f"kaval-{env}", str(CHART), "-f",
         str(ROOT / "deploy" / "environments" / env / "values.yaml")],
        check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def _policies(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [d for d in docs if d["kind"] == "NetworkPolicy"]


def _components(docs: list[dict[str, Any]]) -> set[str]:
    """The component label of every pod the chart makes."""
    found: set[str] = set()
    for d in docs:
        if d["kind"] in ("Deployment", "StatefulSet"):
            found.add(d["spec"]["template"]["metadata"]["labels"][COMPONENT])
    return found


def _sources_for(policies: list[dict[str, Any]], component: str, port: int) -> set[str]:
    """Which components may open a connection to `port` of pods of `component`."""
    out: set[str] = set()
    for p in policies:
        selector = p["spec"]["podSelector"].get("matchLabels")
        if selector is not None and selector.get(COMPONENT) != component:
            continue
        for rule in p["spec"].get("ingress", []):
            if port not in [x["port"] for x in rule["ports"]]:
                continue
            for peer in rule["from"]:
                out.add(peer["podSelector"]["matchLabels"][COMPONENT])
    return out


@needs_helm
@pytest.mark.parametrize("env", ["local", "lab-eks"])
def test_the_rules_are_off_where_they_are_not_asked_for(env: str) -> None:
    assert _policies(_render(env)) == []


@needs_helm
@pytest.mark.parametrize("env", ["staging", "prod"])
def test_everything_in_the_namespace_is_closed_by_default(env: str) -> None:
    deny = [p for p in _policies(_render(env))
            if p["metadata"]["name"].endswith("default-deny-ingress")]
    assert len(deny) == 1
    assert deny[0]["spec"] == {"podSelector": {}, "policyTypes": ["Ingress"]}


@needs_helm
@pytest.mark.parametrize("env", ["staging", "prod"])
def test_the_gateway_accepts_the_poller_and_no_other_pod(env: str) -> None:
    policies = _policies(_render(env))
    assert _sources_for(policies, "gateway", 8000) == {"collector-prometheus"}


@needs_helm
@pytest.mark.parametrize("env", ["staging", "prod"])
def test_the_gateway_door_is_the_gateways_own_port_and_no_other(env: str) -> None:
    docs = _render(env)
    (rule,) = [p for p in _policies(docs)
               if p["metadata"]["name"].endswith("gateway-from-the-poller")]
    ports = [port for r in rule["spec"]["ingress"] for port in r["ports"]]
    assert ports == [{"protocol": "TCP", "port": 8000}]


@needs_helm
@pytest.mark.parametrize("env", ["staging", "prod"])
def test_no_rule_opens_a_door_to_an_address_range_or_another_namespace(env: str) -> None:
    for p in _policies(_render(env)):
        for rule in p["spec"].get("ingress", []):
            name = p["metadata"]["name"]
            assert rule.get("from"), f"{name}: a rule with no `from` allows everyone"
            assert rule.get("ports"), f"{name}: a rule with no ports allows every port"
            for peer in rule["from"]:
                assert set(peer) == {"podSelector"}, p["metadata"]["name"]


@needs_helm
@pytest.mark.parametrize("env", ["staging", "prod"])
def test_every_rule_names_a_pod_that_really_exists(env: str) -> None:
    """A rename that left a rule pointing at nothing would shut a door silently."""
    docs = _render(env)
    exists = _components(docs)
    for p in _policies(docs):
        named = set(p["spec"]["podSelector"].get("matchLabels", {}).values())
        for rule in p["spec"].get("ingress", []):
            for peer in rule["from"]:
                named |= set(peer["podSelector"]["matchLabels"].values())
        components = {v for v in named if not v.startswith("kaval-")}
        assert components <= exists, (p["metadata"]["name"], components - exists)


@needs_helm
@pytest.mark.parametrize("env", ["staging", "prod"])
def test_prometheus_can_read_every_health_page_and_only_prometheus(env: str) -> None:
    docs = _render(env)
    (rule,) = [p for p in _policies(docs)
               if p["metadata"]["name"].endswith("metrics-from-prometheus")]
    assert rule["spec"]["podSelector"] == {}  # every pod
    assert [peer["podSelector"]["matchLabels"][COMPONENT] for r in rule["spec"]["ingress"]
            for peer in r["from"]] == ["prometheus"]
    ports = [p for r in rule["spec"]["ingress"] for p in r["ports"]]
    assert ports == [{"protocol": "TCP", "port": 9100}]


@needs_helm
@pytest.mark.parametrize("env", ["staging", "prod"])
def test_the_model_answers_the_diagnoser_only(env: str) -> None:
    assert _sources_for(_policies(_render(env)), "ollama", 11434) == {"diagnoser"}


def test_the_default_is_off_so_a_plain_install_is_unchanged() -> None:
    values = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    assert values["networkPolicy"] == {"enabled": False}
