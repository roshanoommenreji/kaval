"""The cluster's image check says what ADR-0093 says it says (KAV-140).

The policy (deploy/image-policy/), the way Flux installs Kyverno (deploy/gitops/staging/checker-*),
the registry login the node hands it (infra/) and its alerts (the chart) are separate files that
only work together. These tests pin the parts a quiet mistake would break:

* the signer is exactly the one the promotion gate uses, never a pattern;
* the frozen list (releases built before signing) is a subset of the passed-staging record, holds
  nothing built after signing began, and has an end date that fails the build once it passes;
* the check runs on every image before the list is consulted (Kyverno insists on it);
* Kyverno and every image it runs are pinned; only the admission program is switched on;
* production has the very same checker as staging (KAV-143): its files match staging's except for
  the namespace, its server is told to hand over the registry login, Prometheus watches it;
* no file holds the AWS account number.

What these cannot test is whether Kyverno really verifies a signature. That was proved against the
real signed images in ECR on a laptop cluster (Lab 95, `scripts/dev/checker_trial.sh`) and then live
on staging."""

from __future__ import annotations

import datetime
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "release"))
import promote  # noqa: E402

POLICY_FILE = ROOT / "deploy" / "image-policy" / "image-validating-policy.yaml"
STAGING = ROOT / "deploy" / "gitops" / "staging"
PROD = ROOT / "deploy" / "gitops" / "prod"
RECORD = ROOT / "deploy" / "promotion" / "passed-staging.json"
# The first release that carried signatures was built on this day (KAV-138). Nothing on the record
# from this day on may be on the frozen list.
SIGNING_BEGAN = "2026-11-13"


def _yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _policy() -> dict[str, Any]:
    # ${registry} and ${kaval_namespace} are filled in by Flux; as plain text they are valid YAML
    # once quoted, and the file keeps them quoted where YAML needs it.
    doc = _yaml(POLICY_FILE)
    assert isinstance(doc, dict)
    return doc


def _match_expression(policy: dict[str, Any]) -> str:
    (rule,) = policy["spec"]["matchImageReferences"]
    return str(rule["expression"])


def _frozen(policy: dict[str, Any]) -> list[str]:
    # The list lives in the rule that decides which images are looked at (KAV-146, ADR-0096).
    return re.findall(r"'(sha-[^']+)'", _match_expression(policy))


def _release() -> dict[str, Any]:
    doc = _yaml(STAGING / "checker-helmrelease.yaml")
    assert isinstance(doc, dict)
    return doc


# ---------------------------------------------------------------- the policy


def test_the_signer_is_exactly_the_one_the_promotion_gate_uses() -> None:
    ident = _policy()["spec"]["attestors"][0]["cosign"]["keyless"]["identities"]
    assert ident == [{"issuer": promote.SIGNER_ISSUER, "subject": promote.SIGNER_IDENTITY}]


def test_no_pattern_matching_anywhere_in_the_policy() -> None:
    text = POLICY_FILE.read_text(encoding="utf-8")
    for banned in ("issuerRegExp", "subjectRegExp", "subjectExpression", "insecureIgnore"):
        assert banned not in text, f"{banned} would loosen who may sign"


def test_it_refuses_and_fails_closed_together() -> None:
    spec = _policy()["spec"]
    # KAV-142 (ADR-0094) flipped the pair on purpose: it refuses (Deny) and a checker that is down
    # or silent also refuses (Fail). Deny with Ignore would let anyone who can stop the checker
    # walk past it; Audit with Fail would take pods down for a check that blocks nothing.
    assert spec["validationActions"] == ["Deny"]
    assert spec["failurePolicy"] == "Fail"
    # What keeps "Fail" safe: the scope is the Kaval namespace only, so the checker's own pods
    # and the cluster's system pods can never be waiting on the checker.
    values = spec["matchConstraints"]["namespaceSelector"]["matchExpressions"][0]["values"]
    assert values == ["${kaval_namespace}"]
    # With Fail, a slow first check (it fetches Sigstore's trust data: 8 s measured against the
    # default 10 s) would be a refusal, so the answer time is the API server's maximum.
    assert spec["webhookConfiguration"]["timeoutSeconds"] == 30


