from __future__ import annotations

import json
from pathlib import Path

import prod_smoke as p
import pytest
import staging_smoke as s

TAG = "sha-3ab86d1"
D = {name: "sha256:" + f"{i}" * 64 for i, name in enumerate(s.SERVICES, start=1)}
REG = "000000000000.dkr.ecr.ap-south-1.amazonaws.com"
HEAD = "a1c4f9b0e3d2"
CALENDAR = "{ OnCalendar=*-*-* 19:30:00 UTC ; next_elapse=Tue 2026-10-27 19:30:00 UTC }"

GOOD_TIMERS = {
    "kaval-db-backup": ("active", CALENDAR),
    "kaval-cost-pull": ("active", "{ OnUnitActiveSec=3h }"),
    "kaval-heartbeat": ("active", "{ OnUnitActiveSec=2min }"),
}


def pod_line(name: str, svc: str, *, tag: str = TAG, digest: str | None = None) -> str:
    digest = digest or D[svc]
    return f"{name}\tRunning\t{REG}/kaval/{svc}:{tag}\t{REG}/kaval/{svc}@{digest}\ttrue\t0\t"


def good_pods() -> list[str]:
    pods = [pod_line(f"kaval-prod-{svc}-abc", svc) for svc in s.SERVICES]
    pods.append(pod_line("kaval-prod-gateway-def", "gateway"))  # prod runs two gateways
    return pods


def output(
    pods: list[str] | None = None,
    timers: dict[str, tuple[str, str]] | None = None,
    memory: str = "1104",
    ollama_ok: bool = True,
) -> str:
    health = {
        "status": "ok" if ollama_ok else "degraded",
        "postgres": {"ok": True, "detail": f"migrated to {HEAD}"},
        "ollama": {"ok": ollama_ok, "detail": "gemma3:1b ready" if ollama_ok else "ConnectError"},
    }
    lines = ["##pods", *(good_pods() if pods is None else pods)]
    lines += ["##healthz", json.dumps(health), "200" if ollama_ok else "503"]
    lines += ["##http /v1/incidents?limit=1", "200", "##http /v1/signals?limit=1", "200"]
    for name, (state, calendar) in (GOOD_TIMERS if timers is None else timers).items():
        lines += [f"##timer {name}", state, calendar]
    lines += ["##memory", memory, "##end"]
    return "\n".join(lines)


def record(tag: str = TAG, images: dict[str, str] | None = None) -> str:
    entry = {"tag": tag, "passed_at": "2026-10-21T11:08:34Z", "images": images or D}
    return json.dumps({"schema": 1, "passes": [entry]})


def run(text: str, record_text: str | None = None, head: str | None = HEAD) -> list[s.Check]:
    return p.evaluate_prod(
        s.parse_observation(text),
        p.parse_extras(text),
        TAG,
        D,
        head,
        record() if record_text is None else record_text,
    )


def failed(checks: list[s.Check]) -> list[str]:
    return [c.name for c in checks if not c.ok]


# ── the judgement ───────────────────────────────────────────────────────────────────────


def test_a_healthy_prod_that_passed_staging_passes() -> None:
    assert failed(run(output())) == []


def test_a_prod_running_a_tag_that_never_passed_staging_fails_only_the_record_check() -> None:
    checks = run(output(), record_text=record(tag="sha-ffb436b"))
    assert failed(checks) == ["these exact images passed staging"]
    assert "not on" in next(c.detail for c in checks if not c.ok)


def test_the_tag_re_pointed_since_it_passed_staging_fails_the_record_check() -> None:
    other = {**D, "agent": "sha256:" + "f" * 64}
    checks = run(output(), record_text=record(images=other))
    assert failed(checks) == ["these exact images passed staging"]
    assert "agent" in next(c.detail for c in checks if not c.ok)


def test_an_empty_or_missing_record_fails_instead_of_passing_by_default() -> None:
    assert "these exact images passed staging" in failed(run(output(), record_text="{}"))
    assert "these exact images passed staging" in failed(
        run(output(), record_text='{"passes": []}')
    )


def test_prod_running_someone_elses_build_fails_the_tag_and_digest_checks() -> None:
    pods = good_pods()
    pods[0] = pod_line(
        "kaval-prod-gateway-abc", "gateway", tag="sha-ffb436b", digest="sha256:" + "f" * 64
    )
    bad = failed(run(output(pods)))
    assert f"every service runs {TAG}" in bad
    assert "running digest equals the ECR digest" in bad


def test_a_missing_service_fails() -> None:
    assert "pods running and ready" in failed(run(output(good_pods()[1:4])))


def test_a_model_that_is_not_ready_is_a_note_not_a_failure() -> None:
    text = output(ollama_ok=False)
    assert failed(run(text)) == []
    out = p.notes(s.parse_observation(text), p.parse_extras(text))
    assert any("not ready" in line and "not gated on" in line for line in out)


def test_memory_is_reported_and_never_gates() -> None:
    text = output(memory="312")
    assert failed(run(text)) == []
    assert any(
        "312 MB" in line for line in p.notes(s.parse_observation(text), p.parse_extras(text))
    )


