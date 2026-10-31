"""The sweep's verdict, off AWS: it must be silent when clean and loud about a leftover."""

import lab_sweep
from lab_sweep import Snapshot, exit_code, render, verdict


def test_a_clean_account_is_a_pass() -> None:
    findings = verdict(Snapshot())
    assert findings == []
    assert exit_code(findings, billing_only=False) == 0
    assert "nothing left" in render(findings)


def test_a_load_balancer_the_controller_made_is_a_billing_finding() -> None:
    # The classic: terraform destroy never knew about it, so only the sweep can see it.
    findings = verdict(Snapshot(load_balancers=["app/k8s-kavallab-abc/123"]))
    assert [(f.billing, f.kind) for f in findings] == [(True, "elasticloadbalancing/loadbalancer")]
    assert exit_code(findings, billing_only=True) == 1
    assert "BILLING" in render(findings)


def test_a_disk_the_database_left_behind_is_billing() -> None:
    assert exit_code(verdict(Snapshot(volumes=["vol-0abc"])), billing_only=True) == 1


def test_a_leftover_network_or_role_is_not_billing_but_still_fails_the_strict_check() -> None:
    findings = verdict(Snapshot(vpcs=["vpc-1"], roles=["kaval-lab-eks-node"]))
    assert not any(f.billing for f in findings)
    assert exit_code(findings, billing_only=True) == 0
    assert exit_code(findings, billing_only=False) == 1


def test_the_same_resource_seen_twice_is_one_finding() -> None:
    snap = Snapshot(tagged=[("eks/cluster", "kaval-lab-eks")], clusters=["kaval-lab-eks"])
    assert len(verdict(snap)) == 1


def test_billing_comes_first_and_an_untracked_kind_is_not_billing() -> None:
    snap = Snapshot(tagged=[("ssm/parameter", "p"), ("ec2/natgateway", "nat-1")])
    findings = verdict(snap)
    assert [f.kind for f in findings] == ["ec2/natgateway", "ssm/parameter"]
    assert findings[0].billing and not findings[1].billing


# ── the label index lags behind a deletion; each tagged thing is confirmed with its own service ──


class _Answer:
    def __init__(self, code: int, out: str = "{}", err: str = "") -> None:
        self.returncode, self.stdout, self.stderr = code, out, err


def _ask(monkeypatch, answers: dict[str, _Answer]) -> list[tuple[str, ...]]:
    """Make the aws call answer from a table keyed by the id asked about; return the calls made."""
    calls: list[tuple[str, ...]] = []

    def fake(args: list[str]) -> _Answer:
        calls.append(tuple(args))
        for ident, answer in answers.items():
            if ident in args:
                return answer
        return _Answer(1, err="something unexpected")

    monkeypatch.setattr(lab_sweep, "_run", fake)
    return calls


def test_a_tagged_disk_that_aws_says_does_not_exist_is_dropped_and_named_as_stale(
    monkeypatch,
) -> None:
    _ask(monkeypatch, {"vol-gone": _Answer(254, err="InvalidVolume.NotFound: not exist")})
    kept, stale = lab_sweep.drop_gone([("ec2/volume", "vol-gone")])
    assert kept == [] and stale == [("ec2/volume", "vol-gone")]
    assert "vol-gone" in lab_sweep.render_stale(stale)


def test_a_tagged_disk_that_still_exists_is_still_a_billing_finding(monkeypatch) -> None:
    # The case that must never be hidden.
    _ask(monkeypatch, {"vol-real": _Answer(0, out='{"Volumes": [{"VolumeId": "vol-real"}]}')})
    kept, stale = lab_sweep.drop_gone([("ec2/volume", "vol-real")])
    assert kept == [("ec2/volume", "vol-real")] and stale == []
    assert exit_code(verdict(Snapshot(tagged=kept)), billing_only=True) == 1


def test_an_error_that_is_not_a_plain_not_found_keeps_the_thing_reported(monkeypatch) -> None:
    _ask(monkeypatch, {"vol-x": _Answer(255, err="Could not connect to the endpoint URL")})
    kept, stale = lab_sweep.drop_gone([("ec2/volume", "vol-x")])
    assert kept == [("ec2/volume", "vol-x")] and stale == []


def test_a_kind_with_no_existence_check_is_never_asked_about_and_stays_reported(
    monkeypatch,
) -> None:
    calls = _ask(monkeypatch, {})
    kept, stale = lab_sweep.drop_gone([("iam/role", "kaval-lab-eks-node")])
    assert kept == [("iam/role", "kaval-lab-eks-node")] and stale == [] and calls == []


def test_a_nat_gateway_that_is_deleted_counts_as_gone_but_one_that_is_available_does_not(
    monkeypatch,
) -> None:
    _ask(
        monkeypatch,
        {
            "nat-old": _Answer(0, out='{"NatGateways": [{"State": "deleted"}]}'),
            "nat-live": _Answer(0, out='{"NatGateways": [{"State": "available"}]}'),
        },
    )
    kept, stale = lab_sweep.drop_gone(
        [("ec2/natgateway", "nat-old"), ("ec2/natgateway", "nat-live")]
    )
    assert stale == [("ec2/natgateway", "nat-old")]
    assert kept == [("ec2/natgateway", "nat-live")]


def test_nothing_stale_prints_nothing_extra() -> None:
    assert lab_sweep.render_stale([]) == ""
