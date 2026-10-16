"""The rules that let a new version start on an old database (KAV-85, ADR-0057).

Three things went wrong on 2026-10-14 when production came up on a database two migrations behind.
These tests keep each from coming back:

* the migration runs BEFORE the install when the database is a server of its own (the chart);
* every table a migration creates is given to a service role in `scripts/ops/db-roles.sql`
  (a table nobody was granted is a live "permission denied", not a build failure);
* a migration only adds. While it runs, the OLD version is still serving, so an upgrade that drops
  or renames something breaks it. A deliberate exception says so on a line of its own:
  `# contract: <why the old version is already gone>`.

The chart tests render the real chart with `helm template`, so they skip without helm."""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy" / "charts" / "kaval"
MIGRATIONS = ROOT / "migrations" / "versions"
DB_ROLES = ROOT / "scripts" / "ops" / "db-roles.sql"
CONTRACT_MARK = re.compile(r"(?m)^\s*# contract: \S")
BREAKING = {"drop_table", "drop_column", "rename_table"}


def _upgrade_calls(source: str) -> list[ast.Call]:
    """Every `op.<something>(...)` inside `def upgrade()`."""
    calls: list[ast.Call] = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade":
            calls += [
                n for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "op"
            ]
    return calls


def _name(call: ast.Call) -> str:
    assert isinstance(call.func, ast.Attribute)
    return call.func.attr


def breaking_changes(source: str) -> list[str]:
    """What `upgrade()` takes away from the version still running (empty if it only adds)."""
    found = []
    for call in _upgrade_calls(source):
        if _name(call) in BREAKING:
            found.append(_name(call))
        elif _name(call) == "alter_column" and any(
            k.arg == "new_column_name" for k in call.keywords
        ):
            found.append("alter_column(new_column_name=...)")
    return found


def created_tables(source: str) -> list[str]:
    return [
        str(call.args[0].value) for call in _upgrade_calls(source)
        if _name(call) == "create_table" and call.args and isinstance(call.args[0], ast.Constant)
    ]


def granted_tables(sql: str) -> set[str]:
    """Every word that appears in a GRANT statement of db-roles.sql (comments removed)."""
    code = re.sub(r"--[^\n]*", "", sql)
    statements = re.findall(r"(?is)\bGRANT\b.*?;", code)
    return {w for s in statements for w in re.findall(r"[a-z_][a-z0-9_]*", s.lower())}


# ── the checkers themselves (the negative cases) ──────────────────────────────────────────────


def test_a_migration_that_drops_a_column_is_flagged() -> None:
    src = "from alembic import op\ndef upgrade():\n    op.drop_column('action', 'slack_ts')\n"
    assert breaking_changes(src) == ["drop_column"]


def test_a_migration_that_renames_a_column_is_flagged() -> None:
    src = ("from alembic import op\ndef upgrade():\n"
           "    op.alter_column('action', 'a', new_column_name='b')\n")
    assert breaking_changes(src) == ["alter_column(new_column_name=...)"]


def test_dropping_in_downgrade_is_not_flagged() -> None:
    """`downgrade()` removes what `upgrade()` added: it is the way back, not the way forward."""
    src = ("from alembic import op\ndef upgrade():\n    op.add_column('action', None)\n"
           "def downgrade():\n    op.drop_column('action', 'slack_ts')\n")
    assert breaking_changes(src) == []


def test_a_table_only_named_in_a_comment_is_not_granted() -> None:
    sql = "GRANT SELECT ON signal TO kaval_agent; -- the table `ghost` is only in a comment\n"
    assert "signal" in granted_tables(sql)
    assert "ghost" not in granted_tables(sql)


def test_a_created_table_is_found() -> None:
    src = "from alembic import op\ndef upgrade():\n    op.create_table('widget')\n"
    assert created_tables(src) == ["widget"]


# ── the repository as it is ───────────────────────────────────────────────────────────────────


def _migrations() -> list[Path]:
    return sorted(MIGRATIONS.glob("*.py"))