# ── the timers ──────────────────────────────────────────────────────────────────────────


def test_a_timer_that_is_not_active_fails() -> None:
    timers = {**GOOD_TIMERS, "kaval-cost-pull": ("inactive", "")}
    checks = run(output(timers=timers))
    assert failed(checks) == ["the server's timers are set"]
    assert "kaval-cost-pull is inactive" in next(c.detail for c in checks if not c.ok)


def test_a_missing_timer_fails() -> None:
    timers = {k: v for k, v in GOOD_TIMERS.items() if k != "kaval-db-backup"}
    checks = run(output(timers=timers))
    assert failed(checks) == ["the server's timers are set"]
    assert "kaval-db-backup is missing" in next(c.detail for c in checks if not c.ok)


def test_the_dump_timer_set_for_another_time_fails() -> None:
    wrong = "{ OnCalendar=*-*-* 03:00:00 UTC ; next_elapse=Tue 2026-10-27 03:00:00 UTC }"
    checks = run(output(timers={**GOOD_TIMERS, "kaval-db-backup": ("active", wrong)}))
    assert failed(checks) == ["the server's timers are set"]
    assert "19:30:00" in next(c.detail for c in checks if not c.ok)


def test_garbage_output_fails_instead_of_crashing() -> None:
    assert failed(run("not what we expected")) != []
    assert failed(run("")) != []
    assert p.parse_extras("##timer x\n##memory\nabc") == {
        "timers": {"x": ("", "")},
        "memory_mb": None,
    }


# ── what runs on the server ─────────────────────────────────────────────────────────────


def test_the_remote_script_is_filled_in_for_prod_and_ends_once() -> None:
    script = p.REMOTE % {"ns": "kaval-prod"}
    assert "NS=kaval-prod" in script
    assert script.count("##end") == 1
    assert script.rstrip().endswith("echo '##end'")
    assert "##timer $t" in script and "##memory" in script


def test_the_remote_script_has_no_stray_newline_inside_a_quoted_string() -> None:
    for line in (p.REMOTE % {"ns": "kaval-prod"}).splitlines():
        assert line.count("'") % 2 == 0, line


def test_the_remote_script_only_reads() -> None:
    """The prod smoke test must never be able to change production. Anything that writes, starts,
    stops, scales or deletes is a different tool and needs its own go."""
    script = p.REMOTE % {"ns": "kaval-prod"}
    for verb in (
        "apply",
        "delete",
        "patch",
        "scale",
        "rollout",
        "exec ",
        "edit",
        "create",
        "systemctl start",
        "systemctl stop",
        "systemctl restart",
        "rm ",
        "kill",
    ):
        assert verb not in script, verb


# ── the command ─────────────────────────────────────────────────────────────────────────


def _fake_aws(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    monkeypatch.setattr(p, "current_prod_tag", lambda root=None: TAG)
    monkeypatch.setattr(s, "ecr_digest", lambda svc, tag: D[svc])
    monkeypatch.setattr(s, "git", lambda *a: "")
    monkeypatch.setattr(s, "node_id", lambda prefix=s.PREFIX, bring_up="": "i-0123456789abcdef0")
    monkeypatch.setattr(s, "on_node", lambda instance, script=None: text)


def _root(tmp_path: Path, record_text: str) -> Path:
    path = tmp_path / p.RECORD
    path.parent.mkdir(parents=True)
    path.write_text(record_text, encoding="utf-8")
    return tmp_path


def test_main_passes_and_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_aws(monkeypatch, output())
    root = _root(tmp_path, record())
    before = (root / p.RECORD).read_text(encoding="utf-8")
    assert p.main(["--root", str(root)]) == 0
    assert (root / p.RECORD).read_text(encoding="utf-8") == before
    assert sorted(x.name for x in root.rglob("*") if x.is_file()) == ["passed-staging.json"]
    assert "passed. Nothing was recorded or changed." in capsys.readouterr().out


def test_main_refuses_a_build_that_did_not_pass_staging(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_aws(monkeypatch, output())
    root = _root(tmp_path, record(tag="sha-ffb436b"))
    assert p.main(["--root", str(root)]) == 1
    out = capsys.readouterr().out
    assert "FAIL  these exact images passed staging" in out
    assert "did NOT pass" in out


def test_main_says_it_cannot_run_when_prod_is_parked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_aws(monkeypatch, output())

    def parked(prefix: str = s.PREFIX, bring_up: str = "") -> str:
        raise RuntimeError(f"{prefix} has no running node. Bring it up first: {bring_up}")

    monkeypatch.setattr(s, "node_id", parked)
    assert p.main(["--root", str(_root(tmp_path, record()))]) == 2
    err = capsys.readouterr().err
    assert "kaval-prod has no running node" in err
    assert "prod-rollout.md" in err


def test_main_refuses_something_that_is_not_a_release_tag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_aws(monkeypatch, output())
    assert p.main(["--root", str(_root(tmp_path, record())), "--tag", "latest"]) == 2


def test_it_looks_at_prod_and_never_at_staging() -> None:
    assert p.NAMESPACE == "kaval-prod" and p.PREFIX == "kaval-prod"
    assert "staging" not in p.REMOTE
