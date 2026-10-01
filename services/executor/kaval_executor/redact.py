"""Redacts command output before it is ever written to `execution.stdout` (KAV-47, ADR-0005).

The finding that made this its own module: `execution.stdout` is read by the gateway and
shown on the phone, so a secret that reaches it is a secret in a table a mobile app displays
— redacting only when copying prod data to staging (the original plan) would mean prod
itself holds secrets in a queryable column. So this runs **before** `kaval_executor.executor`
ever calls `session.add(Execution(...))`, not at backup/anonymise time; anonymisation stays a
second line of defence, not the only one.

Two kinds of pattern, both deliberately biased toward over-redacting rather than under- —
the same bias `policy/policy.rego`'s `never` keyword match documents for the same reason: a
false positive here costs a blanked-out word in an audit log; a false negative costs a real
secret sitting in Postgres.

1. Well-known credential *shapes* (AWS account ids, ARNs, access key ids, bearer tokens,
   JWTs, PEM blocks) — regexes specific enough not to eat ordinary operational text like pod
   names or UUIDs, which the audit trail needs to stay readable.
2. `kubectl get secret -o yaml`-shaped output — blanks every value under a `data:` or
   `stringData:` key in any embedded YAML document. This also blanks a ConfigMap's `data:`
   (not actually secret), which is the same loose-in-the-safer-direction trade: the full
   value is still one `kubectl` command away on the live cluster, just never in this column.
"""

from __future__ import annotations

import re

PLACEHOLDER = "<redacted>"

_ACCOUNT_ID = re.compile(r"(?<!\d)\d{12}(?!\d)")
_ARN = re.compile(r"arn:aws:[a-zA-Z0-9:_/.\-]+")
_AWS_ACCESS_KEY = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_BEARER = re.compile(r"(?i)\bbearer\s+[a-z0-9\-_.]{10,}")
# eyJ... is "{" base64-encoded: every JWT header starts with it. Three dot-separated
# base64url segments is specific enough that this never matches ordinary text.
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
_PEM_BLOCK = re.compile(r"-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----", re.DOTALL)

_SECRET_DATA_KEY = {"data:", "stringdata:"}


def _redact_secret_manifests(text: str) -> str:
    """Blank every value line inside a `data:`/`stringData:` block in-place, tracked by
    indentation rather than a full YAML parse — this only ever sees `kubectl ... -o yaml`
    output, which is always consistently indented."""
    lines = text.split("\n")
    out: list[str] = []
    block_indent: int | None = None
    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if block_indent is not None:
            if stripped and indent <= block_indent:
                block_indent = None  # dedented back out of the data/stringData block
            elif stripped and ":" in stripped:
                key = stripped.split(":", 1)[0]
                out.append(f"{' ' * indent}{key}: {PLACEHOLDER}")
                continue
            else:
                out.append(line)
                continue
        if stripped.lower() in _SECRET_DATA_KEY:
            block_indent = indent
        out.append(line)
    return "\n".join(out)


def redact(text: str | None) -> str | None:
    """`None` in, `None` out — `execution.stdout` is nullable, and a redactor that turns a
    missing value into the string `"<redacted>"` would itself be a small lie in the audit
    trail."""
    if text is None:
        return None
    out = _PEM_BLOCK.sub(PLACEHOLDER, text)
    out = _JWT.sub(PLACEHOLDER, out)
    out = _BEARER.sub(PLACEHOLDER, out)
    out = _AWS_ACCESS_KEY.sub(PLACEHOLDER, out)
    out = _ARN.sub(PLACEHOLDER, out)
    out = _ACCOUNT_ID.sub(PLACEHOLDER, out)
    out = _redact_secret_manifests(out)
    return out
