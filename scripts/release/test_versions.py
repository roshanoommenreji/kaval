from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import versions
from versions import Commit

ROOT = Path(__file__).resolve().parents[2]


def commit(subject: str, *files: str, body: str = "") -> Commit:
    return Commit("c" * 40, subject, body, files)


CURRENT = dict.fromkeys(versions.COMPONENTS, "0.1.0")


# ── what a commit means ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("subject", "body", "level"),
    [
        ("fix(gateway): stop crashing", "", "patch"),
        ("feat(agent): propose things (KAV-1)", "", "minor"),
        ("feat!: change the API", "", "major"),
        ("fix(api)!: rename a field", "", "major"),
        ("fix: small", "BREAKING CHANGE: the field is gone", "major"),
        ("docs: words", "", None),
        ("chore(deps): bump", "", None),
        ("infra: a thing", "", None),
        ("not conventional at all", "", None),
        ("Merge pull request #5 from x/y", "", None),
    ],
)
def test_level_of(subject: str, body: str, level: str | None) -> None:
    assert versions.level_of(subject, body) == level


def test_bump_follows_semver() -> None:
    assert versions.bump("1.2.3", "patch") == "1.2.4"
    assert versions.bump("1.2.3", "minor") == "1.3.0"
    assert versions.bump("1.2.3", "major") == "2.0.0"


def test_a_breaking_change_to_a_0x_component_moves_the_minor_so_1_0_0_stays_a_decision() -> None:
    assert versions.bump("0.4.2", "major") == "0.5.0"


def test_bump_rejects_nonsense() -> None:
    with pytest.raises(ValueError):
        versions.bump("1.2", "patch")
    with pytest.raises(ValueError):
        versions.bump("1.2.3", "huge")


# ── which components a file belongs to ────────────────────────────────────────────────


def test_a_services_own_folder_is_its_own_change() -> None:
    assert versions.components_of("services/collector/kaval_collector/x.py") == {"collector"}


def test_shared_code_moves_every_deployed_service_and_not_backup() -> None:
    assert versions.components_of("services/shared/kaval_shared/models.py") == set(versions.APP)


def test_migrations_belong_to_the_gateway_and_policy_to_agent_and_executor() -> None:
    assert versions.components_of("migrations/versions/x.py") == {"gateway"}
    assert versions.components_of("policy/actions.rego") == {"agent", "executor"}


def test_a_file_nothing_ships_moves_nothing() -> None:
    assert versions.components_of("docs/adr/0001.md") == set()
    assert versions.components_of(".github/workflows/ci.yml") == set()


def test_ships_agrees_with_what_the_dockerfiles_copy() -> None:
    """The mapping is a fact about the images, so it is checked against them, not trusted."""
    mapped = {p: set(c) for p, c in versions.SHIPS.items() if not p.startswith("services/")}
    mapped["services/shared/"] = set(versions.APP)
    for svc in versions.COMPONENTS:
        text = (ROOT / f"services/{svc}/Dockerfile").read_text(encoding="utf-8")
        sources = []
        for line in re.findall(r"(?m)^COPY .+$", text):
            parts = line.split()[1:]
            if any(part.startswith("--from") for part in parts):
                continue  # copied from another build stage, not from the repository
            sources += [part.rstrip("/") for part in parts[:-1]]  # the last word is the destination
        for prefix, comps in mapped.items():
            path = prefix.rstrip("/")
            copied = any(
                s == path or path.startswith(s + "/") or s.startswith(path + "/") for s in sources
            )
            assert copied == (svc in comps), f"{svc}'s Dockerfile and SHIPS disagree about {prefix}"


# ── the plan ──────────────────────────────────────────────────────────────────────────


def plan_for(**by_component: list[Commit]) -> versions.Plan:
    return versions.make_plan(CURRENT, "0.1.0", by_component)


def test_nothing_changed_nothing_moves() -> None:
    p = plan_for(gateway=[commit("docs: x", "services/gateway/a.py")])
    assert p.next == {} and p.product_next is None


def test_a_gateway_fix_is_a_gateway_patch_and_a_product_patch() -> None:
    p = plan_for(gateway=[commit("fix: x", "services/gateway/kaval_gateway/a.py")])
    assert p.next == {"gateway": "0.1.1"} and p.product_next == "0.1.1"


