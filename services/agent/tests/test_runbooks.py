"""Chunking a runbook into its `## ` sections (KAV-40)."""

from __future__ import annotations

from kaval_agent.runbooks import chunk_runbook

SAMPLE = """\
# Database lost or corrupted

Some lead-in prose that isn't part of any `## ` section.

## Signals

- Services logging `connection refused`

## Likely causes

Ordered by how often they actually happen.

1. Someone ran something destructive
2. A migration failed part-way

## Remediation

### Assess what you will lose first

Two recovery sources exist.

### Stop the writers

**Reversible:** yes.

## Do not

- Do not restore because a service can't connect.

## Empty section

## Related

- A link.
"""


def test_one_chunk_per_h2_section() -> None:
    chunks = chunk_runbook(SAMPLE)
    assert [c.heading for c in chunks] == [
        "Signals", "Likely causes", "Remediation", "Do not", "Related",
    ]


def test_an_empty_heading_produces_no_chunk() -> None:
    chunks = chunk_runbook(SAMPLE)
    assert "Empty section" not in [c.heading for c in chunks]


def test_ordinal_is_file_order() -> None:
    chunks = chunk_runbook(SAMPLE)
    assert [c.ordinal for c in chunks] == sorted(c.ordinal for c in chunks)


def test_content_is_prefixed_with_the_title_and_heading() -> None:
    signals = next(c for c in chunk_runbook(SAMPLE) if c.heading == "Signals")
    assert signals.content.startswith("Database lost or corrupted — Signals\n\n")
    assert "connection refused" in signals.content


def test_remediation_keeps_its_nested_h3_subsections_as_one_chunk() -> None:
    remediation = next(c for c in chunk_runbook(SAMPLE) if c.heading == "Remediation")
    assert "Assess what you will lose first" in remediation.content
    assert "Stop the writers" in remediation.content


def test_content_hash_is_stable_and_changes_with_content() -> None:
    a = chunk_runbook(SAMPLE)
    b = chunk_runbook(SAMPLE)
    assert [c.content_hash for c in a] == [c.content_hash for c in b]

    edited = SAMPLE.replace("connection refused", "connection reset")
    c = chunk_runbook(edited)
    signals_before = next(x for x in a if x.heading == "Signals")
    signals_after = next(x for x in c if x.heading == "Signals")
    assert signals_before.content_hash != signals_after.content_hash
    # An edit to one section doesn't touch another section's hash.
    causes_before = next(x for x in a if x.heading == "Likely causes")
    causes_after = next(x for x in c if x.heading == "Likely causes")
    assert causes_before.content_hash == causes_after.content_hash


def test_no_title_falls_back_to_the_heading_alone() -> None:
    chunks = chunk_runbook("## Signals\n\nsomething broke\n")
    assert chunks[0].content == "Signals\n\nsomething broke"


def test_no_h2_sections_is_an_empty_list() -> None:
    assert chunk_runbook("# Just a title\n\nAnd some prose, no sections.\n") == []