def test_only_kaval_namespaces_are_in_scope() -> None:
    constraints = _policy()["spec"]["matchConstraints"]
    selector = constraints["namespaceSelector"]["matchExpressions"]
    assert selector == [
        {
            "key": "kubernetes.io/metadata.name",
            "operator": "In",
            "values": ["${kaval_namespace}"],
        }
    ]
    assert constraints["resourceRules"][0]["resources"] == ["pods"]


def test_only_this_projects_images_are_matched_and_the_registry_is_a_placeholder() -> None:
    (rule,) = _policy()["spec"]["matchImageReferences"]
    assert set(rule) == {"expression"}, "a glob would match the frozen releases too (KAV-146)"
    assert " ".join(rule["expression"].split()).startswith(
        "ref.startsWith('${registry}/kaval/') &&"
    )


def test_the_only_placeholders_are_the_two_flux_fills_in() -> None:
    found = set(re.findall(r"\$\{([^}]*)\}", POLICY_FILE.read_text(encoding="utf-8")))
    assert found == {"registry", "kaval_namespace"}


def test_the_check_runs_on_every_image_it_looks_at() -> None:
    expr = _policy()["spec"]["validations"][0]["expression"]
    assert "images.containers" in expr and "images.initContainers" in expr
    assert "verifyImageSignatures" in expr and "attestors.release" in expr


def test_the_frozen_list_is_not_in_the_rule_that_judges_an_image() -> None:
    """Found live on production, 2026-11-13 (KAV-146): `verify > 0 || tag is listed` never let a
    listed release in, because Kyverno refuses an image whose signature check ran and failed,
    whatever the rule says. A listed release must not be matched at all."""
    policy = _policy()
    assert "variables" not in policy["spec"], "the list belongs in matchImageReferences"
    assert "frozen" not in policy["spec"]["validations"][0]["expression"]


def test_the_list_is_matched_by_tag_with_and_without_the_digest_kyverno_adds() -> None:
    expr = " ".join(_match_expression(_policy()).split())
    # Kyverno rewrites "repo:tag" to "repo:tag@sha256:..." before it looks (mutateDigest), so the
    # tag must be found in both forms, and only ever as a whole tag (a tag has no ':' in it).
    assert "ref.endsWith(':' + t)" in expr and "ref.contains(':' + t + '@')" in expr
    assert "![" in expr, "a listed release is the one NOT matched"


def test_registry_login_comes_from_the_secret_the_node_writes() -> None:
    assert _policy()["spec"]["credentials"] == {"secrets": ["ecr-cred"]}


# ------------------------------------------------------------ the frozen list


def test_the_frozen_list_is_on_the_passed_staging_record() -> None:
    record = {p["tag"] for p in json.loads(RECORD.read_text(encoding="utf-8"))["passes"]}
    frozen = _frozen(_policy())
    assert frozen, "an empty list is a different decision: delete the list, not leave it empty"
    assert len(frozen) == len(set(frozen)), "a tag is listed twice"
    assert set(frozen) <= record, f"not on the record: {sorted(set(frozen) - record)}"


def test_nothing_built_after_signing_began_is_on_the_frozen_list() -> None:
    passes = json.loads(RECORD.read_text(encoding="utf-8"))["passes"]
    late = {p["tag"] for p in passes if p["passed_at"][:10] >= SIGNING_BEGAN}
    assert not late & set(_frozen(_policy())), (
        "a release from the signed era must be signed, not exempt"
    )


def test_the_frozen_list_has_an_end_date_and_it_has_not_passed() -> None:
    ends = _policy()["metadata"]["annotations"]["kaval.dev/frozen-list-ends"]
    end = datetime.date.fromisoformat(ends)
    assert datetime.date.today() <= end, (
        f"the frozen list ended on {ends}: delete it (and the rollback exception with it), or "
        "extend it in a new ADR line with a reason"
    )