def test_the_product_follows_the_largest_move() -> None:
    p = plan_for(
        gateway=[commit("fix: x", "services/gateway/a.py")],
        agent=[commit("feat: y", "services/agent/a.py")],
    )
    assert p.next == {"gateway": "0.1.1", "agent": "0.2.0"}
    assert p.product_next == "0.2.0"


def test_one_commit_touching_two_components_moves_both() -> None:
    c = commit("feat: z", "services/gateway/a.py", "services/agent/b.py")
    p = plan_for(gateway=[c], agent=[c])
    assert set(p.next) == {"gateway", "agent"}


def test_shared_code_change_moves_every_service_that_ships_it() -> None:
    c = commit("fix: x", "services/shared/kaval_shared/m.py")
    p = plan_for(**{svc: [c] for svc in versions.COMPONENTS})
    assert set(p.next) == set(versions.APP)


def test_a_component_only_counts_commits_that_touched_its_own_files() -> None:
    p = plan_for(collector=[commit("feat: x", "services/gateway/a.py")])
    assert p.next == {}


def test_a_breaking_change_at_0x_moves_the_product_minor_not_major() -> None:
    p = plan_for(gateway=[commit("feat!: x", "services/gateway/a.py")])
    assert p.next == {"gateway": "0.2.0"} and p.product_next == "0.2.0"


def test_markdown_names_what_moves_and_why() -> None:
    p = plan_for(gateway=[commit("fix: stop crashing", "services/gateway/a.py")])
    text = versions.markdown(p)
    assert "0.1.0 -> **0.1.1**" in text and "stop crashing" in text and "(unchanged)" in text


def test_markdown_says_so_when_nothing_moves() -> None:
    assert "No component" in versions.markdown(plan_for())


def test_the_release_is_named_by_the_product_with_its_components_listed() -> None:
    v = {**CURRENT, "gateway": "1.2.0"}
    assert versions.bill_of_materials(v, "0.3.0") == (
        "Kaval 0.3.0: gateway 1.2.0 · collector 0.1.0 · agent 0.1.0 · executor 0.1.0 · backup 0.1.0"
    )


def test_tag_names() -> None:
    assert versions.tag_names(CURRENT, "0.3.0") == {
        "gateway-v0.1.0": "gateway",
        "collector-v0.1.0": "collector",
        "agent-v0.1.0": "agent",
        "executor-v0.1.0": "executor",
        "backup-v0.1.0": "backup",
        "v0.3.0": "product",
    }


def test_uv_lock_records_the_projects_own_version_and_nothing_else() -> None:
    lock = (
        '[[package]]\nname = "other"\nversion = "0.1.0"\n\n'
        '[[package]]\nname = "kaval"\nversion = "0.1.0"\nsource = { editable = "." }\n'
    )
    got = versions.rewrite_uv_lock(lock, "0.2.0")
    assert 'name = "kaval"\nversion = "0.2.0"' in got
    assert 'name = "other"\nversion = "0.1.0"' in got
    with pytest.raises(ValueError):
        versions.rewrite_uv_lock('[[package]]\nname = "x"\n', "0.2.0")


def test_the_real_uv_lock_has_the_line_the_rewriter_needs() -> None:
    text = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert versions.rewrite_uv_lock(text, "9.9.9") != text


def test_rewriting_files_changes_only_the_version_line() -> None:
    assert versions.rewrite_init('"""d"""\n__version__ = "0.1.0"\nx = 1\n', "0.2.0") == (
        '"""d"""\n__version__ = "0.2.0"\nx = 1\n'
    )
    toml = '[project]\nname = "kaval"\nversion = "0.1.0"\n[tool]\nversion = "9.9.9"\n'
    assert 'version = "0.2.0"' in versions.rewrite_pyproject(toml, "0.2.0")
    assert 'version = "9.9.9"' in versions.rewrite_pyproject(toml, "0.2.0")
    with pytest.raises(ValueError):
        versions.rewrite_init("nothing here\n", "0.2.0")


# ── against a real (scratch) Git repository ───────────────────────────────────────────


