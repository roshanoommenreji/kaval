"""The live proof script for the image checker (KAV-143) does only what its header says.

It cannot be run for real without a live node, so these pin what a quiet mistake would break: the
throwaway pod holds nothing and is labelled for cleanup, the shell it sends to the node is valid,
the steps that create or delete refuse to run without `--yes`, and the output never shows an
account number."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import checker_proof as cp  # noqa: E402


def test_the_throwaway_pod_only_sleeps_holds_no_secret_and_is_labelled() -> None:
    text = cp.pod_manifest("p", "kaval-prod", "REG", cp.SIGNED_TAG)
    assert "labels: {proof: kav143}" in text
    assert "namespace: kaval-prod" in text
    assert 'command: ["sleep","600"]' in text
    assert "automountServiceAccountToken: false" in text
    for holds_something in ("env:", "volumes:", "volumeMounts:", "secretKeyRef", "hostPath"):
        assert holds_something not in text, holds_something
    assert "imagePullSecrets: [{name: ecr-cred}]" in text  # the registry login only
    assert f"image: ${{REG}}/kaval/gateway:{cp.SIGNED_TAG}" in text
    assert "limits: {memory: 64Mi}" in text  # it cannot eat the node


def test_the_signed_tag_is_the_signed_release_and_the_planted_name_is_not_a_real_tag() -> None:
    assert cp.SIGNED_TAG == "sha-152b59e"  # the one signed release in ECR (ADR-0094)
    assert cp.PLANTED_TAG not in {"latest", cp.SIGNED_TAG}


@pytest.mark.parametrize("step", ["state", "alerts", "signed", "unsigned", "frozen", "restart",
                                  "clean"])
@pytest.mark.parametrize("env", ["prod", "staging"])
def test_every_remote_step_is_valid_shell_for_its_own_namespace(step: str, env: str) -> None:
    script = cp.remote_script(step, cp.NAMESPACES[env], env)
    assert f"NS=kaval-{env}\n" in script
    assert "kaval-prod" not in script.replace("NS=kaval-prod", "") or env == "prod"
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash on this machine")
    done = subprocess.run([bash, "-n"], input=script, text=True, capture_output=True, check=False)
    assert done.returncode == 0, done.stderr


def test_clean_deletes_only_what_this_script_labelled() -> None:
    script = cp.remote_script("clean", "kaval-prod", "prod")
    assert "-l proof=kav143" in script
    assert "delete namespace" not in script and "--all" not in script


def test_nothing_but_the_three_named_pods_is_ever_created() -> None:
    created = {s: cp.remote_script(s, "kaval-prod", "prod").count("kubectl apply")
               for s in ("state", "alerts", "signed", "unsigned", "frozen", "restart", "clean")}
    assert created == {"state": 0, "alerts": 0, "signed": 1, "unsigned": 1, "frozen": 1,
                       "restart": 0, "clean": 0}


def test_restart_restarts_the_checker_in_place_and_touches_nothing_else() -> None:
    # KAV-144: the blind spot is a restart INSIDE the same pod. Deleting the pod gives a new
    # address, which the alert always caught, so it would prove nothing.
    script = cp.remote_script("restart", "kaval-staging", "staging")
    assert "$CRI stop \"$OLD\"" in script
    # k3s from its binary has only `k3s crictl` (found live on staging); either must work
    assert "CRI=$(command -v crictl || echo 'k3s crictl')" in script
    assert "--label io.kubernetes.pod.namespace=kyverno" in script  # only the checker's container
    assert "--name '^kyverno$'" in script
    for wider in ("kubectl delete", "crictl rm", "crictl stopp", "systemctl", "reboot"):
        assert wider not in script, wider
    assert "kyverno-resource-validating-webhook-cfg" in script  # waits for its hook to be back
    assert "process_start_time_seconds" in script  # the number the alert uses, before and after
    assert "restart" in cp.MUTATING


def test_it_will_not_plant_an_image_that_is_really_the_signed_one(
        monkeypatch: pytest.MonkeyPatch) -> None:
    # Found live on production: two release tags can name ONE image, and a signature belongs to the
    # image. Planting from such a tag made the "unsigned" pod a signed one.
    calls: list[tuple[str, ...]] = []

    def same_image(*args: str) -> str:
        calls.append(args)
        return "sha256:" + "a" * 64

    monkeypatch.setattr(cp, "_aws", same_image)
    assert "refusing to plant" in cp.laptop_step("plant")
    assert not [c for c in calls if "put-image" in c]  # nothing was written to the registry


def test_it_plants_when_the_base_is_a_different_image(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []

    def different(*args: str) -> str:
        calls.append(args)
        if "describe-images" in args:
            return "sha256:" + ("b" if f"imageTag={cp.BASE_TAG}" in args else "c") * 64
        if "batch-get-image" in args:
            return '{"mediaType": "application/vnd.oci.image.index.v1+json"}'
        return cp.PLANTED_TAG

    monkeypatch.setattr(cp, "_aws", different)
    assert cp.laptop_step("plant") == f"planted {cp.PLANTED_TAG}"
    assert [c for c in calls if "put-image" in c]


def test_steps_that_change_something_refuse_without_yes(capsys: pytest.CaptureFixture[str]) -> None:
    for step in cp.MUTATING:
        assert cp.main(["prod", step]) == 2
        assert "--yes" in capsys.readouterr().out


def test_read_only_steps_do_not_need_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []
    def fake(env: str, script: str) -> str:
        sent.append(script)
        return "ok"

    monkeypatch.setattr(cp, "run_on_node", fake)
    assert cp.main(["prod", "state"]) == 0 and cp.main(["prod", "alerts"]) == 0
    assert len(sent) == 2


def test_an_account_number_never_reaches_the_screen() -> None:
    twelve = "1" * 12
    host = ".dkr.ecr.ap-south-1.amazonaws.com"
    assert cp.scrub(f"{twelve}{host}") == f"<acct>{host}"
    digest = "sha256:" + "a" * 64
    assert cp.scrub(digest) == digest  # a digest is not an account number
    assert cp.scrub("1234567890123") == "1234567890123"  # 13 digits: not one
