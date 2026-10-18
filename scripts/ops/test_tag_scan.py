"""The live-account scan's decisions (KAV-93), on made-up resources: no AWS call."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

import tag_scan as ts  # noqa: E402

OK = {"Project": "kaval", "Env": "prod", "ManagedBy": "terraform"}


def res(kind: str, rid: str, **tags: str) -> dict[str, Any]:
    return {"kind": kind, "id": rid, "tags": tags}


def test_a_fully_labelled_account_has_no_findings() -> None:
    report = ts.scan([res("ec2/instance", "i-1", **OK), res("ec2/volume", "vol-1", **OK)])
    assert report.findings == [] and report.by_env["prod"] == 2


def test_a_missing_key_is_listed_with_what_is_missing() -> None:
    bad = {"Project": "kaval", "Name": "snap"}
    report = ts.scan([res("ec2/snapshot", "snap-1", **bad)])
    want = [("ec2/snapshot", "snap-1", "snap", ["missing Env", "missing ManagedBy"])]
    assert report.findings == want


def test_a_resource_with_no_tags_at_all_is_still_found() -> None:
    report = ts.scan([res("ec2/volume", "vol-9")])
    assert len(report.findings) == 1 and "missing Project" in report.findings[0][3]


def test_another_projects_resources_are_counted_never_listed() -> None:
    other = {"Project": "stock-trader"}
    report = ts.scan([res("ec2/vpc", "vpc-1", **other), res("s3/bucket", "b", **other)])
    assert report.findings == [] and report.others == {"stock-trader": 2}


def test_the_same_resource_seen_by_two_sources_counts_once() -> None:
    twice = [res("ec2/instance", "i-1", **OK), res("ec2/instance", "i-1", **OK)]
    assert ts.scan(twice).seen == 1


def test_session_records_are_ignored() -> None:
    assert ts.scan([res("ssm/session", "s-1")]).seen == 0


def test_the_output_names_the_findings_and_masks_account_ids() -> None:
    report = ts.scan([res("ec2/volume", "vol-9"), res("ec2/vpc", "v", Project="stock-trader")])
    account = "1111" + "2222" + "3333"
    text = ts._mask(ts.render(report) + " arn:aws:ec2:ap-south-1:" + account)
    assert "Missing or wrong labels: 1" in text and "vol-9" in text
    assert "1 belonging to stock-trader" in text and "<acct>" in text and account not in text


def test_a_terminated_server_the_tagging_service_still_lists_is_not_a_finding() -> None:
    """Found live 2026-10-18 after staging's old server was replaced: the tagging service kept
    listing it, with its old labels, for about an hour, and the scan reported it."""
    from tag_scan import drop_terminated

    listed = [
        {
            "kind": "ec2/instance",
            "id": "i-0old",
            "tags": {"Project": "kaval", "Name": "kaval-staging"},
        },
        {
            "kind": "ec2/instance",
            "id": "i-0new",
            "tags": {"Project": "kaval", "Name": "kaval-staging"},
        },
        {
            "kind": "ec2/volume",
            "id": "i-0old",
            "tags": {"Project": "kaval"},
        },  # same id text, other kind
    ]
    kept = drop_terminated(listed, {"i-0old"})
    assert [(r["kind"], r["id"]) for r in kept] == [
        ("ec2/instance", "i-0new"),
        ("ec2/volume", "i-0old"),
    ]
