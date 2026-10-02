"""The Kubernetes config loader `kaval_collector.k8s_events` needs (KAV-48).

A small, deliberate duplicate of `kaval_executor.k8s.load_config` rather than an import of
it: the collector is upstream of everything (it only ever produces `signal` rows; nothing
downstream depends on it existing first), and importing from `kaval_executor` would point
that dependency arrow backwards for six lines of code.
"""

from __future__ import annotations

from kubernetes import config


def load_config() -> None:
    """In-cluster first — this is how the collector actually runs, as its own read-only
    ServiceAccount. Falls back to the local kubeconfig so `python -m
    kaval_collector.k8s_events --once` also works from a laptop against a reachable
    cluster, the same laptop-run affordance the rest of this codebase's tools have."""
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
