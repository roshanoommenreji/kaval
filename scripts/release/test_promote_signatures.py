"""The promotion gate refuses an image whose signature does not verify (KAV-139, ADR-0092).

The checking tools (`cosign`, `gh`) are replaced by one small stand-in program that is run for real
as a subprocess, so the whole path is exercised: the command line the gate builds, the exit code it
gets back, and what it does with it. The stand-in only says yes to the exact signer it was told
about, for the exact digests it was told are signed, so a gate that asked the wrong question (a
pattern, another workflow, another branch) would be refused here, not waved through.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import promote
import pytest
from staging_smoke import SERVICES

ACCOUNT = "".join(["1234", "5678", "9012"])  # a made-up number, built from pieces
REGISTRY = f"{ACCOUNT}.dkr.ecr.ap-south-1.amazonaws.com"
ALL_KINDS = tuple(kind for _label, kind in promote.RECORD_KINDS)
DIGESTS = {s: "sha256:" + str(i + 1) * 64 for i, s in enumerate(SERVICES)}

STAND_IN = """
import json, os, sys

args = sys.argv[1:]
with open(os.environ["STANDIN_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(args) + "\\n")


def value(flag):
    return args[args.index(flag) + 1] if flag in args else None


signed = os.environ["SIGNED_DIGESTS"].split(",")
ref = args[1] if args[0] == "verify" else args[2].removeprefix("oci://")
digest = ref.split("@")[1]
ok = digest in signed
if args[0] == "verify":  # cosign verify <ref> --certificate-identity .. --certificate-oidc-issuer
    ok = (
        ok
        and value("--certificate-identity") == os.environ["KNOWN_IDENTITY"]
        and value("--certificate-oidc-issuer") == os.environ["KNOWN_ISSUER"]
    )
else:  # gh attestation verify oci://<ref> --repo --signer-workflow --source-ref ..
    ok = (
        ok
        and value("--repo") == os.environ["KNOWN_REPO"]
        and value("--signer-workflow") == os.environ["KNOWN_WORKFLOW"]
        and value("--source-ref") == os.environ["KNOWN_REF"]
        and value("--predicate-type") in os.environ["KNOWN_KINDS"].split(",")
    )
if not ok:
    print("Error: no matching signatures for " + ref, file=sys.stderr)
    sys.exit(1)
print("verified " + ref)
"""


class Tools:
    """The stand-in, configured; `calls()` reads back every command line it was given."""

    def __init__(self, tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.script = tmp / "standin.py"
        self.script.write_text(STAND_IN, encoding="utf-8")
        self.log = tmp / "calls.jsonl"
        cmd = f"{Path(sys.executable).as_posix()} {self.script.as_posix()}"
        self.mp = monkeypatch
        self.mp.setenv("COSIGN", cmd)
        self.mp.setenv("GH", cmd)
        self.mp.setenv("STANDIN_LOG", str(self.log))
        self.configure()

    def configure(
        self,
        signed: dict[str, str] | None = None,
        identity: str = promote.SIGNER_IDENTITY,
        workflow: str = promote.SIGNER_WORKFLOW,
        ref: str = promote.SIGNER_REF,
        kinds: tuple[str, ...] = ALL_KINDS,
    ) -> None:
        signed = DIGESTS if signed is None else signed
        # "none", not "": an empty value would unset the variable on Windows.
        self.mp.setenv("SIGNED_DIGESTS", ",".join(signed.values()) or "none")
        self.mp.setenv("KNOWN_IDENTITY", identity)
        self.mp.setenv("KNOWN_ISSUER", promote.SIGNER_ISSUER)
        self.mp.setenv("KNOWN_REPO", promote.REPO)
        self.mp.setenv("KNOWN_WORKFLOW", workflow)
        self.mp.setenv("KNOWN_REF", ref)
        self.mp.setenv("KNOWN_KINDS", ",".join(kinds))

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(ln) for ln in self.log.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Tools:
    return Tools(tmp_path, monkeypatch)


# ── the gate says yes when everything is as release.yml leaves it ────────────────────


def test_a_fully_signed_release_passes(tools: Tools) -> None:
    checks = promote.check_signatures(DIGESTS, REGISTRY)
    assert [c.ok for c in checks] == [True, True, True]
    assert len(tools.calls()) == 3 * len(SERVICES)  # every check, every image


def test_every_signature_check_names_the_exact_signer_and_never_a_pattern(tools: Tools) -> None:
    promote.check_signatures(DIGESTS, REGISTRY)
    cosign = [c for c in tools.calls() if c[0] == "verify"]
    assert len(cosign) == len(SERVICES)
    for call in cosign:
        assert call[call.index("--certificate-identity") + 1] == (
            "https://github.com/roshanoommenreji/kaval/.github/workflows/release.yml@refs/heads/main"
        )
        assert call[call.index("--certificate-oidc-issuer") + 1] == (
            "https://token.actions.githubusercontent.com"
        )
    for call in tools.calls():
        assert not any("regexp" in a for a in call), call
    # The digest is what is checked, not a tag.
    assert {c[1].split("@")[1] for c in cosign} == set(DIGESTS.values())


def test_the_build_record_and_parts_list_are_checked_for_workflow_branch_and_kind(
    tools: Tools,
) -> None:
    promote.check_signatures(DIGESTS, REGISTRY)
    records = [c for c in tools.calls() if c[0] == "attestation"]
    assert len(records) == 2 * len(SERVICES)
    kinds = {c[c.index("--predicate-type") + 1] for c in records}
    assert kinds == {"https://slsa.dev/provenance/v1", "https://cyclonedx.org/bom"}
    for call in records:
        assert call[call.index("--repo") + 1] == "roshanoommenreji/kaval"
        assert call[call.index("--signer-workflow") + 1] == (
            "roshanoommenreji/kaval/.github/workflows/release.yml"
        )
        assert call[call.index("--source-ref") + 1] == "refs/heads/main"


# ── the negative cases: the gate says no ─────────────────────────────────────────────


def test_an_unsigned_image_is_refused_and_named(tools: Tools) -> None:
    tools.configure(signed={s: d for s, d in DIGESTS.items() if s != "executor"})
    checks = promote.check_signatures(DIGESTS, REGISTRY)
    assert not any(c.ok for c in checks)
    for c in checks:
        assert "executor" in c.detail
        assert "gateway" not in c.detail and "agent" not in c.detail  # only the culprit


def test_an_image_signed_by_another_workflow_is_refused(tools: Tools) -> None:
    other = "https://github.com/roshanoommenreji/kaval/.github/workflows/ci.yml@refs/heads/main"
    tools.configure(identity=other, workflow="roshanoommenreji/kaval/.github/workflows/ci.yml")
    checks = promote.check_signatures(DIGESTS, REGISTRY)
    assert not any(c.ok for c in checks)  # all three questions: signature, record, parts list


def test_an_image_signed_on_another_branch_is_refused(tools: Tools) -> None:
    tools.configure(
        identity=promote.SIGNER_IDENTITY.replace("heads/main", "heads/other"),
        ref="refs/heads/other",
    )
    assert not any(c.ok for c in promote.check_signatures(DIGESTS, REGISTRY))


def test_one_missing_record_refuses_only_that_check(tools: Tools) -> None:
    tools.configure(kinds=("https://slsa.dev/provenance/v1",))  # no parts list stored
    sig, record, parts = promote.check_signatures(DIGESTS, REGISTRY)
    assert sig.ok and record.ok and not parts.ok
    assert "parts list" in parts.name


def test_a_refusal_never_shows_the_registry_or_the_account_number(tools: Tools) -> None:
    tools.configure(signed={})  # nothing signed: the stand-in's error text names the registry
    checks = promote.check_signatures(DIGESTS, REGISTRY)
    shown = " ".join(f"{c.name} {c.detail}" for c in checks)
    assert "no matching signatures" in shown  # the reason is kept...
    assert ACCOUNT not in shown and REGISTRY not in shown  # ...with the address hidden
    assert "***" in shown


# ── the gate cannot decide: it refuses, it does not guess ────────────────────────────


def test_a_missing_tool_is_cannot_decide_not_a_pass(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("COSIGN", "no-such-signing-tool-anywhere")
    monkeypatch.setenv("REGISTRY", REGISTRY)
    monkeypatch.setattr(promote, "ecr_digests", lambda tag: DIGESTS)
    assert promote.main(["signatures", "--tag", "sha-1111111"]) == 2
    assert "not installed" in capsys.readouterr().err


def test_no_registry_is_cannot_decide(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("REGISTRY", raising=False)
    monkeypatch.setattr(promote, "ecr_digests", lambda tag: DIGESTS)
    assert promote.main(["signatures", "--tag", "sha-1111111"]) == 2
    assert "REGISTRY is not set" in capsys.readouterr().err


@pytest.mark.parametrize(
    "bad", ["--registry", "evil.example.com", "123.dkr.ecr.x.amazonaws.com", ""]
)
def test_a_registry_that_is_not_an_ecr_address_is_never_passed_to_a_tool(
    bad: str, tools: Tools
) -> None:
    with pytest.raises(RuntimeError):
        promote.check_signatures(DIGESTS, bad)
    assert tools.calls() == []


# ── the command, end to end ──────────────────────────────────────────────────────────


def test_the_signatures_command_passes_a_signed_tag(
    tools: Tools, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("REGISTRY", REGISTRY)
    monkeypatch.setattr(promote, "ecr_digests", lambda tag: DIGESTS)
    assert promote.main(["signatures", "--tag", "sha-1111111"]) == 0
    out = capsys.readouterr().out
    assert out.count("PASS") == 3 and "carries valid signatures" in out
    assert ACCOUNT not in out


def test_the_signatures_command_refuses_an_unsigned_tag(
    tools: Tools, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tools.configure(signed={})
    monkeypatch.setenv("REGISTRY", REGISTRY)
    monkeypatch.setattr(promote, "ecr_digests", lambda tag: DIGESTS)
    assert promote.main(["signatures", "--tag", "sha-1111111"]) == 1
    out = capsys.readouterr().out
    assert out.count("FAIL") == 3 and "does NOT carry valid signatures" in out
    assert ACCOUNT not in out


def test_an_image_missing_from_the_registry_is_cannot_decide(
    tools: Tools, monkeypatch: pytest.MonkeyPatch
) -> None:
    def gone(tag: str) -> dict[str, str]:
        raise RuntimeError("kaval/gateway:sha-1111111 is not readable in ECR")

    monkeypatch.setenv("REGISTRY", REGISTRY)
    monkeypatch.setattr(promote, "ecr_digests", gone)
    assert promote.main(["signatures", "--tag", "sha-1111111"]) == 2


def test_verify_asks_the_signature_question_about_the_digests_that_passed_staging() -> None:
    """`verify` reaches Git, Jira and AWS, so it is not run whole here; this pins that it calls the
    signature check with the record's digests, after the ECR check and as part of the same report
    that gates the promotion."""
    import inspect

    src = inspect.getsource(promote.cmd_verify)
    ecr = src.index("check_ecr(")
    sigs = src.index('check_signatures(entry["images"], registry_from_env())')
    assert ecr < sigs
    assert "checks.extend(" in src[sigs - 20 : sigs]
