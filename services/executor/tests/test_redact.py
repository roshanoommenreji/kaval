"""`redact()` against each pattern it claims to catch, and against the ordinary operational
text it must leave alone — a redactor that also eats pod names and UUIDs would make the
audit trail useless, which is as real a failure mode as under-redacting (KAV-47, ADR-0005)."""

from __future__ import annotations

from kaval_executor.redact import redact


def _r(text: str) -> str:
    """`redact()` is `str | None -> str | None` (None in, None out — see
    `test_none_stays_none`), but every other test here only ever passes a `str` and wants one
    back, so this narrows the type instead of repeating an `assert ... is not None` in each."""
    out = redact(text)
    assert out is not None
    return out


def test_none_stays_none() -> None:
    assert redact(None) is None


def test_ordinary_kubectl_output_is_untouched() -> None:
    line = "deleted pod kaval-demo/checkout-7f9c4d8b6-x2klp (uid abc-123, was Running)"
    assert _r(line) == line


def test_a_12_digit_account_id_is_redacted() -> None:
    out = _r("account 000000000000 reached the limit")
    assert "000000000000" not in out
    assert out == "account <redacted> reached the limit"


def test_an_11_or_13_digit_number_is_left_alone() -> None:
    # Only exactly 12 digits is an AWS account id shape; anything else is probably just a
    # number (a byte count, a port range, a restart count) and should survive.
    assert _r("12345678901 bytes") == "12345678901 bytes"
    assert _r("9876543210987 bytes") == "9876543210987 bytes"


def test_an_arn_is_redacted() -> None:
    out = _r("role arn:aws:iam::000000000000:role/kaval-executor granted")
    assert "arn:aws:iam" not in out and "000000000000" not in out


def test_an_aws_access_key_id_is_redacted() -> None:
    assert "AKIAIOSFODNN7EXAMPLE" not in _r("key AKIAIOSFODNN7EXAMPLE leaked")


def test_a_bearer_token_is_redacted() -> None:
    out = _r("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.e30.abc123def456ghi789")
    assert "eyJhbGciOiJIUzI1NiJ9" not in out


def test_a_jwt_is_redacted_even_without_the_word_bearer() -> None:
    token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhIn0.c2lnbmF0dXJl"
    assert token not in _r(f"service account token: {token}")


def test_a_pem_block_is_redacted() -> None:
    pem = "-----BEGIN PRIVATE KEY-----\nMIIBVQIBADANBgkqhkiG\n-----END PRIVATE KEY-----"
    out = _r(f"found a key:\n{pem}\nend")
    assert "MIIBVQIBADANBgkqhkiG" not in out and "found a key:" in out and "end" in out


def test_a_secret_manifests_data_values_are_redacted() -> None:
    manifest = (
        "apiVersion: v1\n"
        "kind: Secret\n"
        "metadata:\n"
        "  name: db-creds\n"
        "data:\n"
        "  password: c29tZXNlY3JldA==\n"
        "  username: a2F2YWw=\n"
        "type: Opaque\n"
    )
    out = _r(manifest)
    assert "c29tZXNlY3JldA==" not in out and "a2F2YWw=" not in out
    assert "password: <redacted>" in out and "username: <redacted>" in out
    # Structure survives — only the values under data: are touched.
    assert "kind: Secret" in out and "type: Opaque" in out


def test_stringdata_block_is_also_redacted() -> None:
    manifest = "kind: Secret\nstringData:\n  token: plain-text-secret\nmetadata:\n  name: x\n"
    out = _r(manifest)
    assert "plain-text-secret" not in out and "token: <redacted>" in out
    assert "name: x" in out  # metadata, after the block ends, is untouched


def test_a_configmaps_data_is_also_redacted_deliberately() -> None:
    # Over-redaction, on purpose: the redactor can't tell Secret from ConfigMap just by
    # seeing a data: key, and the documented bias is toward blanking too much rather than
    # too little.
    manifest = "kind: ConfigMap\ndata:\n  log_level: info\n"
    assert "log_level: <redacted>" in _r(manifest)
