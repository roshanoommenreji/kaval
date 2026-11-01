"""The policy keyword list against names written to get past it (KAV-112, ADR-0072).

Needs the real `opa` binary, like test_policy.py (skips on a laptop without it; CI sets
KAVAL_REQUIRE_OPA=1). Kept in its own file so those skips do not hide the offline injection tests.

What this pins, and what it deliberately does not. `policy.rego` refuses an action whose type
CONTAINS a dangerous word, and the words are a list: a name that avoids every word is not `never`.
That is by design ("loose in the safer direction" is the rule's comment, and it is a second layer,
not the first). The first layer is that the type must be one of the three Kaval can carry out. So
here: the words that are on the list always win, and no name, however it is spelt, is ever `auto`.
The test that a misspelt name cannot run at all is in test_injection.py.
"""

from __future__ import annotations

import os
import shutil

import pytest
from kaval_agent import policy
from kaval_shared.models import BlastRadius, PolicyClass

if shutil.which(policy.DEFAULT_OPA_BIN) is None:
    _reason = "opa not on PATH - install it (docs/labs/lab-00-toolchain.md) to run these tests"
    if os.environ.get("KAVAL_REQUIRE_OPA") == "1":
        pytest.fail(_reason, pytrace=False)
    pytestmark = pytest.mark.skip(reason=_reason)


@pytest.mark.parametrize("name", [
    "EC2 Instance Termination", "terminate-EC2", "TERMINATE_ec2_instances", "delete-namespace",
    "NamespaceDelete", "remove_PersistentVolumeClaim", "detach-iam-role", "change_billing_alarm",
    "rm_pvc", "Delete PVC",
])
def test_a_name_containing_a_listed_word_is_never_however_it_is_spelt_or_cased(name: str) -> None:
    assert policy.classify(name, BlastRadius.pod, True, 0.99) == PolicyClass.never


@pytest.mark.parametrize("name", [
    "terminate_instance", "delete_ns", "kill_node", "drop_database", "wipe_volume",
    "ЕС2_terminate",        # Cyrillic E and S: looks like "EC2", is not
    "e​c2", "ec 2", "e-c-2",       # split so the word never appears whole
    "restart_pod; delete namespace",  # contains a listed word, so this one IS never
])
def test_no_name_however_clever_is_ever_auto(name: str) -> None:
    assert policy.classify(name, BlastRadius.pod, True, 0.99) != PolicyClass.auto


def test_the_widest_blast_radius_is_never_whatever_the_name_says() -> None:
    for name in ("restart_pod", "terminate_instance", "ЕС2_terminate"):
        assert policy.classify(name, BlastRadius.account, True, 0.99) == PolicyClass.never


def test_a_confident_model_cannot_promote_an_unpromoted_action() -> None:
    """Confidence 1.0, reversible, smallest blast radius: still not `auto`. Only an entry in
    policy/promotions.json (an ADR, and rows to point at) makes an action class auto."""
    assert policy.classify("restart_pod", BlastRadius.pod, True, 1.0) != PolicyClass.auto
    assert policy.classify("increase_memory_limit", BlastRadius.deployment, True, 1.0) \
        != PolicyClass.auto
