"""The login on the approval route (KAV-115, ADR-0073, threat T-APPROVE-1).

None of these need a database: a refused call must be refused *before* the database is opened,
so the session dependencies are replaced with ones that fail the test if anything asks for them.
A call that gets past the login is shown by that failure being raised.
"""

from __future__ import annotations

import hmac
import logging
import uuid
from collections.abc import Iterator
from typing import NoReturn

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from kaval_gateway import api, approver_auth
from kaval_gateway.main import app
from prometheus_client import REGISTRY

TOKEN = "k3J" + "x" * 40  # 43 characters, the length of 32 random bytes in url-safe base64
BODY = {"verdict": "approved", "actor": "roshan"}


class ReachedTheDatabase(Exception):
    """Raised by the stand-in session: the call got past the login."""


def _no_session() -> NoReturn:
    raise ReachedTheDatabase


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    app.dependency_overrides[api.read_session] = _no_session
    app.dependency_overrides[api.write_session] = _no_session
    monkeypatch.setenv("APPROVER_TOKEN", TOKEN)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _post(client: TestClient, headers: dict[str, str] | None = None,
          body: object = BODY) -> object:
    return client.post(f"/v1/actions/{uuid.uuid4()}/decisions", json=body, headers=headers)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _refused(why: str) -> float:
    return REGISTRY.get_sample_value("kaval_approval_refused_total", {"outcome": why}) or 0.0


# --- closed unless configured ---------------------------------------------------------------

SHORT = "x" * (approver_auth.MIN_TOKEN_LENGTH - 1)


@pytest.mark.parametrize("value", [None, "", "   ", "short", SHORT])
def test_with_no_usable_token_configured_the_route_is_shut(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, value: str | None,
) -> None:
    if value is None:
        monkeypatch.delenv("APPROVER_TOKEN")
    else:
        monkeypatch.setenv("APPROVER_TOKEN", value)
    # Even a caller who "knows" the value is refused: a short or missing setting is a mistake,
    # and a mistake must not open the door.
    for headers in (None, _bearer(value or "")):
        resp = _post(client, headers)
        assert resp.status_code == 503  # type: ignore[attr-defined]


def test_a_shut_route_counts_the_refusal(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("APPROVER_TOKEN")
    before = _refused("switched_off")
    _post(client)
    assert _refused("switched_off") == before + 1


# --- who gets in ----------------------------------------------------------------------------

def test_the_right_token_gets_past_the_login(client: TestClient) -> None:
    with pytest.raises(ReachedTheDatabase):
        _post(client, _bearer(TOKEN))


def test_the_scheme_word_is_not_case_sensitive(client: TestClient) -> None:
    with pytest.raises(ReachedTheDatabase):
        _post(client, {"Authorization": f"bearer {TOKEN}"})


def test_no_header_is_a_401_that_asks_for_a_bearer_token(client: TestClient) -> None:
    before = _refused("missing")
    resp = _post(client)
    assert resp.status_code == 401  # type: ignore[attr-defined]
    assert resp.headers["www-authenticate"] == "Bearer"  # type: ignore[attr-defined]
    assert _refused("missing") == before + 1


@pytest.mark.parametrize("header", [
    TOKEN,                          # the token with no scheme
    f"Basic {TOKEN}",               # the wrong scheme
    "Bearer",                       # no token
    "Bearer    ",                   # blank token
    "",
])
def test_a_malformed_header_is_refused_as_missing(client: TestClient, header: str) -> None:
    assert _post(client, {"Authorization": header}).status_code == 401  # type: ignore[attr-defined]


@pytest.mark.parametrize("wrong", [
    "a" * 43,                       # right length, wrong
    TOKEN[:-1],                     # one short
    TOKEN + "x",                    # one long
    TOKEN[:20],                     # a correct beginning
    TOKEN.swapcase(),               # same letters, different case
    "x" * 500,                      # absurdly long
    "'; drop table decision; --",
    "null",
])
def test_a_wrong_token_is_a_401(client: TestClient, wrong: str) -> None:
    before = _refused("wrong")
    resp = _post(client, _bearer(wrong))
    assert resp.status_code == 401  # type: ignore[attr-defined]
    assert _refused("wrong") == before + 1


def test_the_token_is_compared_in_constant_time(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        seen.append((a, b))
        return bool(real(a, b))

    monkeypatch.setattr(hmac, "compare_digest", spy)
    _post(client, _bearer("wrong-but-plausible-" + "y" * 24))
    assert len(seen) == 1


# --- before anything else is read -----------------------------------------------------------

def test_a_refused_call_never_opens_the_database(client: TestClient) -> None:
    # `_no_session` raises if asked for; getting a plain 401 back proves it was not.
    assert _post(client, _bearer("wrong" * 10)).status_code == 401  # type: ignore[attr-defined]


def test_the_login_is_checked_before_the_body(client: TestClient) -> None:
    resp = _post(client, None, body={"verdict": "auto_approved", "actor": ""})
    assert resp.status_code == 401  # type: ignore[attr-defined]  # not a 422 that explains the shape


# --- the token is never written down --------------------------------------------------------

def test_neither_the_log_nor_the_reply_carries_the_token(
    client: TestClient, caplog: pytest.LogCaptureFixture,
) -> None:
    attempt = "leaky-" + "z" * 40
    with caplog.at_level(logging.DEBUG):
        resp = _post(client, _bearer(attempt))
        with pytest.raises(ReachedTheDatabase):
            _post(client, _bearer(TOKEN))
    for text in (resp.text, caplog.text, str(resp.headers)):  # type: ignore[attr-defined]
        assert attempt not in text and TOKEN not in text


def test_the_refusal_names_the_caller_and_the_reason_only(
    client: TestClient, caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="kaval_gateway.approver_auth"):
        _post(client, _bearer("wrong" * 10))
    assert [r.getMessage().split(" from ")[0] for r in caplog.records] \
        == ["approval call refused (wrong)"]


def test_the_counter_only_ever_carries_one_of_three_fixed_words(client: TestClient) -> None:
    _post(client, _bearer("wrong" * 10))
    _post(client)
    words = {
        s.labels["outcome"] for m in REGISTRY.collect() if m.name == "kaval_approval_refused"
        for s in m.samples if "outcome" in s.labels
    }
    assert words <= {"switched_off", "missing", "wrong"}


# --- which routes need it -------------------------------------------------------------------

def test_every_route_that_writes_requires_the_login() -> None:
    """Add a second write route without the login and this fails. The reads stay open, as the
    threat model says: they are kept off the network by the rules in the chart, not by a token.
    The login must also come before the database session in the route's dependencies."""
    writes = [r for r in api.router.routes if isinstance(r, APIRoute)
              and (r.methods or set()) - {"GET", "HEAD", "OPTIONS"}]
    assert writes, "expected at least the decisions route"
    for route in writes:
        calls = [d.call for d in route.dependant.dependencies]
        assert calls[0] is approver_auth.require_approver, route.path


def test_no_write_route_hangs_off_the_app_outside_that_router() -> None:
    """The check above looks at the `/v1` router; this makes sure nothing else is mounted."""
    outside = [r for r in app.routes if isinstance(r, APIRoute)
               and (r.methods or set()) - {"GET", "HEAD", "OPTIONS"}]
    assert outside == []
