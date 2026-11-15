"""Read a rendered chart and say who holds which Kubernetes permission (KAV-154, Lab 100).

    helm template kaval deploy/charts/kaval -n kaval \
        -f deploy/environments/prod/values.yaml | python scripts/dev/rbac_scan.py

It reads the YAML on standard input and prints every workload with its service account, every
binding with its rules, and three kinds of finding: a rule with a wildcard, a rule that can read
Secrets, and a workload that mounts a service-account token although its account is bound to no
role (a token nothing needs). It reads text only: no cluster, no login. It exits 1 if it finds a
wildcard or a Secrets rule, so it can be used as a check.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

import yaml

WORKLOADS = ("Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob")


@dataclass
class Review:
    workloads: list[tuple[str, str, str, bool]] = field(default_factory=list)
    bindings: list[tuple[str, str, str, list[dict[str, Any]]]] = field(default_factory=list)
    wildcards: list[str] = field(default_factory=list)
    secret_readers: list[str] = field(default_factory=list)
    unused_tokens: list[str] = field(default_factory=list)

    @property
    def serious(self) -> bool:
        return bool(self.wildcards or self.secret_readers)


def _pod_spec(doc: dict[str, Any]) -> dict[str, Any]:
    spec = doc["spec"]
    if doc["kind"] == "CronJob":
        spec = spec["jobTemplate"]["spec"]
    return dict(spec["template"]["spec"])


def review(docs: list[dict[str, Any]]) -> Review:
    out = Review()
    roles: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    account_mounts: dict[str, bool] = {}
    bound: set[str] = set()

    for doc in docs:
        meta = doc.get("metadata", {})
        if doc.get("kind") in ("Role", "ClusterRole"):
            namespace = meta.get("namespace", "") if doc["kind"] == "Role" else ""
            roles[(doc["kind"], namespace, meta["name"])] = doc.get("rules") or []
        elif doc.get("kind") == "ServiceAccount":
            account_mounts[meta["name"]] = doc.get("automountServiceAccountToken", True)

    for doc in docs:
        meta = doc.get("metadata", {})
        if doc.get("kind") in ("RoleBinding", "ClusterRoleBinding"):
            ref = doc["roleRef"]
            namespace = meta.get("namespace", "") if ref["kind"] == "Role" else ""
            rules = roles.get((ref["kind"], namespace, ref["name"]), [])
            for subject in doc.get("subjects") or []:
                if subject["kind"] == "ServiceAccount":
                    bound.add(subject["name"])
                where = meta.get("namespace", "(whole cluster)")
                out.bindings.append((subject["name"], where, ref["name"], rules))
            for rule in rules:
                flat = [
                    str(v) for key in ("apiGroups", "resources", "verbs") for v in rule.get(key, [])
                ]
                if "*" in flat:
                    out.wildcards.append(f"{ref['name']}: {rule}")
                if "secrets" in rule.get("resources", []):
                    out.secret_readers.append(f"{ref['name']}: {rule}")

    for doc in docs:
        if doc.get("kind") not in WORKLOADS:
            continue
        pod = _pod_spec(doc)
        account = pod.get("serviceAccountName", "default")
        mounts = pod.get("automountServiceAccountToken", account_mounts.get(account, True))
        name = f"{doc['kind']}/{doc['metadata']['name']}"
        out.workloads.append((name, account, "yes" if mounts else "no", account in bound))
        if mounts and account not in bound:
            out.unused_tokens.append(
                f"{name} mounts the token of '{account}', which is bound to no role"
            )
    return out


def main() -> int:
    docs = [d for d in yaml.safe_load_all(sys.stdin.read()) if isinstance(d, dict)]
    result = review(docs)
    print("WORKLOADS  (name | service account | mounts a token | account has a role)")
    for name, account, mounts, has_role in sorted(result.workloads):
        print(f"  {name} | {account} | {mounts} | {'yes' if has_role else 'no'}")
    print("BINDINGS  (who | where | role)")
    for who, where, role, rules in sorted(result.bindings, key=lambda b: (b[0], b[1])):
        print(f"  {who} | {where} | {role}")
        for rule in rules:
            names = f" only {rule['resourceNames']}" if rule.get("resourceNames") else ""
            print(f"      {rule.get('verbs')} on {rule.get('resources')}{names}")
    for title, items in (
        ("WILDCARDS", result.wildcards),
        ("CAN READ SECRETS", result.secret_readers),
        ("TOKENS NOTHING NEEDS", result.unused_tokens),
    ):
        print(f"{title}: {'none' if not items else ''}")
        for item in items:
            print(f"  {item}")
    return 1 if result.serious else 0


if __name__ == "__main__":
    sys.exit(main())