# ----------------------------------------------------------- how Kyverno is installed


def test_kyverno_chart_is_an_exact_version() -> None:
    version = _release()["spec"]["chart"]["spec"]["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), (
        "no ranges: a range would upgrade the checker silently"
    )


def test_every_image_the_chart_runs_is_pinned_to_a_digest() -> None:
    tags: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "image" in node and isinstance(node["image"], dict) and "tag" in node["image"]:
                tags.append(node["image"]["tag"])
            for v in node.values():
                walk(v)

    walk(_release()["spec"]["values"])
    assert len(tags) == 6, (
        "6 image settings: admission, init, migration, upgrade check, cleanup, test"
    )
    for tag in tags:
        assert re.fullmatch(r"v\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", tag), tag


def test_only_the_admission_program_is_switched_on_and_it_has_a_memory_ceiling() -> None:
    values = _release()["spec"]["values"]
    for off in ("backgroundController", "cleanupController", "reportsController"):
        assert values[off] == {"enabled": False}, off
    resources = values["admissionController"]["container"]["resources"]
    assert resources["limits"]["memory"] and resources["requests"]["memory"]


def test_flux_applies_the_policy_with_the_registry_from_the_node_not_from_git() -> None:
    sync = _yaml(STAGING / "image-policy-sync.yaml")
    spec = sync["spec"]
    assert spec["path"] == "./deploy/image-policy"
    assert spec["postBuild"]["substitute"] == {"kaval_namespace": "kaval-staging"}
    assert spec["postBuild"]["substituteFrom"] == [{"kind": "ConfigMap", "name": "kaval-registry"}]
    assert (ROOT / "deploy" / "image-policy" / "kustomization.yaml").is_file()


def test_the_checker_cannot_hold_up_the_rest_of_stagings_sync() -> None:
    # Its policy is its own Flux object, and staging's main one does not wait for it.
    main = _yaml(STAGING / "sync.yaml")
    assert "dependsOn" not in main["spec"]
    assert "healthChecks" not in main["spec"]


def test_staging_lists_the_checker_files() -> None:
    listed = _yaml(STAGING / "kustomization.yaml")["resources"]
    for name in ("checker-source.yaml", "checker-helmrelease.yaml", "image-policy-sync.yaml"):
        assert name in listed, name


# ------------------------------------------------- production has the same checker (KAV-143)

CHECKER_FILES = ("checker-source.yaml", "checker-helmrelease.yaml", "image-policy-sync.yaml")


def _without_comments(path: Path) -> str:
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def test_production_lists_the_checker_files() -> None:
    listed = _yaml(PROD / "kustomization.yaml")["resources"]
    for name in CHECKER_FILES:
        assert (PROD / name).is_file(), name
        assert name in listed, name


def test_production_and_staging_run_the_very_same_checker() -> None:
    # Kustomize cannot share a file across folders (KAV-51), so production holds copies. Compared
    # without comments: the code must be identical, or one environment tests what another does not.
    for name in ("checker-source.yaml", "checker-helmrelease.yaml"):
        assert _without_comments(PROD / name) == _without_comments(STAGING / name), name
    prod = _without_comments(PROD / "image-policy-sync.yaml")
    staging = _without_comments(STAGING / "image-policy-sync.yaml")
    assert prod.replace("kaval-prod", "kaval-staging") == staging
    assert "kaval-staging" not in prod


