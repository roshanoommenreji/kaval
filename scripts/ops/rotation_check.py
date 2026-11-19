"""Prove a secret rotation on staging worked, without ever showing a secret (KAV-159, ADR-0100).

Rotating a secret has three moving parts that must agree: Parameter Store (where the new value is
written), the cluster's Secret (a copy the pods read) and the thing that checks it (the gateway for
the approver token, the database for a password). This asks all three, from the node and the
database server over Session Manager, and prints only words and numbers: `yes`, `no`, `ok`,
`refused`, an HTTP status, a version number. No secret value ever leaves those servers, so none
reaches this laptop's output, a log or a chat.

    python scripts/ops/rotation_check.py before     # first: also keeps the OLD values on the node
    python scripts/ops/rotation_check.py applied    # after Terraform's replace, node not re-run
    python scripts/ops/rotation_check.py after      # after the node re-ran, gateway restarted
    python scripts/ops/rotation_check.py finish     # forget the kept old values (always do this)

The old values are kept in `/run/kav159` on the node (memory only, root only, gone at a reboot) and
exist for one reason: after the change, the old token and the old password must be REFUSED, and the
only way to prove that is to try them. `finish` deletes them.

Read-only against everything except that one folder. Staging only; it refuses any other prefix.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "release"))

import staging_smoke as base  # noqa: E402  (shares the Session Manager helpers)

PREFIX = "kaval-staging"
NAMESPACE = "kaval-staging"
REGION = "ap-south-1"
BASELINE = ROOT / ".build" / "rotation-baseline.json"
PHASES = ("before", "applied", "after", "finish")

# The two database logins rehearsed: one the services use all day, and the owner's own login
# (the one that is only ever read when the database is first created, which is the trap).
DB_ROLES = (("gateway", "kaval_gateway"), ("admin", "kaval"))

# On the node. `%` is doubled for Python's formatting. The token is only ever compared or sent in a
# header from inside this script; every `echo` prints a word, a number or a version.
REMOTE = r"""
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
K=/usr/local/bin/kubectl; NS=%(ns)s; P=%(prefix)s; R=%(region)s; SAVE=%(save)s; FINISH=%(finish)s
OLD=/run/kav159
if [ "$FINISH" = 1 ]; then rm -rf "$OLD"; echo '##finished'; echo yes; echo '##end'; exit 0; fi
getp() { aws ssm get-parameter --region $R --with-decryption --name "$1" --query Parameter.Value --output text 2>/dev/null | tr -d '[:space:]'; }
ver()  { aws ssm get-parameter --region $R --name "$1" --query Parameter.Version --output text 2>/dev/null; }
gw=$($K get svc -n $NS -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.clusterIP}:{.spec.ports[0].port}{"\n"}{end}' | awk '$1 ~ /-gateway$/ {print $2; exit}')
DBH=$(aws ec2 describe-instances --region $R --filters "Name=tag:Role,Values=database" "Name=tag:Name,Values=$P-database" "Name=instance-state-name,Values=running" --query "Reservations[0].Instances[0].PrivateIpAddress" --output text)
umask 077; mkdir -p "$OLD"
if [ "$SAVE" = 1 ]; then
  getp /kaval/$P/approver/token > "$OLD/token"
  getp /kaval/$P/db/gateway-password > "$OLD/db_gateway"
  getp /kaval/$P/db/admin-password > "$OLD/db_admin"
