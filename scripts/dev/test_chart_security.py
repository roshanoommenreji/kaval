"""Every pod the chart makes passes Pod Security `restricted`, and the gateway's checks use the
two probe pages (KAV-105, KAV-106, ADR-0068).

Found live in Lab 66: the chart passed `baseline` but failed `restricted` on all seven pods. Renders
the real chart for every environment with `helm template`, so it needs helm; without it the tests
skip (CI's helm job renders the chart too). The live proof is staging's smoke run, plus a throwaway
namespace that enforces `restricted` (Lab 67)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy" / "charts" / "kaval"
ENVS = ["staging", "prod", "local", "lab-eks"]
needs_helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def _render(env: str) -> list[dict[str, Any]]:
    out = subprocess.run(
        ["helm", "template", f"kaval-{env}", str(CHART), "-f",
         str(ROOT / "deploy" / "environments" / env / "values.yaml")],
        check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def _pod_specs(docs: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    """(where, pod spec) for every workload kind."""
    found: list[tuple[str, dict[str, Any]]] = []
    for d in docs:
        name = f"{d['kind']}/{d['metadata']['name']}"
        if d["kind"] in ("Deployment", "StatefulSet", "DaemonSet", "Job"):
            found.append((name, d["spec"]["template"]["spec"]))
        elif d["kind"] == "CronJob":
            found.append((name, d["spec"]["jobTemplate"]["spec"]["template"]["spec"]))
    return found


def violations(where: str, pod: dict[str, Any]) -> list[str]:
    """What stops this pod passing `restricted`. Empty means it passes."""
    problems: list[str] = []
    psc = pod.get("securityContext") or {}
    for flag in ("hostNetwork", "hostPID", "hostIPC"):
        if pod.get(flag):
            problems.append(f"{where}: {flag}")
    for c in (pod.get("initContainers") or []) + pod["containers"]:
        who = f"{where}/{c['name']}"
        sc = c.get("securityContext") or {}
        caps = sc.get("capabilities") or {}
        if sc.get("privileged"):
            problems.append(f"{who}: privileged")
        if sc.get("allowPrivilegeEscalation") is not False:
            problems.append(f"{who}: allowPrivilegeEscalation is not false")
        if "ALL" not in caps.get("drop", []):
            problems.append(f"{who}: capabilities not dropped")
        if caps.get("add", []) not in ([], ["NET_BIND_SERVICE"]):
            problems.append(f"{who}: adds capabilities")
        if sc.get("runAsNonRoot", psc.get("runAsNonRoot")) is not True:
            problems.append(f"{who}: runAsNonRoot is not true")
        user = sc.get("runAsUser", psc.get("runAsUser"))
        if not isinstance(user, int) or user == 0:
            problems.append(f"{who}: no numeric non-zero runAsUser")
        seccomp = (sc.get("seccompProfile") or psc.get("seccompProfile") or {}).get("type")
        if seccomp not in ("RuntimeDefault", "Localhost"):
            problems.append(f"{who}: no seccompProfile")
    return problems


@needs_helm
@pytest.mark.parametrize("env", ENVS)
def test_every_pod_passes_restricted(env: str) -> None:
    pods = _pod_specs(_render(env))
    assert pods, "rendered no workloads at all"
    problems = [p for where, pod in pods for p in violations(where, pod)]
    assert not problems, "\n".join(problems)


@needs_helm
def test_the_in_cluster_database_runs_as_its_own_user_on_a_subfolder() -> None:
    """A non-root Postgres cannot take over the disk's top folder (initdb must own what it
    initialises), so the data lives in a subfolder the group may create."""
    docs = _render("local")
    db = next(d for d in docs if d["kind"] == "Deployment"
              and d["metadata"]["name"].endswith("-postgres"))
    pod = db["spec"]["template"]["spec"]
    assert pod["securityContext"]["runAsUser"] == 999
    assert pod["securityContext"]["fsGroup"] == 999
    env = {e["name"]: e.get("value") for e in pod["containers"][0].get("env", [])}
    assert env.get("PGDATA") == "/var/lib/postgresql/data/pgdata"


def test_the_checker_itself_refuses_a_pod_that_is_not_locked_down() -> None:
    """The negative case: a pod like yesterday's chart is reported, setting by setting."""
    loose = {"containers": [{"name": "app", "image": "x"}]}
    found = violations("Deployment/loose", loose)
    assert len(found) == 5, found
    root = {"securityContext": {"runAsNonRoot": True, "runAsUser": 0,
                                "seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [{"name": "app", "securityContext": {
                "allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}}]}
    assert violations("Deployment/root", root) == [
        "Deployment/root/app: no numeric non-zero runAsUser"]


def anonymous_pg_isready(where: str, pod: dict[str, Any]) -> list[str]:
    """Containers that run pg_isready without naming the database user.

    Found live on staging (KAV-105 follow-up): the pods now run as uid 10001, which the Postgres
    image has no account for, so pg_isready cannot work out a default user and answers "no
    attempt" for ever. The start-up wait then never ends. Naming the user (-U) avoids the lookup."""
    found = []
    for c in [*pod.get("initContainers", []), *pod.get("containers", [])]:
        text = " ".join(str(part) for part in [*c.get("command", []), *c.get("args", [])])
        if "pg_isready" in text and " -U " not in text:
            found.append(f"{where}/{c['name']}: pg_isready without -U")
    return found


@needs_helm
@pytest.mark.parametrize("env", ENVS)
def test_every_postgres_wait_names_its_user(env: str) -> None:
    pods = _pod_specs(_render(env))
    waits = [c for _, pod in pods for c in pod.get("initContainers", [])
             if "pg_isready" in " ".join(c.get("command", []))]
    if env != "local":  # local runs its own database, the others wait for a separate server
        assert waits, "found no start-up wait at all, so this test checks nothing"
    problems = [p for where, pod in pods for p in anonymous_pg_isready(where, pod)]
    assert not problems, "\n".join(problems)


def test_the_user_check_itself_refuses_a_wait_that_names_nobody() -> None:
    """The negative case: yesterday's wait is reported; a wait that names its user is not."""
    bad = {"initContainers": [{"name": "wait", "command": [
        "sh", "-c", 'until pg_isready -h "$H"; do :; done']}]}
    good = {"initContainers": [{"name": "wait", "command": [
        "sh", "-c", 'until pg_isready -h "$H" -U "$POSTGRES_USER"; do :; done']}]}
    assert anonymous_pg_isready("Job/x", bad) == ["Job/x/wait: pg_isready without -U"]
    assert anonymous_pg_isready("Job/x", good) == []


def _gateway_container(env: str) -> dict[str, Any]:
    for where, pod in _pod_specs(_render(env)):
        if where.startswith("Deployment/") and where.endswith("-gateway"):
            return dict(pod["containers"][0])
    raise AssertionError("no gateway Deployment")


@needs_helm
@pytest.mark.parametrize("env", ENVS)
def test_gateway_is_alive_on_livez_and_ready_on_readyz(env: str) -> None:
    gw = _gateway_container(env)
    assert gw["livenessProbe"]["httpGet"]["path"] == "/livez"
    assert gw["readinessProbe"]["httpGet"]["path"] == "/readyz"


@needs_helm
def test_no_probe_is_pointed_at_the_full_report() -> None:
    """/healthz also needs the language model: pointed at by a probe it would restart or remove a
    gateway that is fine (found live, Lab 66)."""
    for env in ENVS:
        for _, pod in _pod_specs(_render(env)):
            for c in pod["containers"]:
                for kind in ("livenessProbe", "readinessProbe", "startupProbe"):
                    path = (c.get(kind) or {}).get("httpGet", {}).get("path")
                    assert path != "/healthz", f"{env}/{c['name']} {kind}"
