"""Synthetic signals: fake incidents shaped exactly like the real ones, written as real rows.

Phase 1 has no cluster and no bill worth watching, but everything downstream (correlation,
the agent, the evals, the phone) needs signals to work on. So each scenario here emits the
burst of observations one real failure produces, in the payload shapes the real sources
use: Kubernetes Event objects, Prometheus samples, Alertmanager webhooks, Cost Explorer
rows. When the real collector arrives in Phase 3 it writes the same shapes, and nothing
downstream changes.

Two rules:
- A fake must never pass for real. Every `value` carries `synthetic: true`, the scenario
  name and a `run_id`, so a run can be found, excluded from metrics, or ignored in prod.
- Same seed, same signals. `--seed` makes a run's content reproducible (evals depend on
  that). The timestamps still follow `at` (default: now), and every run gets a fresh
  `run_id`, so two runs with one seed are still two runs.

    python -m kaval_collector.synthetic --list
    python -m kaval_collector.synthetic oom-crashloop --seed 42
    python -m kaval_collector.synthetic cost-spike --dry-run
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from kaval_shared.models import Signal
from sqlalchemy.orm import Session

# The source vocabulary the real collector will use too. Downstream code keys off these.
KUBERNETES = "kubernetes"
PROMETHEUS = "prometheus"
ALERTMANAGER = "alertmanager"
COST_EXPLORER = "cost_explorer"
AWS_EC2 = "aws_ec2"

DEMO_NAMESPACE = "kaval-demo"


@dataclass(frozen=True)
class Draft:
    """One signal before it has a timestamp: `offset` seconds relative to the run's `at`."""

    source: str
    kind: str
    target: str
    value: dict[str, object]
    offset: float


@dataclass(frozen=True)
class Scenario:
    name: str
    summary: str
    build: Callable[[random.Random], list[Draft]]


def _suffix(rng: random.Random) -> str:
    """A pod-name hash like Kubernetes generates: `checkout-7f9c4-x2klp`."""
    alphabet = "bcdfghjklmnpqrstvwxz2456789"
    return "".join(rng.choices(alphabet, k=5)) + "-" + "".join(rng.choices(alphabet, k=5))


def _event(namespace: str, pod: str, reason: str, message: str, count: int) -> dict[str, object]:
    """The fields of a core/v1 Event the agent actually reads."""
    return {
        "type": "Warning",
        "reason": reason,
        "message": message,
        "count": count,
        "involvedObject": {"kind": "Pod", "namespace": namespace, "name": pod},
    }


def _oom_crashloop(rng: random.Random) -> list[Draft]:
    pod = f"checkout-{_suffix(rng)}"
    target = f"{DEMO_NAMESPACE}/{pod}"
    limit = 256 * 1024 * 1024
    drafts = [
        Draft(PROMETHEUS, "container_memory_near_limit", target, {
            "metric": "container_memory_working_set_bytes",
            "container": "checkout",
            "value_bytes": int(limit * rng.uniform(0.95, 0.99)),
            "limit_bytes": limit,
        }, -300),
        Draft(KUBERNETES, "pod_oom_killed", target, _event(
            DEMO_NAMESPACE, pod, "OOMKilled",
            "Container checkout exceeded its memory limit (256Mi) and was killed", 1,
        ), -240),
    ]
    for i, offset in enumerate((-180, -120, -60), start=1):
        drafts.append(Draft(KUBERNETES, "pod_back_off", target, {
            **_event(DEMO_NAMESPACE, pod, "BackOff",
                     "Back-off restarting failed container checkout", i),
            "restart_count": i + 1,
        }, offset + rng.uniform(-5, 5)))
    drafts.append(Draft(ALERTMANAGER, "alert_firing", target, {
        "alertname": "KubePodCrashLooping",
        "status": "firing",
        "severity": "warning",
        "labels": {"namespace": DEMO_NAMESPACE, "pod": pod, "container": "checkout"},
    }, -30))
    return drafts


def _exec_format(rng: random.Random) -> list[Draft]:
    # The amd64-image-on-Graviton failure (CLAUDE.md constraint 2), as the cluster sees it.
    pod = f"gateway-{_suffix(rng)}"
    target = f"{DEMO_NAMESPACE}/{pod}"
    drafts = [
        Draft(KUBERNETES, "container_exited", target, {
            **_event(DEMO_NAMESPACE, pod, "Error",
                     "exec /usr/local/bin/uvicorn: exec format error", 1),
            "exit_code": 255,
            "image": "kaval/gateway:sha-synthetic",
        }, -150),
    ]
    for i, offset in enumerate((-120, -60, 0), start=1):
        drafts.append(Draft(KUBERNETES, "pod_back_off", target, {
            **_event(DEMO_NAMESPACE, pod, "BackOff",
                     "Back-off restarting failed container gateway", i),
            "restart_count": i,
        }, offset - rng.uniform(0, 5)))  # jitter only backwards: never in the future
    return drafts


