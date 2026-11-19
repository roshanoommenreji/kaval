"""Tests for the weekly image watch's decisions (KAV-167, ADR-0106). No Trivy, no network: Trivy's
answer is a small hand-made dictionary, and the repository files are the real ones."""

from pathlib import Path

import image_watch as w
import pytest

ROOT = Path(__file__).resolve().parents[2]


def _vuln(vid="CVE-2099-0001", pkg="libfoo", installed="1.0", fixed="1.1", sev="HIGH"):
    return {"VulnerabilityID": vid, "PkgName": pkg, "InstalledVersion": installed,
            "FixedVersion": fixed, "Severity": sev}


def _answer(n_packages, vulns=()):
    return {"Results": [{"Packages": [{"Name": f"p{i}"} for i in range(n_packages)],
                         "Vulnerabilities": list(vulns)}]}


# -- reading the repository ---------------------------------------------------------------------

def test_the_real_production_values_pin_at_least_one_sha_tag():
    values = (ROOT / "deploy/environments/prod/values.yaml").read_text(encoding="utf-8")
    tags = w.pinned_tags(values)
    assert tags and all(t.startswith("sha-") for t in tags)


def test_a_commented_out_tag_is_not_a_pin():
    text = "  # tag: sha-1234567\n  tag: sha-abcdef0   # the real one\n"
    assert w.pinned_tags(text) == ["sha-abcdef0"]


def test_the_shipped_base_image_is_the_last_from_without_alias_or_platform():
    text = ("FROM --platform=$BUILDPLATFORM golang:1.27@sha256:aa AS build\n"
            "FROM python:3.11-slim@sha256:bb AS wheel\n"
            "FROM --platform=linux/arm64 python:3.11-slim@sha256:cc\n")
    assert w.shipped_base_image(text) == "python:3.11-slim@sha256:cc"


def test_every_real_dockerfile_has_a_shipped_base_image_pinned_by_digest():
    dockerfiles = sorted((ROOT / "services").glob("*/Dockerfile"))
    assert dockerfiles
    for df in dockerfiles:
        ref = w.shipped_base_image(df.read_text(encoding="utf-8"))
        assert "@sha256:" in ref, f"{df} ships an image that is not pinned by digest: {ref}"


def test_a_dockerfile_without_from_is_an_error():
    with pytest.raises(ValueError):
        w.shipped_base_image("RUN echo hi\n")


def test_third_party_images_skip_kavals_own_and_block_openers():
    prom = "quay.io/prometheus/prometheus:v3@sha256:ab"
    text = (
        "db:\n  image: pgvector/pgvector:pg16\ngateway:\n  image:\n    repository: kaval/gateway\n"
        f"  other: kaval/x:1\n  image: kaval/thing:1\n  image: {prom}\n"
    )
    assert w.third_party_images(text) == ["pgvector/pgvector:pg16", prom]


def test_the_real_chart_names_third_party_images():
    values = (ROOT / "deploy/charts/kaval/values.yaml").read_text(encoding="utf-8")
    refs = w.third_party_images(values)
    assert any(r.startswith("pgvector/pgvector") for r in refs)


# -- reading Trivy's answer ---------------------------------------------------------------------

def test_summarize_counts_packages_and_keeps_findings():
    r = w.summarize("main: uv.lock", "lock", _answer(40, [_vuln()]))
    assert r.packages == 40 and len(r.findings) == 1 and r.findings[0].package == "libfoo"


def test_a_trivy_answer_with_no_results_has_no_packages():
    assert w.summarize("t", "image", {}).packages == 0


def test_clean_when_enough_packages_and_no_findings():
    status, lines = w.verdict([w.summarize("a", "lock", _answer(60)),
                               w.summarize("b", "image", _answer(90))])
    assert (status, lines) == ("clean", [])


def test_found_names_the_target_the_id_the_package_and_the_fix():
    status, lines = w.verdict([w.summarize("production pin sha-1234567: uv.lock", "lock",
                                           _answer(60, [_vuln(sev="CRITICAL")]))])
    assert status == "found"
    assert lines == [
        "production pin sha-1234567: uv.lock: CRITICAL CVE-2099-0001 in libfoo 1.0 (fixed in 1.1)"
    ]


def test_a_scan_that_read_too_little_is_broken_not_clean():
    status, lines = w.verdict([w.summarize("a", "lock", _answer(3))])
    assert status == "broken" and "not an all-clear" in lines[0]


def test_broken_beats_found_and_still_lists_the_finding():
    status, lines = w.verdict([w.summarize("a", "lock", _answer(2)),
                               w.summarize("b", "image", _answer(50, [_vuln()]))])
    assert status == "broken" and any("CVE-2099-0001" in line for line in lines)


def test_a_target_that_could_not_be_scanned_is_broken_and_says_why():
    bad = w.Result(target="main: pgvector/pgvector:pg16", kind="image", packages=0,
                   error="timed out")
    status, lines = w.verdict([w.summarize("a", "lock", _answer(60)), bad])
    assert status == "broken"
    assert lines == ["main: pgvector/pgvector:pg16: could not be scanned (timed out)"]


def test_a_failed_target_does_not_hide_findings_in_the_others():
    bad = w.Result(target="x", kind="image", packages=0, error="timed out")
    status, lines = w.verdict([bad, w.summarize("a", "lock", _answer(60, [_vuln()]))])
    assert status == "broken" and any("CVE-2099-0001" in line for line in lines)


def test_nothing_scanned_is_broken():
    assert w.verdict([])[0] == "broken"


def test_the_planted_test_file_needs_only_its_three_libraries():
    assert w.verdict([w.summarize("p", "plant", _answer(3, [_vuln()]))])[0] == "found"


def test_render_for_each_status_and_a_cut_for_a_huge_list():
    assert "nothing new" in w.render("clean", [])
    assert "known problems" in w.render("found", ["x"])
    assert "could not look" in w.render("broken", ["x"])
    huge = w.render("found", [f"line {i} " + "x" * 200 for i in range(500)])
    assert len(huge) < w.MAX_REPORT + 200 and "cut short" in huge


def test_many_problems_in_one_package_become_one_line():
    many = [_vuln(vid=f"CVE-2099-{i:04d}", pkg="stdlib", installed="v1.27.1",
                  fixed="1.27.3, 1.26.9")
            for i in range(5)]
    status, lines = w.verdict([w.summarize("main: prometheus", "image", _answer(400, many))])
    assert status == "found" and len(lines) == 1
    assert "5 problems in stdlib v1.27.1" in lines[0]
    assert "CVE-2099-0000, CVE-2099-0001, CVE-2099-0002, ..." in lines[0]
    assert "fixes in 1.27.3, 1.26.9" in lines[0]


def test_one_critical_among_many_is_named_as_the_worst():
    mixed = [_vuln(vid="CVE-2099-0001", sev="HIGH"), _vuln(vid="CVE-2099-0002", sev="CRITICAL")]
    _, lines = w.verdict([w.summarize("t", "image", _answer(50, mixed))])
    assert "worst CRITICAL" in lines[0]


def test_a_base_image_needs_as_many_packages_as_any_image():
    assert w.verdict([w.summarize("b", "base", _answer(3))])[0] == "broken"
    assert w.verdict([w.summarize("b", "base", _answer(80))])[0] == "clean"


def test_a_long_digest_is_cut_for_reading_and_a_plain_tag_is_not():
    assert w.short("python:3.11@sha256:" + "ab" * 32) == "python:3.11@abababababab"
    assert w.short("pgvector/pgvector:pg16") == "pgvector/pgvector:pg16"
