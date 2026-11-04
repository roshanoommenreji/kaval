"""`db-roles.sql` must not print the passwords it is given (KAV-125, ADR-0077).

The script hands each service's password to the server with a `SELECT set_config(...)`, and `psql`
prints the result of a SELECT. The result was the password, so it landed in the journal and the
boot log of every server that ran the script (found on staging, 2026-11-04). `test_db_roles.py`
runs the file through the Python driver, which prints nothing, so it could never see this. This
file runs the real `psql` and reads what it prints.

Like the other database tests it needs a database (the `POSTGRES_*` settings) and, here, the
`psql` program. Without them it skips on a laptop; CI sets `KAVAL_REQUIRE_DB=1`, which turns the
skip into a failure, so the check cannot vanish from a green run.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import NoReturn

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "ops" / "db-roles.sql"

# Fake passwords, built from pieces so no secret scanner takes them for real ones.
FAKES = {
    name: "-".join(["not", "a", "real", name, "password"])
    for name in ("gateway", "agent", "executor", "collector")
}
VARIABLES = {f"{name}_password": value for name, value in FAKES.items()}
ANY_FAKE = re.compile("|".join(map(re.escape, FAKES.values())))


def _unavailable(reason: str) -> NoReturn:
    if os.environ.get("KAVAL_REQUIRE_DB") == "1":
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)


def _psql(sql: str, variables: dict[str, str]) -> tuple[int, str]:
    """Run `sql` through the real psql as the node's bootstrap does: (exit code, all it printed)."""
    psql = shutil.which("psql")
    if psql is None:
        _unavailable("the psql program is not installed here")
    try:
        host, port = os.environ["POSTGRES_HOST"], os.environ["POSTGRES_PORT"]
        db, user = os.environ["POSTGRES_DB"], os.environ["POSTGRES_USER"]
        password = os.environ["POSTGRES_PASSWORD"]
    except KeyError as exc:
        _unavailable(f"no database settings here: {exc} is not set")
    command = [psql, "-X", "-h", host, "-p", port, "-U", user, "-d", db,
               "-v", "ON_ERROR_STOP=1", "-f", "-"]
    for name, value in variables.items():
        command += ["-v", f"{name}={value}"]
    done = subprocess.run(  # noqa: S603 - fixed program, fixed arguments, test-only values
        command,
        input=sql.encode("utf-8"),  # bytes: no newline translation on Windows
        capture_output=True,
        env={**os.environ, "PGPASSWORD": password},
        timeout=60,
        check=False,
    )
    output = done.stdout.decode("utf-8", "replace") + done.stderr.decode("utf-8", "replace")
    return done.returncode, output


def _without_passwords(output: str) -> str:
    return ANY_FAKE.sub("<password>", output)


def test_the_capture_can_see_what_psql_prints() -> None:
    """The control: a SELECT's result does reach the output, so "no password found" means it."""
    code, output = _psql("SELECT 'canary-value-printed';\n", {})
    assert code == 0, output
    assert "canary-value-printed" in output


def test_running_the_script_prints_none_of_the_passwords() -> None:
    # In a transaction that is rolled back, so no role is left behind in the test database.
    sql = "BEGIN;\n" + SCRIPT.read_text(encoding="utf-8") + "\nROLLBACK;\n"
    code, output = _psql(sql, VARIABLES)
    assert code == 0, "the script itself failed, so this proves nothing:\n" + _without_passwords(
        output
    )
    leaked = [name for name, value in FAKES.items() if value in output]
    assert leaked == [], f"psql printed the password of: {', '.join(leaked)}"


def _scram_stored_key(password: str, salt: bytes, iterations: int) -> bytes:
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    return hashlib.sha256(client_key).digest()


def test_the_roles_still_get_exactly_the_passwords_they_were_given() -> None:
    """Hiding the output must not change what reaches the roles: each stored fingerprint matches."""
    roles = ["kaval_" + name for name in FAKES]
    listing = ", ".join(f"'{role}'" for role in roles)
    sql = (
        "BEGIN;\n" + SCRIPT.read_text(encoding="utf-8") + "\n"
        "\\pset tuples_only on\n\\pset format unaligned\n"
        "SELECT 'FINGERPRINT ' || rolname || ' ' || rolpassword FROM pg_authid\n"
        f"  WHERE rolname IN ({listing});\n"
        "ROLLBACK;\n"
    )
    code, output = _psql(sql, VARIABLES)
    if "permission denied" in output:
        _unavailable("this database user cannot read pg_authid")
    assert code == 0, _without_passwords(output)
    found = dict(re.findall(r"FINGERPRINT (kaval_\w+) (\S+)", output))
    assert sorted(found) == sorted(roles), f"expected a password on every role, got {sorted(found)}"
    for name, plain in FAKES.items():
        fingerprint = found["kaval_" + name]
        parsed = re.fullmatch(r"SCRAM-SHA-256\$(\d+):([^$]+)\$([^:]+):(.+)", fingerprint)
        if parsed is None:
            _unavailable("the server stores no SCRAM passwords, so the match cannot be checked")
        iterations, salt, stored = int(parsed[1]), base64.b64decode(parsed[2]), base64.b64decode(
            parsed[3]
        )
        assert _scram_stored_key(plain, salt, iterations) == stored, (
            f"kaval_{name} got a different password"
        )


def test_every_line_that_passes_a_password_hides_its_result() -> None:
    """A cheap guard that needs no database: a new password line must carry `\\gset`."""
    offenders = []
    for number, line in enumerate(SCRIPT.read_text(encoding="utf-8").splitlines(), start=1):
        if line.lstrip().startswith("--"):
            continue
        if re.search(r":'\w*password\w*'", line) and "\\gset" not in line:
            offenders.append(number)
    assert offenders == [], f"db-roles.sql lines {offenders} pass a password without `\\gset`"
