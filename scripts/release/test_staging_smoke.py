from __future__ import annotations

import json
from datetime import UTC, datetime

import staging_smoke as s

TAG = "sha-2459417"
D = {name: "sha256:" + f"{i}" * 64 for i, name in enumerate(s.SERVICES, start=1)}
REG = "000000000000.dkr.ecr.ap-south-1.amazonaws.com"


def pod_line(
    name: str,
    svc: str,
    *,
    phase: str = "Running",
    ready: str = "true",
    restarts: int = 0,
    tag: str = TAG,
    digest: str | None = None,
) -> str:
    digest = digest or D[svc]
    return (
        f"{name}\t{phase}\t{REG}/kaval/{svc}:{tag}\t"
        f"{REG}/kaval/{svc}@{digest}\t{ready}\t{restarts}\t"
    )


def output(pods: list[str], *, health: dict | None = None, code: str = "503") -> str:
    health = health or {
        "status": "degraded",
        "postgres": {"ok": True, "detail": "migrated to a1c4f9b0e3d2"},
        "ollama": {"ok": False, "detail": "ConnectError"},
    }
    return "\n".join(
        [
            "##pods",
            *pods,
            "##healthz",
            json.dumps(health),
            code,
            "##http /v1/incidents?limit=1",
            "200",
            "##http /v1/signals?limit=1",
            "200",
            "##end",
        ]
    )


def good_pods() -> list[str]:
    return [pod_line(f"kaval-staging-{svc}-abc", svc) for svc in s.SERVICES]


def run(text: str, head: str | None = "a1c4f9b0e3d2") -> list[s.Check]:
    return s.evaluate(s.parse_observation(text), TAG, D, head)


def failed(checks: list[s.Check]) -> list[str]:
    return [c.name for c in checks if not c.ok]


def test_a_healthy_staging_passes_even_though_ollama_is_down() -> None:
    assert failed(run(output(good_pods()))) == []


def test_a_finished_migration_job_pod_is_fine() -> None:
    job = pod_line("kaval-staging-migrate-x", "gateway", phase="Succeeded", ready="false")
    assert failed(run(output([*good_pods(), job]))) == []


def test_wrong_tag_fails_both_tag_and_digest() -> None:
    pods = good_pods()
    pods[0] = pod_line("kaval-staging-gateway-abc", "gateway", tag="sha-ffb436b")
    bad = failed(run(output(pods)))
    assert f"every service runs {TAG}" in bad
    assert "running digest equals the ECR digest" not in bad  # the digest is still ECR's


def test_same_tag_but_different_bytes_fails_the_digest_check() -> None:
    pods = good_pods()
    pods[1] = pod_line("kaval-staging-agent-abc", "agent", digest="sha256:" + "f" * 64)
    assert failed(run(output(pods))) == ["running digest equals the ECR digest"]


def test_a_missing_service_fails() -> None:
    bad = failed(run(output(good_pods()[:3])))
    assert "pods running and ready" in bad
    assert "running digest equals the ECR digest" in bad


def test_not_ready_fails() -> None:
    pods = good_pods()
    pods[2] = pod_line("kaval-staging-executor-abc", "executor", ready="false")
    assert failed(run(output(pods))) == ["pods running and ready"]


def test_one_first_boot_restart_passes_but_is_written_down() -> None:
    pods = good_pods()
    pods[1] = pod_line("kaval-staging-agent-abc", "agent", restarts=1)
    checks = run(output(pods))
    assert failed(checks) == []
    detail = next(c.detail for c in checks if c.name == "no crash loop")
    assert "agent-abc restarted 1x" in detail


def test_a_crash_loop_fails() -> None:
    pods = good_pods()
    pods[1] = pod_line("kaval-staging-agent-abc", "agent", restarts=s.MAX_RESTARTS + 1)
    assert failed(run(output(pods))) == ["no crash loop"]


def test_database_unreachable_fails() -> None:
    health = {
        "status": "degraded",
        "postgres": {"ok": False, "detail": "OperationalError"},
        "ollama": {"ok": False, "detail": "ConnectError"},
    }
    assert failed(run(output(good_pods(), health=health))) == [
        "gateway reaches the database, migrated"
    ]


def test_database_behind_the_commits_migrations_fails() -> None:
    assert failed(run(output(good_pods()), head="ffffffffffff")) == [
        "gateway reaches the database, migrated"
    ]


def test_api_not_answering_fails() -> None:
    text = output(good_pods()).replace(
        "##http /v1/signals?limit=1\n200", "##http /v1/signals?limit=1\n500"
    )
    assert failed(run(text)) == ["GET /v1/signals?limit=1"]


def test_garbage_output_fails_instead_of_crashing() -> None:
    assert failed(run("not what we expected")) != []
    assert failed(run("")) != []


def test_alembic_head_is_the_revision_nobody_descends_from() -> None:
    assert s.alembic_head({"a": None, "b": "a", "c": "b"}) == "c"
    assert s.alembic_head({"a": None, "b": "a", "c": "a"}) is None  # two heads: refuse to guess


def test_a_pass_is_recorded_and_a_rerun_replaces_it() -> None:
    now = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    other = {"backup": "sha256:" + "9" * 64}
    entry = s.build_entry(TAG, "2459417" + "0" * 33, D, other, run(output(good_pods())), now)
    text = s.add_pass(None, entry)
    text = s.add_pass(text, entry)
    data = json.loads(text)
    assert [p["tag"] for p in data["passes"]] == [TAG]
    assert data["passes"][0]["passed_at"] == "2026-10-09T12:00:00Z"
    assert data["passes"][0]["not_covered"], "a record must say what it does not prove"
    assert text.endswith("\n")


def test_has_passed_needs_every_digest_to_match() -> None:
    now = datetime(2026, 10, 9, tzinfo=UTC)
    text = s.add_pass(None, s.build_entry(TAG, None, D, {}, [], now))
    assert s.has_passed(text, D)
    assert not s.has_passed(text, {**D, "agent": "sha256:" + "f" * 64})
    assert not s.has_passed(text, {})


def test_the_remote_script_has_no_stray_newline_inside_a_jsonpath() -> None:
    # A literal newline inside a quoted jsonpath splits the shell line and the lookup silently
    # returns nothing (found live: the gateway address came back empty).
    for line in (s.REMOTE % {"ns": "kaval-staging"}).splitlines():
        assert line.count("'") % 2 == 0, line
