"""The EKS lab must be the same chart as everywhere else, and its pieces must agree (ADR-0067).

CLAUDE.md: "If lab-eks needs a template change that prod-k3s doesn't, the portability claim is
broken." These checks make that a failing test rather than a promise:

  * the lab installs the chart from the same path as production, and nothing in the chart mentions
    the lab or EKS's IRSA label (the label is added by the lab's own deploy file, not by the chart)
  * the names that must match across Terraform and the deploy files do match (a mismatch fails
    silently on a live cluster: the service account gets no AWS login)
  * the lab's list of Flux objects holds every file it should, and keeps the hand-applied switch out
  * nothing committed holds an account number
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
GITOPS = ROOT / "deploy" / "gitops" / "lab-eks"
CHART = ROOT / "deploy" / "charts" / "kaval"
TF = ROOT / "infra" / "modules" / "eks-lab" / "variables.tf"


def _load(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]


def _release() -> dict:
    return _load(GITOPS / "helmrelease.yaml")[0]


def test_the_lab_installs_the_same_chart_path_as_production() -> None:
    prod = _load(ROOT / "deploy" / "gitops" / "prod" / "helmrelease.yaml")[0]
    assert _release()["spec"]["chart"]["spec"]["chart"] == prod["spec"]["chart"]["spec"]["chart"]


def test_the_chart_knows_nothing_about_the_lab_or_irsa() -> None:
    for template in (CHART / "templates").glob("*"):
        text = template.read_text(encoding="utf-8").lower()
        assert "lab-eks" not in text, template.name
        assert "eks.amazonaws.com" not in text, template.name
    values = (CHART / "values.yaml").read_text(encoding="utf-8")
    assert "lab-eks" not in values and "role-arn" not in values


def test_the_irsa_label_is_added_by_the_post_renderer_to_the_executors_account() -> None:
    release = _release()
    patch = release["spec"]["postRenderers"][0]["kustomize"]["patches"][0]
    # The chart names the executor's service account <release>-executor (templates/executor.yaml).
    assert patch["target"]["name"] == f"{release['spec']['releaseName']}-executor"
    assert "eks.amazonaws.com/role-arn" in patch["patch"]


def test_terraform_trusts_exactly_the_service_account_the_chart_makes() -> None:
    tf = TF.read_text(encoding="utf-8")
    release = _release()["spec"]
    namespace = release["targetNamespace"]
    account = f"{release['releaseName']}-executor"
    assert re.search(rf'variable "executor_namespace".*?default\s*=\s*"{namespace}"', tf, re.S)
    assert re.search(rf'variable "executor_service_account".*?default\s*=\s*"{account}"', tf, re.S)


def test_the_namespace_the_gitops_files_use_is_the_one_the_release_installs_into() -> None:
    namespace = _load(GITOPS / "namespace.yaml")[0]["metadata"]["name"]
    assert namespace == _release()["spec"]["targetNamespace"]
    assert _load(GITOPS / "ingress.yaml")[0]["metadata"]["namespace"] == namespace
    for policy in _load(GITOPS / "hardening" / "network-policies.yaml"):
        assert policy["metadata"]["namespace"] == namespace


def test_the_ingress_points_at_the_service_the_chart_makes_and_is_never_open_to_the_world() -> None:
    ingress = _load(GITOPS / "ingress.yaml")[0]
    backend = ingress["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]
    assert backend["name"] == f"{_release()['spec']['releaseName']}-gateway"
    cidr = ingress["metadata"]["annotations"]["alb.ingress.kubernetes.io/inbound-cidrs"]
    assert cidr == "${LAB_ALLOWED_CIDR}"  # filled from the hand-made ConfigMap, never 0.0.0.0/0
    assert "0.0.0.0/0" not in (GITOPS / "ingress.yaml").read_text(encoding="utf-8")


def test_network_policies_select_pods_by_labels_the_pods_really_carry() -> None:
    # Found live: part-of is on the chart's objects, not its pods, so the policies matched 1 of 7.
    helpers = (CHART / "templates" / "_helpers.tpl").read_text(encoding="utf-8")
    pod_labels = helpers.split('define "kaval.selectorLabels"')[1].split("end")[0]
    for policy in _load(GITOPS / "hardening" / "network-policies.yaml"):
        selectors = [policy["spec"]["podSelector"]]
        for rule in policy["spec"].get("ingress", []):
            peers = rule.get("from", [])
            selectors += [peer["podSelector"] for peer in peers if "podSelector" in peer]
        for selector in selectors:
            for key in selector.get("matchLabels", {}):
                assert key in pod_labels, f"{policy['metadata']['name']}: pods have no {key}"


def test_the_load_balancer_does_not_health_check_the_path_that_needs_a_model() -> None:
    # Found live: /healthz also checks the language model, the lab has none, so it answers 503.
    annotations = _load(GITOPS / "ingress.yaml")[0]["metadata"]["annotations"]
    assert annotations["alb.ingress.kubernetes.io/healthcheck-path"] != "/healthz"


def test_the_kustomization_lists_every_file_except_the_hand_applied_switch() -> None:
    listed = set(_load(GITOPS / "kustomization.yaml")[0]["resources"])
    present = {p.name for p in GITOPS.glob("*.yaml")} - {"kustomization.yaml"}
    # hardening-sync.yaml is applied by hand on purpose: it is the switch for the strict-rules step.
    assert present - listed == {"hardening-sync.yaml"}
    assert listed <= present


def test_the_strict_rules_switch_is_not_managed_by_the_labs_own_flux_object() -> None:
    kustomization = _load(GITOPS / "kustomization.yaml")[0]["resources"]
    assert "hardening-sync.yaml" not in kustomization


def test_the_load_balancer_controller_version_matches_the_policy_it_is_given() -> None:
    text = (GITOPS / "load-balancer-controller.yaml").read_text(encoding="utf-8")
    assert "version: 3.5.0" in text  # chart 3.5.0 = controller v3.5.0
    module = (ROOT / "infra" / "modules" / "eks-lab" / "main.tf").read_text(encoding="utf-8")
    assert "v3.5.0" in module


def test_the_lab_asks_for_no_secret_and_commits_no_account_number() -> None:
    files = [p for p in GITOPS.rglob("*.yaml")]
    files += [ROOT / "deploy" / "environments" / "lab-eks" / "values.yaml"]
    env = ROOT / "infra" / "envs" / "lab-eks"
    files += [*env.glob("*.tf"), *env.glob("*.tfvars.example")]  # not the git-ignored ones
    files += list((ROOT / "infra" / "modules" / "eks-lab").glob("*.tf"))
    files += list((ROOT / "infra" / "modules" / "lab-reaper").glob("*.tf"))
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"(?<!\d)\d{12}(?!\d)", text), f"{path.name}: account number?"
        assert "arn:aws:iam::" not in text.replace("arn:aws:iam::aws:policy", ""), path.name


def test_the_database_disk_is_xfs_because_ext4_leaves_a_folder_postgres_refuses() -> None:
    # Found live: the chart mounts the disk on Postgres's data directory; ext4 adds lost+found.
    sc = _load(GITOPS / "storageclass.yaml")[0]
    assert sc["parameters"]["csi.storage.k8s.io/fstype"] == "xfs"
    # parameters of a class cannot change in place, so Flux must be allowed to replace it
    assert sc["metadata"]["annotations"]["kustomize.toolkit.fluxcd.io/force"] == "enabled"


def test_the_servers_may_pull_aws_add_on_images_and_only_those_besides_kavals() -> None:
    # Found live: without it the add-on pods are refused (403) and no server ever becomes Ready.
    module = (ROOT / "infra" / "modules" / "eks-lab" / "main.tf").read_text(encoding="utf-8")
    start = module.split('resource "aws_iam_role_policy" "node_ecr_addons"')[1]
    block = start.split("\nresource ")[0]
    for repo in ("repository/eks/*", "repository/amazon-k8s-cni*", "repository/amazon/*"):
        assert repo in block
    assert "ecr:PutImage" not in block and '"*"' not in block  # pull only, no wildcard repository


def test_the_install_does_not_wait_because_the_migration_hook_runs_after_the_wait() -> None:
    # Found live: with the chart's own Postgres the migration is a post-install hook. Helm runs it
    # only after every Deployment is ready, and they cannot be ready before the tables exist.
    chart_job = (CHART / "templates" / "migrate-job.yaml").read_text(encoding="utf-8")
    assert "post-install" in chart_job  # the reason; if the chart changes, revisit disableWait
    assert _release()["spec"]["install"]["disableWait"] is True


def test_subnet_tags_added_from_outside_are_not_stripped_by_the_next_apply() -> None:
    # Found live: aws_ec2_tag adds the tags, the subnet resource then plans to remove them again.
    env = (ROOT / "infra" / "envs" / "lab-eks" / "main.tf").read_text(encoding="utf-8")
    assert 'key_prefixes = ["kubernetes.io/"]' in env