def _cost_spike(rng: random.Random) -> list[Draft]:
    # Cost Explorer is daily and lags ~24 h, so a "spike" is one day's row against a week
    # of normal ones. Amounts are strings: money never goes through a float.
    service = "Amazon Elastic Compute Cloud - Compute"
    target = "ec2/ap-south-1"
    drafts = []
    for day in range(7, 1, -1):
        drafts.append(Draft(COST_EXPLORER, "daily_cost", target, {
            "service": service,
            "granularity": "DAILY",
            "amount_usd": f"{rng.uniform(0.28, 0.34):.2f}",
        }, -day * 86400))
    drafts.append(Draft(COST_EXPLORER, "daily_cost", target, {
        "service": service,
        "granularity": "DAILY",
        "amount_usd": f"{rng.uniform(1.9, 2.4):.2f}",
    }, -86400))
    return drafts


def _idle_volume(rng: random.Random) -> list[Draft]:
    volume = "vol-0" + "".join(rng.choices("0123456789abcdef", k=16))
    return [Draft(AWS_EC2, "volume_unattached", f"ebs/{volume}", {
        "volume_id": volume,
        "state": "available",
        "size_gib": 20,
        "volume_type": "gp3",
        "days_unattached": rng.randint(7, 21),
        "monthly_cost_usd": "1.82",
    }, 0)]


SCENARIOS: dict[str, Scenario] = {
    s.name: s
    for s in (
        Scenario("oom-crashloop", "a pod over its memory limit, killed and restarting",
                 _oom_crashloop),
        Scenario("exec-format", "an amd64 image on the arm64 node, crashing at start",
                 _exec_format),
        Scenario("cost-spike", "one day of EC2 spend at ~7x the week before", _cost_spike),
        Scenario("idle-volume", "an EBS volume attached to nothing, still billing",
                 _idle_volume),
    )
}


def generate(
    scenario: str, *, at: datetime | None = None, seed: int | None = None
) -> list[Signal]:
    """Build (not insert) one run of a scenario's signals, oldest first."""
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}; choose from {', '.join(SCENARIOS)}")
    rng = random.Random(seed)
    at = at or datetime.now(UTC)
    run_id = str(uuid.uuid4())  # fresh every run, even with a seed: runs stay tellable apart
    drafts = sorted(SCENARIOS[scenario].build(rng), key=lambda d: d.offset)
    return [
        Signal(
            source=d.source,
            kind=d.kind,
            target=d.target,
            value={**d.value, "synthetic": True, "scenario": scenario, "run_id": run_id},
            observed_at=at + timedelta(seconds=d.offset),
        )
        for d in drafts
    ]


def emit(session: Session, signals: Sequence[Signal]) -> int:
    """Insert one run in a single transaction: a run lands whole or not at all."""
    session.add_all(signals)
    session.commit()
    return len(signals)


def _as_json(signal: Signal) -> str:
    return json.dumps({
        "source": signal.source,
        "kind": signal.kind,
        "target": signal.target,
        "observed_at": signal.observed_at.isoformat(),
        "value": signal.value,
    })


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m kaval_collector.synthetic",
        description="Write one fake incident's worth of signal rows.",
    )
    parser.add_argument("scenario", nargs="?", choices=sorted(SCENARIOS))
    parser.add_argument("--seed", type=int, help="same seed, same signals")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the signals as JSON lines; touch no database")
    parser.add_argument("--list", action="store_true", help="list the scenarios and exit")
    args = parser.parse_args(argv)

    if args.list or args.scenario is None:
        for s in SCENARIOS.values():
            print(f"{s.name:<14} {s.summary}")
        return 0

    signals = generate(args.scenario, seed=args.seed)
    run_id = signals[0].value["run_id"]
    if args.dry_run:
        for signal in signals:
            print(_as_json(signal))
        return 0

    from kaval_shared.db import get_engine  # only a real run needs the database

    with Session(get_engine()) as session:
        count = emit(session, signals)
    print(f"{args.scenario}: wrote {count} signals, run_id {run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
