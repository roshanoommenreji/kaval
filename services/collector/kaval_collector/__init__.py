"""Kaval collector: turns observations into `signal` rows. INSERT only, read-only credentials.

Phase 1 ships only the synthetic source (`kaval_collector.synthetic`). The real sources
arrive after it and write the same row shapes: Kubernetes events (`k8s_events`) and Prometheus
(`prometheus`) in Phase 3; Alertmanager and Cost Explorer are still to come. Nothing
downstream can tell a fake from a real one except the `synthetic` flag in `value`.
"""

# This component's own version (ADR-0013). Bumped by the conventional commits that touch
# services/collector/ or the shared code it ships; CI stamps it on the image as
# org.opencontainers.image.version and checks the two agree.
__version__ = "0.1.0"
