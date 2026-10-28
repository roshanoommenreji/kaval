"""The sweep's verdict, off AWS: it must be silent when clean and loud about a leftover."""

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