@pytest.mark.parametrize("path", _migrations(), ids=lambda p: p.name)
def test_every_migration_only_adds(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    if CONTRACT_MARK.search(source):
        return
    assert breaking_changes(source) == [], (
        f"{path.name} takes something away while the old version is still running. If the old "
        "version is already gone, say so on a line of its own: `# contract: <why>` (ADR-0057)."
    )


def test_every_table_a_migration_creates_is_granted_to_a_service() -> None:
    granted = granted_tables(DB_ROLES.read_text(encoding="utf-8"))
    created = {t for p in _migrations() for t in created_tables(p.read_text(encoding="utf-8"))}
    missing = sorted(created - granted)
    assert not missing, f"no GRANT in scripts/ops/db-roles.sql for: {', '.join(missing)}"


# ── the chart: where the migration runs ───────────────────────────────────────────────────────

needs_helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def _hook(*values: str) -> str:
    done = subprocess.run(
        ["helm", "template", "kaval-x", str(CHART), *values],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert done.returncode == 0, done.stderr
    docs = re.split(r"(?m)^---\s*$", done.stdout)
    jobs = [d for d in docs if re.search(r"(?m)^kind: Job$", d) and "-migrate-" in d]
    assert len(jobs) == 1
    match = re.search(r'"helm.sh/hook": (\S+)', jobs[0])
    assert match
    return match.group(1)


@needs_helm
@pytest.mark.parametrize("env", ["staging", "prod"])
def test_with_a_database_server_of_its_own_the_migration_runs_first(env: str) -> None:
    values = ROOT / "deploy" / "environments" / env / "values.yaml"
    assert _hook("-f", str(values)) == "pre-install,pre-upgrade"


@needs_helm
def test_with_the_charts_own_postgres_the_migration_still_waits_for_it() -> None:
    """The local/k3d case: there is no database until the release creates one."""
    hook = _hook("--set", "postgres.enabled=true", "--set", "postgres.password=x")
    assert hook == "post-install,pre-upgrade"


# ── the server's start-up script: permissions after the migration ─────────────────────────────

TEMPLATE = ROOT / "infra" / "modules" / "node" / "user_data.sh.tftpl"


def _bootstrap_script() -> str:
    """The bootstrap script cut out of the template, with the template's own syntax resolved."""
    text = TEMPLATE.read_text(encoding="utf-8")
    start_marker = "cat > /usr/local/sbin/kaval-gitops-bootstrap <<'SCRIPT'\n"
    start = text.index(start_marker) + len(start_marker)
    body = text[start:text.index("\nSCRIPT\n", start) + 1]
    body = re.sub(r"(?m)^\s*%\{[^}]*\}\s*$\n", "", body)  # the if/endif lines: keep both branches
    body = body.replace("$${", "${").replace("${name_prefix}", "kaval-test")
    return body.replace("${gitops_env}", "test")


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
def test_the_bootstrap_script_is_valid_shell(tmp_path: Path) -> None:
    script = tmp_path / "bootstrap.sh"
    script.write_text(_bootstrap_script(), encoding="utf-8", newline="\n")
    done = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_permissions_are_handed_out_again_after_the_release_is_ready_on_every_boot() -> None:
    """Not only on a seeded staging: production after a parked week has an old database too."""
    script = _bootstrap_script()
    assert "SEEDED" not in script, "the second pass must not depend on the staging-only seed"
    wait = script.index("kubectl wait helmrelease/kaval-test")
    assert script.count("run_db_roles\n") >= 2  # the first pass, and the one after the wait
    assert script.index("run_db_roles\nfi\nunset ADMIN_PW") > wait


def test_the_second_pass_is_started_last_and_does_not_hold_up_the_boot() -> None:
    script = _bootstrap_script()
    spawn = script.index("systemd-run --no-block --unit=kaval-db-grants")
    assert spawn > script.index("rm -f /tmp/db-roles.sql"), "two runs must never share the file"
    assert "kubectl wait" not in script[spawn:]
