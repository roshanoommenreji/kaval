"""Classifies one proposed action as `auto` / `ask` / `never` by evaluating `policy/policy.rego`
through the `opa` CLI (KAV-42, ADR-0017).

`policy/` is the single source of truth. This module is one caller of it; the executor
(Phase 3) will be the second, evaluating the identical `.rego` files, which is the "evaluated
twice, one definition" design `policy/README.md` already commits to. Nothing here decides
policy — it only asks OPA and translates the answer.

Two things `policy/policy.rego` already encodes, worth restating because they explain why this
module has no thresholds of its own:

- **`never` is rules over the action type and blast radius, never over confidence.** A model
  proposing to delete a PVC with confidence 0.99 is still refused (ADR-0006 rule 1).
- **Nothing is born `auto`.** `policy/promotions.json` starts empty, and stays empty until a
  future ADR cites `outcome` rows and adds an entry. Until then this module can return `auto`
  for no input at all — checked directly, not just by a mocked test, in Lab 12.

If OPA itself can't be evaluated (binary missing, non-zero exit, output that doesn't parse),
`classify()` returns `PolicyClass.ask`, never `never` and never `auto`. A policy engine that
fails should produce more human questions, not fewer or a false green light — the same shape as
ADR-0006's Jev rules-only fallback, and the same default `.env.example`'s `DEFAULT_AUTONOMY`
already documents.

    python -m kaval_agent.policy classify --type restart_pod --blast-radius pod \\
        --reversible --confidence 0.95
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from kaval_shared.models import BlastRadius, PolicyClass

logger = logging.getLogger(__name__)


def _default_policy_dir() -> Path:
    """`KAVAL_POLICY_DIR`, set by the Dockerfile, wins — it has to: hatchling's wheel build
    (`pyproject.toml`'s `packages = [...]`) installs `kaval_agent` flattened into
    site-packages, not as `services/agent/kaval_agent`, so `__file__` inside a *built* image
    is nowhere near the repo root, however many `.parent`s are walked. (Found by CI, not
    guessed: `opa eval` against a directory that didn't exist, failing every action closed to
    `ask` rather than raising anything visible — see ADR-0017.)

    Without the env var — a laptop run against an editable install (`uv sync`/`pip install -e
    ".[agent]"`) — `__file__` still resolves inside the real source tree, so the four-parents
    walk (`kaval_agent/policy.py -> kaval_agent -> agent -> services -> repo root`) is correct
    there, and is kept as that case's fallback rather than a second env var to set locally.
    """
    env = os.environ.get("KAVAL_POLICY_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "policy"


DEFAULT_POLICY_DIR = _default_policy_dir()
DEFAULT_OPA_BIN = "opa"
DEFAULT_TIMEOUT = 5.0


class PolicyEvaluationError(RuntimeError):
    """`opa eval` failed to run or returned something that doesn't parse. Never raised out of
    `classify()` — caught there and turned into the `ask` fallback. Public so a caller that
    wants to know *why* a fallback happened (rather than just that it did) can catch it around
    a direct `_run_opa` call, and so tests can assert on it precisely."""


def _run_opa(
    input_doc: dict[str, object], *, opa_bin: str, policy_dir: Path, timeout: float,
) -> str:
    """Run `opa eval` over `input_doc`, return the raw decision string. Talks to a real `opa`
    binary via subprocess — there is no Python OPA client, and a subprocess call is what Lab 00
    already has developers install `opa` for."""
    try:
        proc = subprocess.run(
            [opa_bin, "eval", "--format=json", "-I", "-d", str(policy_dir), "data.policy.decision"],
            input=json.dumps(input_doc), capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PolicyEvaluationError(f"could not run {opa_bin!r}: {exc}") from exc
    if proc.returncode != 0:
        raise PolicyEvaluationError(
            f"opa eval exited {proc.returncode} (policy_dir={policy_dir}): "
            f"stderr={proc.stderr.strip()!r} stdout={proc.stdout.strip()!r}"
        )
    try:
        body = json.loads(proc.stdout)
        return str(body["result"][0]["expressions"][0]["value"])
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise PolicyEvaluationError(
            f"opa eval returned an unexpected shape: {exc} ({proc.stdout[:200]!r})"
        ) from exc


def classify(
    action_type: str, blast_radius: BlastRadius, reversible: bool, confidence: float, *,
    opa_bin: str = DEFAULT_OPA_BIN, policy_dir: Path | str = DEFAULT_POLICY_DIR,
    timeout: float = DEFAULT_TIMEOUT,
) -> PolicyClass:
    """Ask `policy/policy.rego` what class this action belongs to. Pure with respect to the
    database — the caller (`diagnose.write_proposal`) is the one writing rows."""
    input_doc = {
        "action": {
            "type": action_type, "blast_radius": blast_radius.value, "reversible": reversible,
        },
        "confidence": confidence,
    }
    try:
        decision = _run_opa(
            input_doc, opa_bin=opa_bin, policy_dir=Path(policy_dir), timeout=timeout,
        )
    except PolicyEvaluationError as exc:
        logger.warning("policy evaluation failed (%s); classifying %r as ask", exc, action_type)
        return PolicyClass.ask
    try:
        return PolicyClass(decision)
    except ValueError:
        logger.warning("opa returned an unrecognised decision %r; classifying as ask", decision)
        return PolicyClass.ask


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m kaval_agent.policy",
        description="Classify one action against policy/policy.rego, without touching the "
                     "database. Useful to check a hypothetical action, or that `opa` is on PATH.",
    )
    parser.add_argument("--type", required=True, dest="action_type")
    parser.add_argument("--blast-radius", required=True, choices=[b.value for b in BlastRadius])
    parser.add_argument("--reversible", action="store_true")
    parser.add_argument("--confidence", required=True, type=float)
    args = parser.parse_args(argv)

    result = classify(
        args.action_type, BlastRadius(args.blast_radius), args.reversible, args.confidence,
    )
    print(result.value)
    return 0


if __name__ == "__main__":
    sys.exit(main())
