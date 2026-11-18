"""The AWS half of the access review: who can do what in the live account? (KAV-154)

Read-only. Run by hand with `make iam-review`. Lists every IAM role Kaval owns, whether a
permission boundary is attached, and every Allow statement that is wider than it should be (an
action ending in `*`, or a resource of `*`). It also says whether IAM Access Analyzer exists and
what it found, and what the calling user may do. It changes nothing, and it prints no account
number and no key (how many keys the user holds is the 90-day change's record, not this scan's).

A wide statement is not always a fault: some AWS actions cannot be limited to one resource (the
`Describe` calls, the registry login token). Those are listed so each one can be explained in
docs/architecture/access-review.md, not hidden.

Another project (`stock-trader`) shares the account. Its roles are only counted, never reviewed.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

REGION = "ap-south-1"
# Roles that belong to Kaval start with this; another project's do not.
KAVAL_PREFIX = "kaval-"
ACCOUNT_ID = re.compile(r"\b\d{12}\b")
# Actions AWS cannot limit to one resource. They are read-only or issue a short login; the reason is
# written next to each in the review, so the scan marks them "known" rather than hiding them.
CANNOT_BE_NARROWED = (
    "ec2:Describe",
    "autoscaling:Describe",
    "ecr:GetAuthorizationToken",
    "ce:Get",
    "cloudwatch:PutMetricData",
)


def as_list(value: Any) -> list[str]:
    """IAM lets a field be one string or a list of them."""
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


GITHUB_SIGN_IN = "oidc-provider/token.actions.githubusercontent.com"


def classify_finding(details: dict[str, Any]) -> str:
    """ "expected" or "NEW" for one external-access finding (KAV-160).

    The analyzer reports every role that GitHub's sign-in service may assume, because that service
    is outside the account. Each such role is locked to one repository and branch by its own trust
    rule, which the access review reads; that is the one kind of finding accepted as expected. The
    rest is NEW: anything public, anything shared with another AWS account, any other action.
    """
    one = details.get("externalAccessDetails", details)
    principal = one.get("principal") or {}
    federated = principal.get("Federated", "") if isinstance(principal, dict) else ""
    return (
        "expected"
        if (
            not one.get("isPublic")
            and set(as_list(one.get("action"))) == {"sts:AssumeRoleWithWebIdentity"}
            and set(principal) == {"Federated"}
            and federated.endswith(GITHUB_SIGN_IN)
        )
        else "NEW"
    )


def wide_statements(policy: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Every Allow statement that is wider than it should be.

    Returns (what, statement id, detail), where what is "action" (an action ending in `*`),
    "resource" (a resource of `*` for an action AWS *can* narrow) or "resource-known" (a resource of
    `*` for actions AWS cannot narrow). A statement with a Condition is limited by it and is not
    listed for its resource.
    """
    statements = policy.get("Statement", [])
    if isinstance(statements, dict):
        statements = [statements]
    found: list[tuple[str, str, str]] = []
    for st in statements:
        if st.get("Effect") != "Allow":
            continue
        sid = str(st.get("Sid", "-"))
        actions = as_list(st.get("Action"))
        for action in actions:
            if action == "*" or action.endswith(":*"):
                found.append(("action", sid, action))
        if "*" in as_list(st.get("Resource")):
            narrowable = [a for a in actions if not a.startswith(CANNOT_BE_NARROWED)]
            if st.get("Condition"):
                continue
            if narrowable:
                found.append(("resource", sid, ", ".join(narrowable)))
            else:
                found.append(("resource-known", sid, ", ".join(actions)))
    return found


def mask(text: str) -> str:
    """Account numbers are masked in everything this prints."""
    return ACCOUNT_ID.sub("<account>", text)


def review_role(iam: Any, name: str) -> dict[str, Any]:
    role = iam.get_role(RoleName=name)["Role"]
    attached = [
        p["PolicyName"] for p in iam.list_attached_role_policies(RoleName=name)["AttachedPolicies"]
    ]
    wide: list[tuple[str, str, str]] = []
    inline = iam.list_role_policies(RoleName=name)["PolicyNames"]
    for policy_name in inline:
        doc = iam.get_role_policy(RoleName=name, PolicyName=policy_name)["PolicyDocument"]
        wide += [(w, f"{policy_name}/{sid}", detail) for w, sid, detail in wide_statements(doc)]
    return {
        "name": name,
        "boundary": "PermissionsBoundary" in role,
        "managed": attached,
        "inline": inline,
        "wide": wide,
    }


def main() -> int:
    import boto3  # imported here so the tests need no AWS library

    session = boto3.Session(profile_name="kaval", region_name=REGION)
    iam = session.client("iam")
    names: list[str] = []
    others = 0
    for page in iam.get_paginator("list_roles").paginate():
        for role in page["Roles"]:
            if role["Path"].startswith("/aws-service-role/"):
                continue
            if role["RoleName"].startswith(KAVAL_PREFIX):
                names.append(role["RoleName"])
            else:
                others += 1
    print(f"ROLES  Kaval's: {len(names)}   another project's (counted, not reviewed): {others}")
    reviewed = [review_role(iam, n) for n in sorted(names)]
    for r in reviewed:
        print(
            f"\n{r['name']}\n  permission boundary: {'yes' if r['boundary'] else 'none'}"
            f"   managed: {r['managed'] or '-'}   inline: {r['inline'] or '-'}"
        )
        for what, sid, detail in r["wide"]:
            print(mask(f"  {what.upper():15} {sid}: {detail[:100]}"))

    print("\nACCESS ANALYZER")
    analyzers = session.client("accessanalyzer").list_analyzers()["analyzers"]
    if not analyzers:
        print("  none exists in", REGION)
    for a in analyzers:
        client = session.client("accessanalyzer")
        found: list[dict[str, Any]] = []
        for page in client.get_paginator("list_findings_v2").paginate(analyzerArn=a["arn"]):
            found += [f for f in page["findings"] if f["status"] == "ACTIVE"]
        print(f"  {a['name']} ({a['type']}, {a['status']}): {len(found)} active findings")
        for f in found:
            detail = client.get_finding_v2(analyzerArn=a["arn"], id=f["id"])["findingDetails"][0]
            name = f["resource"].split("/")[-1]
            print(mask(f"    {classify_finding(detail):8} {f['resourceType']} {name}"))

    print("\nTHE CALLING USER (name and keys are not printed)")
    sts_arn = session.client("sts").get_caller_identity()["Arn"]
    user = sts_arn.split("/")[-1]
    policies = [
        p["PolicyName"] for p in iam.list_attached_user_policies(UserName=user)["AttachedPolicies"]
    ]
    inline_user = iam.list_user_policies(UserName=user)["PolicyNames"]
    has_second_factor = bool(iam.list_mfa_devices(UserName=user)["MFADevices"])
    print(f"  attached policies: {policies}   inline: {inline_user}")
    print("  second factor on the user:", "yes" if has_second_factor else "none")
    print(
        json.dumps({"roles_without_boundary": [r["name"] for r in reviewed if not r["boundary"]]})
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
