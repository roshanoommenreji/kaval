"""The executor's half of removal: hand an approved order over, and say so honestly.

(KAV-95, ADR-0063.)

The executor holds no AWS login and never will. When a person approves a removal step, this writes
ONE order into ONE ConfigMap in the cluster. The server's timer carries it out to AWS, the removal
worker (infra/modules/waste-removal) re-checks AWS itself and acts, and its answer comes back the
same way as a `removal_done` or `removal_failed` signal. So what this records is "handed over",
never "done": the execution row says exactly that, and the real result arrives later.

Three things are required before an order is written, on top of the policy re-check and the
person's approval that `executor.execute_one` already demands:

- removal is switched on (`KAVAL_REMOVAL_ENABLED=true`), the one switch;
- the proposal was made by the removal planner (`model == "rule:removal"`): a proposal from the
  model, or from any other route, cannot order a deletion, whatever it says;
- the incident is about one of the two removable causes, and the action's target is an AWS id of
  the right kind for its step (a disk for a disk step, a copy for a copy step).

Its only right in the cluster is `get`/`patch` on that one ConfigMap, by name
(templates/executor.yaml).
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from typing import Any

from kaval_agent.removal import RULE_MODEL, STEP_BACKUP, STEP_DELETE, STEP_SNAPSHOT
from kaval_shared import health
from kaval_shared.models import Action

# action type -> the step word the worker understands
ORDER_STEPS = {
    STEP_BACKUP: "mark_backup",
    STEP_DELETE: "delete_volume",
    STEP_SNAPSHOT: "delete_snapshot",
}
_TARGET = {
    STEP_BACKUP: re.compile(r"vol-[0-9a-f]{8,17}"),
    STEP_DELETE: re.compile(r"vol-[0-9a-f]{8,17}"),
    STEP_SNAPSHOT: re.compile(r"snap-[0-9a-f]{8,17}"),
}
KEEP_ORDERS = 5  # the newest few; the worker skips any it has already answered
KEY = "orders.json"


def enabled() -> bool:
    return os.environ.get("KAVAL_REMOVAL_ENABLED", "").lower() == "true"


def refusal(action: Action) -> str | None:
    """None if this removal step may be handed over; otherwise the reason it may not."""
    if action.type not in ORDER_STEPS:
        return f"not a removal step: {action.type!r}"
    if not enabled():
        return "removal is switched off (KAVAL_REMOVAL_ENABLED is not true)"
    proposal = action.proposal
    if proposal.model != RULE_MODEL:
        return "only a proposal made by the removal planner may order a removal"
    cause = health.cause_of_fingerprint(proposal.incident.fingerprint)
    if cause not in health.REMOVABLE_CAUSES:
        return f"the incident's cause {cause!r} is not one removal may act on"
    if (action.type == STEP_SNAPSHOT) != (cause == "snapshot_past_retention"):
        return f"the step {action.type!r} does not belong to the cause {cause!r}"
    if not _TARGET[action.type].fullmatch(action.target):
        return f"the target {action.target!r} is not the kind of AWS id this step acts on"
    return None


def add_order(document: str | None, order: dict[str, str], keep: int = KEEP_ORDERS) -> str:
    """The orders document with `order` added (once) and only the newest `keep` kept. Anything
    unreadable in the old document is dropped rather than carried forward."""
    try:
        loaded = json.loads(document or "{}")
        old = loaded.get("orders", []) if isinstance(loaded, dict) else []
    except ValueError:
        old = []
    orders = [o for o in old if isinstance(o, dict) and isinstance(o.get("id"), str)]
    if not any(o["id"] == order["id"] for o in orders):
        orders.append(order)
    return json.dumps({"orders": orders[-keep:]}, separators=(",", ":"), sort_keys=True)


def _count(document: str | None) -> int:
    try:
        loaded = json.loads(document or "{}")
        return len(loaded.get("orders", [])) if isinstance(loaded, dict) else 0
    except (ValueError, TypeError):
        return 0


def make_order(action: Action, now: datetime) -> dict[str, str]:
    return {
        "id": str(action.id),
        "step": ORDER_STEPS[action.type],
        "target": action.target,
        "at": now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def hand_over(
    action: Action, *, v1: Any, namespace: str, configmap: str, now: datetime | None = None
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Write the order into the ConfigMap. Returns (before, after, stdout) for the execution row.
    Raises if the ConfigMap cannot be read or patched: a failed execution, recorded as such."""
    order = make_order(action, now or datetime.now(UTC))
    current = v1.read_namespaced_config_map(configmap, namespace)
    before_doc = (current.data or {}).get(KEY)
    after_doc = add_order(before_doc, order)
    v1.patch_namespaced_config_map(configmap, namespace, {"data": {KEY: after_doc}})
    before = {"orders_in_configmap": _count(before_doc)}
    after = {"order_id": order["id"], "step": order["step"], "handed_over": True}
    stdout = (
        f"order {order['id']} ({order['step']} {order['target']}) handed over; the removal "
        "worker's answer arrives with the server's next copy (up to 3 hours) as a removal_done "
        "or removal_failed signal"
    )
    return before, after, stdout