def run(root: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
    return out.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    run(tmp_path, "init", "-q", "-b", "main")
    run(tmp_path, "config", "user.email", "t@example.com")
    run(tmp_path, "config", "user.name", "t")
    run(tmp_path, "config", "commit.gpgsign", "false")
    run(tmp_path, "config", "tag.gpgsign", "false")
    for c in versions.COMPONENTS:
        p = tmp_path / versions.init_path(c)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('__version__ = "0.1.0"\n', encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n', encoding="utf-8")
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "kaval"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    run(tmp_path, "add", "-A")
    run(tmp_path, "commit", "-q", "-m", "feat: first")
    return tmp_path


def change(root: Path, svc: str, subject: str) -> None:
    f = root / f"services/{svc}/kaval_{svc}/work.py"
    f.write_text(f.read_text() + "x\n" if f.exists() else "x\n", encoding="utf-8")
    run(root, "add", "-A")
    run(root, "commit", "-q", "-m", subject)


def test_no_tag_means_cannot_decide(repo: Path) -> None:
    with pytest.raises(RuntimeError, match="was never tagged"):
        versions.load_plan(root=repo)


def test_tag_then_plan_then_bump_then_tag_again(repo: Path) -> None:
    assert versions.main(["--root", str(repo), "tag", "--commit", "HEAD"]) == 0
    assert run(repo, "tag", "-l").split() == sorted(versions.tag_names(CURRENT, "0.1.0"))
    assert "Kaval 0.1.0: gateway 0.1.0" in run(repo, "tag", "-l", "v0.1.0", "-n1")

    assert versions.load_plan(root=repo).product_next is None  # nothing since the baseline

    change(repo, "gateway", "fix(gateway): a bug")
    change(repo, "agent", "docs(agent): words")
    plan = versions.load_plan(root=repo)
    assert plan.next == {"gateway": "0.1.1"} and plan.product_next == "0.1.1"

    assert versions.main(["--root", str(repo), "bump"]) == 0
    assert versions.versions_at("", repo)[0]["gateway"] == "0.1.1"
    assert versions.versions_at("", repo)[1] == "0.1.1"
    assert 'version = "0.1.1"' in (repo / "uv.lock").read_text(encoding="utf-8")
    run(repo, "commit", "-q", "-am", "chore(release): Kaval 0.1.1")

    # the bump commit is itself a `chore`, so it moves nothing; but the gateway is now 0.1.1 and
    # untagged, so counting starts from a tag that does not exist yet: the release must tag first
    with pytest.raises(RuntimeError, match="gateway-v0.1.1"):
        versions.load_plan(root=repo)
    assert versions.main(["--root", str(repo), "tag", "--commit", "HEAD"]) == 0
    assert "gateway-v0.1.1" in run(repo, "tag", "-l")
    assert "agent-v0.1.1" not in run(repo, "tag", "-l")  # the agent did not change
    assert versions.load_plan(root=repo).product_next is None

    assert versions.main(["--root", str(repo), "tag", "--commit", "HEAD"]) == 0  # idempotent
    assert run(repo, "tag", "-l").split().count("gateway-v0.1.1") == 1


def test_a_merge_commit_adds_no_change_of_its_own(repo: Path) -> None:
    versions.main(["--root", str(repo), "tag"])
    run(repo, "switch", "-q", "-c", "topic")
    change(repo, "collector", "feat(collector): thing")
    run(repo, "switch", "-q", "main")
    run(repo, "merge", "-q", "--no-ff", "topic", "-m", "Merge pull request #1 from x/topic")
    got = versions.commits_between("collector-v0.1.0", "HEAD", repo)
    assert [c.subject for c in got] == ["feat(collector): thing"]
    assert versions.load_plan(root=repo).next == {"collector": "0.2.0"}


def test_an_older_release_may_lack_a_component_that_did_not_exist_yet(repo: Path) -> None:
    first = run(repo, "rev-parse", "HEAD")
    (repo / versions.init_path("backup")).unlink()
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", "chore: drop backup for the test")
    older = run(repo, "rev-parse", "HEAD")
    with pytest.raises(RuntimeError, match="does not exist"):
        versions.versions_at(older, repo)
    comps, _ = versions.versions_at(older, repo, partial=True)
    assert "backup" not in comps and comps["gateway"] == "0.1.0"
    assert "backup" in versions.versions_at(first, repo)[0]
