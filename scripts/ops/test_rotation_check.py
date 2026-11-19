"""Tests for the rotation checker (KAV-159). No AWS: canned answers from the servers."""

from __future__ import annotations

import re

import rotation_check as rc

BASE = {"token": 1, "db_gateway": 1, "db_admin": 1}


def answers(**kw: str) -> dict[str, str]:
    """A healthy 'before' picture, with any answer overridden."""
    obs = {
        "token_ssm_version": "1",
        "token_secret_matches_ssm": "yes",
        "token_current_http": "422",
        "token_old_http": "422",
        "token_bogus_http": "401",
    }
    for role, _ in rc.DB_ROLES:
        obs |= {
            f"db_{role}_ssm_version": "1",
            f"db_{role}_secret_matches_ssm": "yes",
            f"db_{role}_current_login": "ok",
            f"db_{role}_old_login": "ok",
        }
    obs.update(kw)
    return obs


def after_answers(**kw: str) -> dict[str, str]:
    obs = answers(token_ssm_version="2", token_old_http="401")
    for role, _ in rc.DB_ROLES:
        obs[f"db_{role}_ssm_version"] = "2"
        obs[f"db_{role}_old_login"] = "refused"
    obs.update(kw)
    return obs


def names_failed(checks: list[rc.Check]) -> list[str]:
    return [c.name for c in checks if not c.ok]


# covers: the rehearsal's three pictures


def test_a_healthy_start_passes() -> None:
    assert names_failed(rc.evaluate("before", answers(), BASE)) == []


def test_a_finished_rotation_passes() -> None:
    assert names_failed(rc.evaluate("after", after_answers(), BASE)) == []


def test_the_gap_between_apply_and_restart_is_what_we_expect() -> None:
    obs = answers(
        token_ssm_version="2",
        token_secret_matches_ssm="no",
        token_current_http="401",
        token_old_http="422",
    )
    for role, _ in rc.DB_ROLES:
        obs[f"db_{role}_ssm_version"] = "2"
        obs[f"db_{role}_old_login"] = "ok"
        obs[f"db_{role}_current_login"] = "refused"
    assert names_failed(rc.evaluate("applied", obs, BASE)) == []


# The negative cases: it must refuse to call a half-done rotation done.


def test_an_old_token_that_still_works_fails_the_check() -> None:
    bad = names_failed(rc.evaluate("after", after_answers(token_old_http="422"), BASE))
    assert bad == ["token: the OLD token is refused"]


def test_a_value_that_never_changed_fails_the_check() -> None:
    bad = names_failed(rc.evaluate("after", after_answers(token_ssm_version="1"), BASE))
    assert "token: stored value is the new one" in bad


def test_the_admin_trap_is_caught() -> None:
    """New admin value stored, but the database still has the old one: the new login is refused."""
    obs = after_answers(db_admin_current_login="refused", db_admin_old_login="ok")
    bad = names_failed(rc.evaluate("after", obs, BASE))
    assert "admin password: the database accepts the NEW one" in bad
    assert "admin password: the database refuses the OLD one" in bad


def test_a_missing_answer_is_a_failure_not_a_crash() -> None:
    assert names_failed(rc.evaluate("after", {}, BASE))
    assert names_failed(rc.evaluate("before", {}, {}))


def test_a_gateway_error_is_not_acceptance() -> None:
    assert not rc._accepted("503")
    assert not rc._accepted("000")
    assert not rc._accepted("401")
    assert rc._accepted("422")
    assert rc._accepted("404")


# Parsing


def test_parse_reads_sections() -> None:
    text = "##a\nyes\n##b\n\n401\n##end\n"
    assert rc.parse(text) == {"a": "yes", "b": "401", "end": ""}


def test_failed_logins_count_by_name_only() -> None:
    text = "##failed_logins\nkaval_gateway=3\nkaval=1\n##end\n"
    assert rc.parse_failed_logins(text) == {"kaval_gateway": 3, "kaval": 1}


# Safety: the script can never print a secret


def test_no_echo_in_the_script_prints_a_secret() -> None:
    script = rc.remote_script(save=True, finish=False)
    for line in script.splitlines():
        for part in re.split(r";|&&|\|\|", line):
            part = part.strip()
            if part.startswith("echo"):
                assert not re.search(r"\$\(|\$S|\$SEC|\$OLD", part), part
    assert "--debug" not in script
    assert "set -x" not in script


def test_the_script_is_for_staging_only() -> None:
    script = rc.remote_script(save=False, finish=False)
    assert "kaval-staging" in script
    assert "kaval-prod" not in script
    assert rc.PREFIX == "kaval-staging"


def test_finish_removes_the_kept_values() -> None:
    script = rc.remote_script(save=False, finish=True)
    assert "FINISH=1" in script and 'rm -rf "$OLD"' in script


def test_the_old_values_stay_off_disk_at_rest() -> None:
    """/run is memory-backed and the folder root-only; anywhere else would be a leak."""
    script = rc.remote_script(save=True, finish=False)
    assert "OLD=/run/kav159" in script
    assert "umask 077" in script
    assert script.count('> "$OLD/') == 3


def test_a_status_code_is_followed_by_a_line_break() -> None:
    """curl prints no newline; without one the next ## marker is glued to it (found live)."""
    script = rc.remote_script(save=False, finish=False)
    assert 'decisions"; echo; }' in script