fi
# An empty body is refused with 422 AFTER the login is checked (a 401 comes first), so this can
# never change anything: it only tells us whether the token was believed.
code() { curl -s -m 10 -o /dev/null -w '%%{http_code}' -X POST -H "Authorization: Bearer $1" -H 'Content-Type: application/json' -d '{}' "http://$gw/v1/actions/00000000-0000-0000-0000-00000000000a/decisions"; echo; }
echo '##token_ssm_version'; ver /kaval/$P/approver/token
S=$($K get secret kaval-approver -n $NS -o jsonpath='{.data.APPROVER_TOKEN}' | base64 -d)
echo '##token_secret_matches_ssm'; [ -n "$S" ] && [ "$S" = "$(getp /kaval/$P/approver/token)" ] && echo yes || echo no
echo '##token_current_http'; code "$(getp /kaval/$P/approver/token)"
echo '##token_old_http'; if [ -s "$OLD/token" ]; then code "$(cat $OLD/token)"; else echo none; fi
echo '##token_bogus_http'; code "bogusbogusbogusbogusbogusbogusbogusbogus"
%(roles)s
echo '##end'
"""  # noqa: E501

ROLE_BLOCK = r"""
echo '##db_%(role)s_ssm_version'; ver /kaval/$P/db/%(role)s-password
SEC=$($K get secret kaval-postgres-%(role)s -n $NS -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)
echo '##db_%(role)s_secret_matches_ssm'; [ -n "$SEC" ] && [ "$SEC" = "$(getp /kaval/$P/db/%(role)s-password)" ] && echo yes || echo no
login() { PGPASSWORD="$1" PGCONNECT_TIMEOUT=8 psql "host=$DBH port=5432 dbname=kaval user=%(user)s sslmode=require" -tAc 'select 1' 2>/dev/null | grep -qx 1 && echo ok || echo refused; }
echo '##db_%(role)s_current_login'; login "$(getp /kaval/$P/db/%(role)s-password)"
echo '##db_%(role)s_old_login'; if [ -s "$OLD/db_%(role)s" ]; then login "$(cat $OLD/db_%(role)s)"; else echo none; fi
"""  # noqa: E501

# On the database server: who was refused a password, by login name only (never a value).
DB_LOG = r"""
echo '##failed_logins'
docker logs postgres --since %(minutes)sm 2>&1 | grep 'password authentication failed' \
  | sed -E 's/.*for user "([^"]*)".*/\1/' | sort | uniq -c | awk '{print $2"="$1}'