def test_production_checks_the_production_namespace_and_only_that() -> None:
    spec = _yaml(PROD / "image-policy-sync.yaml")["spec"]
    assert spec["postBuild"]["substitute"] == {"kaval_namespace": "kaval-prod"}
    assert spec["postBuild"]["substituteFrom"] == [{"kind": "ConfigMap", "name": "kaval-registry"}]
    assert spec["path"] == "./deploy/image-policy"
    main = _yaml(PROD / "sync.yaml")["spec"]
    assert "dependsOn" not in main and "healthChecks" not in main  # the checker cannot hold it up
    assert _yaml(PROD / "sync.yaml")["spec"]["path"] == "./deploy/gitops/prod"
    # the namespace the pods really run in is the one the checker looks at
    ns = _yaml(PROD / "namespace.yaml")
    assert ns["metadata"]["name"] == "kaval-prod"


def test_the_production_server_hands_the_checker_its_login_and_the_timer_refreshes_it() -> None:
    prod_tf = (ROOT / "infra" / "envs" / "prod" / "main.tf").read_text(encoding="utf-8")
    assert re.search(r"image_checker_enabled\s*=\s*true", prod_tf)
    script = (ROOT / "infra" / "modules" / "node" / "user_data.sh.tftpl").read_text(
        encoding="utf-8"
    )
    start = script.index("cat > /usr/local/sbin/kaval-gitops-bootstrap <<")
    end = script.index("\nSCRIPT\n", start)
    refreshed = script[start:end]  # the part the 6-hourly timer re-runs
    assert "%{ if image_checker_enabled ~}" in refreshed
    assert "namespace: kyverno" in refreshed  # so the checker's login never outlives the 12 h token
    timer = script[script.index("kaval-gitops-bootstrap.timer <<") :]
    assert "OnUnitActiveSec=6h" in timer[: timer.index("\nUNIT\n")]


def test_staging_hands_the_checker_its_login_and_the_node_does_it_only_when_asked() -> None:
    staging_tf = (ROOT / "infra" / "envs" / "staging" / "main.tf").read_text(encoding="utf-8")
    assert re.search(r"image_checker_enabled\s*=\s*true", staging_tf)
    script = (ROOT / "infra" / "modules" / "node" / "user_data.sh.tftpl").read_text(
        encoding="utf-8"
    )
    block = script[script.index("%{ if image_checker_enabled ~}") :]
    block = block[: block.index("%{ endif ~}")]
    assert "namespace: kyverno" in block and "name: ecr-cred" in block
    variable = (ROOT / "infra" / "modules" / "node" / "variables.tf").read_text(encoding="utf-8")
    assert re.search(r'variable "image_checker_enabled" \{[^}]*default\s*=\s*false', variable, re.S)


def test_prometheus_watches_the_checker_in_staging_and_production() -> None:
    for env in ("staging", "prod"):
        values = _yaml(ROOT / "deploy" / "environments" / env / "values.yaml")
        assert values["imageChecker"] == {"enabled": True}, env
        assert values["prometheus"]["enabled"], env  # the scrape job and rules need Prometheus
    inline = _yaml(PROD / "helmrelease.yaml")["spec"]["values"]  # the hand-kept copy
    assert inline["imageChecker"] == {"enabled": True} and inline["prometheus"]["enabled"]
    chart = _yaml(ROOT / "deploy" / "charts" / "kaval" / "values.yaml")
    assert chart["imageChecker"] == {"enabled": False}


# ------------------------------------------------------ the account number stays out


@pytest.mark.parametrize(
    "path",
    [
        POLICY_FILE,
        ROOT / "deploy" / "image-policy" / "kustomization.yaml",
        STAGING / "checker-source.yaml",
        STAGING / "checker-helmrelease.yaml",
        STAGING / "image-policy-sync.yaml",
        PROD / "checker-source.yaml",
        PROD / "checker-helmrelease.yaml",
        PROD / "image-policy-sync.yaml",
        ROOT / "deploy" / "charts" / "kaval" / "files" / "kaval-image-checker.rules.yaml",
        ROOT / "scripts" / "dev" / "checker_trial.sh",
    ],
)
def test_no_account_number_or_registry_address_in_the_files(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"(?<![0-9])[0-9]{12}(?![0-9])", text)
    assert "dkr.ecr" not in text or "${" in text or path.suffix == ".sh", "a registry address"