echo '##end'
"""


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def remote_script(*, save: bool, finish: bool) -> str:
    roles = "".join(ROLE_BLOCK % {"role": r, "user": u} for r, u in DB_ROLES)
    return REMOTE % {
        "ns": NAMESPACE,
        "prefix": PREFIX,
        "region": REGION,
        "save": "1" if save else "0",
        "finish": "1" if finish else "0",
        "roles": roles.replace("%%", "%"),
    }


def parse(text: str) -> dict[str, str]:
    """`##name` then its first non-empty line. Never raises: odd output becomes a failed check."""
    out: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("##"):
            current = line[2:].strip()
            out.setdefault(current, "")
        elif current and line and not out[current]:
            out[current] = line
    return out


def parse_failed_logins(text: str) -> dict[str, int]:
    found: dict[str, int] = {}
    in_section = False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("##"):
            in_section = line == "##failed_logins"
        elif in_section and "=" in line:
            user, _, n = line.partition("=")
            if n.isdigit():
                found[user] = int(n)
    return found


def _version(obs: dict[str, str], key: str) -> int | None:
    v = obs.get(key, "")
    return int(v) if v.isdigit() else None


def _accepted(code: str) -> bool:
    """A believed token is answered 422 (empty body) or 404; a refused one 401 or 503."""
    return code.isdigit() and code not in ("401", "503", "000") and int(code) < 500


def evaluate(phase: str, obs: dict[str, str], baseline: dict[str, int]) -> list[Check]:
    """Judge what the servers said against what this phase of the rotation should look like."""
    checks: list[Check] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append(Check(name, ok, detail))

    def want(name: str, key: str, expected: str) -> None:
        got = obs.get(key, "(no answer)")
        add(name, got == expected, f"{got} (expected {expected})")

    def bumped(name: str, key: str, base_key: str, expect_bumped: bool) -> None:
        now, then = _version(obs, key), baseline.get(base_key)
        if now is None or then is None:
            add(name, False, f"version unreadable (now {now}, before {then})")
            return
        add(name, (now > then) == expect_bumped, f"version {then} -> {now}")

    if phase == "before":
        want("token: the cluster's copy is the stored one", "token_secret_matches_ssm", "yes")
        add(
            "token: accepted by the gateway",
            _accepted(obs.get("token_current_http", "")),
            obs.get("token_current_http", "(no answer)"),
        )
        want("token: a made-up token is refused", "token_bogus_http", "401")
        for role, _ in DB_ROLES:
            want(
                f"{role} password: the cluster's copy is the stored one",
                f"db_{role}_secret_matches_ssm",
                "yes",
            )
            want(f"{role} password: the database accepts it", f"db_{role}_current_login", "ok")
        return checks

    if phase == "applied":
        # Terraform has written new values; nothing has copied them or told the services yet.
        bumped("token: a new value is stored", "token_ssm_version", "token", True)
        want("token: the cluster still holds the old copy", "token_secret_matches_ssm", "no")
        add(
            "token: the running gateway still believes the OLD token",
            _accepted(obs.get("token_old_http", "")),
            obs.get("token_old_http", "(no answer)"),
        )
        want("token: the NEW token is not believed yet", "token_current_http", "401")
        for role, _ in DB_ROLES:
            bumped(
                f"{role} password: a new value is stored",
                f"db_{role}_ssm_version",
                f"db_{role}",
                True,
            )
            want(
                f"{role} password: the database still takes the OLD one",
                f"db_{role}_old_login",
                "ok",
            )
            want(
                f"{role} password: the database does NOT yet take the new one",
                f"db_{role}_current_login",
                "refused",
            )
        return checks

    # after: everything has caught up
    bumped("token: stored value is the new one", "token_ssm_version", "token", True)
    want("token: the cluster's copy is the new one", "token_secret_matches_ssm", "yes")
    add(
        "token: the NEW token is accepted",
        _accepted(obs.get("token_current_http", "")),
        obs.get("token_current_http", "(no answer)"),
    )
    want("token: the OLD token is refused", "token_old_http", "401")
    want("token: a made-up token is refused", "token_bogus_http", "401")
    for role, _ in DB_ROLES:
        bumped(
            f"{role} password: stored value is the new one",
            f"db_{role}_ssm_version",
            f"db_{role}",
            True,
        )
        want(
            f"{role} password: the cluster's copy is the new one",
            f"db_{role}_secret_matches_ssm",
            "yes",
        )
        want(f"{role} password: the database accepts the NEW one", f"db_{role}_current_login", "ok")
        want(
            f"{role} password: the database refuses the OLD one", f"db_{role}_old_login", "refused"
        )
    return checks


def versions_from(obs: dict[str, str]) -> dict[str, int]:
    out: dict[str, int] = {}
    token = _version(obs, "token_ssm_version")
    if token is not None:
        out["token"] = token
    for role, _ in DB_ROLES:
        v = _version(obs, f"db_{role}_ssm_version")
        if v is not None:
            out[f"db_{role}"] = v
    return out


def database_id() -> str:
    return str(base.node_id(f"{PREFIX}-database"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("phase", choices=PHASES)
    ap.add_argument("--minutes", type=int, default=120, help="how far back to count failed logins")
    args = ap.parse_args(argv)

    node = base.node_id(PREFIX)
    if args.phase == "finish":
        done = parse(base.on_node(node, remote_script(save=False, finish=True)))
        print("old values forgotten:", done.get("finished", "no answer"))
        return 0 if done.get("finished") == "yes" else 1

    failed_text = base.on_node(database_id(), DB_LOG % {"minutes": args.minutes})
    obs = parse(base.on_node(node, remote_script(save=args.phase == "before", finish=False)))

    baseline: dict[str, Any] = {}
    if args.phase == "before":
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(versions_from(obs)), encoding="utf-8")
        baseline = versions_from(obs)
    elif BASELINE.exists():
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))

    checks = evaluate(args.phase, obs, baseline)
    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"{'PASS' if c.ok else 'FAIL'}  {c.name.ljust(width)}  {c.detail}")
    failed = parse_failed_logins(failed_text)
    print(
        f"info  refused logins in the database's log, last {args.minutes} min: "
        f"{failed or 'none'} (our own old-password tests add some; the services should add none)"
    )
    bad = [c for c in checks if not c.ok]
    print(f"\n{len(checks) - len(bad)} of {len(checks)} as expected for '{args.phase}'.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
