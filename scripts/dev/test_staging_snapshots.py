"""`make staging-down` deletes every backup copy that belongs to staging, no other (KAV-127).

The nightly stop makes its backup copy of the database disk under one name for every environment
(`kaval-hard-stop-pre-stop`), so a teardown that looked for copies by NAME left them behind: three
10 GB copies stayed in the account until they were deleted by hand. What every copy does carry is
its LABELS (Project and Env, copied from the disk or set by whatever made it). These tests run the
real `staging.sh down` against a pretend AWS that holds both environments' copies and applies
filters the way AWS does, then check which copies are gone.

The stand-ins can never reach the real account: they come first on the PATH, and the AWS settings
are pointed at nothing, so even a mistake in this file would fail rather than delete.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ops" / "staging.sh"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")

Snap = dict[str, object]

# A pretend `aws`. It knows only the calls `staging.sh down` makes, applies `--filters` the way AWS
# does (every filter must match; any one of a filter's values may; `*` is a wildcard; a copy
# without the tag never matches), and refuses anything else, which the tests then see as UNEXPECTED.
FAKE_AWS = r'''
import fnmatch, json, os, sys

STORE, LOG = os.environ["FAKE_STORE"], os.environ["FAKE_LOG"]
args = sys.argv[1:]
with open(LOG, "a", encoding="utf-8") as fh:
    fh.write("aws " + " ".join(args) + "\n")


def fail(code, message):
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write("UNEXPECTED " + message + "\n")
    print(message, file=sys.stderr)
    sys.exit(code)


def options(tokens):
    found, current = {}, None
    for token in tokens:
        if token.startswith("--"):
            current = found.setdefault(token, [])
        elif current is not None:
            current.append(token)
    return found


def load():
    with open(STORE, encoding="utf-8") as fh:
        return json.load(fh)


def save(snapshots):
    with open(STORE, "w", encoding="utf-8") as fh:
        json.dump(snapshots, fh)


call, opts = tuple(args[:2]), options(args[2:])
query = (opts.get("--query") or [""])[0]
if call == ("ec2", "describe-snapshots"):
    snapshots = load()
    for flt in opts.get("--filters", []):
        name, _, values = flt.partition(",Values=")
        name = name.removeprefix("Name=")
        if not name.startswith("tag:") or not values:
            fail(254, "InvalidParameterValue: bad filter " + flt)
        snapshots = [
            s for s in snapshots
            if s["tags"].get(name[4:]) is not None
            and any(fnmatch.fnmatchcase(s["tags"][name[4:]], v) for v in values.split(","))
        ]
    if query == "Snapshots[].SnapshotId":
        if os.environ.get("FAKE_LIST_FAILS"):
            print("An error occurred (RequestExpired): the login has expired", file=sys.stderr)
            sys.exit(255)
        print("\t".join(s["id"] for s in snapshots))
    elif query == "length(Snapshots)":
        print(len(snapshots))
    else:
        fail(99, "describe-snapshots with query " + query)
elif call == ("ec2", "delete-snapshot"):
    wanted = opts["--snapshot-id"][0]
    snapshots = load()
    if wanted not in [s["id"] for s in snapshots]:
        fail(255, "InvalidSnapshot.NotFound: " + wanted)
    save([s for s in snapshots if s["id"] != wanted])
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write("DELETE " + wanted + "\n")
elif call == ("ec2", "describe-volumes") and query == "Volumes[].VolumeId":
    print("")  # no detached data volume
elif call == ("ec2", "describe-volumes") and query == "length(Volumes)":
    print(0)
elif call == ("ec2", "describe-instances") and query == "length(Reservations[].Instances[])":
    print(0)
elif call == ("ec2", "describe-vpcs") and query == "length(Vpcs)":
    print(0)
else:
    fail(99, "aws " + " ".join(args))
'''

# staging's Terraform record is empty in these tests: `down` skips the destroy and goes straight to
# the sweeps, which is also what a re-run after a half-finished teardown does.
FAKE_TERRAFORM = r"""#!/bin/bash
case "$*" in
  *"state list"*) ;;
  *) echo "UNEXPECTED terraform $*" >> "$FAKE_LOG"; exit 99 ;;
esac
"""


def snap(snap_id: str, name: str, env: str | None, **more: str) -> Snap:
    """One backup copy as AWS lists it. `env=None` is a copy with no Env label at all."""
    tags = {"Name": name, "Project": "kaval", "Role": "database-data", **more}
    if env is not None:
        tags["Env"] = env
    return {"id": snap_id, "tags": tags}


# Staging's own copies, one for each way the project makes one.
NIGHTLY_STOP = snap("snap-0a1", "kaval-hard-stop-pre-stop", "staging", ManagedBy="kaval-lambda")
DAILY_SCHEDULE = snap("snap-0a2", "kaval-staging-database-data", "staging", ManagedBy="terraform")
IDLE_STOP = snap("snap-0a3", "kaval-staging-database-pre-stop", "staging", ManagedBy="kaval-lambda")
BY_HAND = snap("snap-0a4", "kaval-staging-database-pre-stop", "staging", ManagedBy="manual")
STAGING_COPIES = {"snap-0a1", "snap-0a2", "snap-0a3", "snap-0a4"}  # the first one leaked

# What must survive a staging teardown.
PROD_NIGHTLY_STOP = snap("snap-0b1", "kaval-hard-stop-pre-stop", "prod", ManagedBy="kaval-lambda")
PROD_SCHEDULE = snap("snap-0b2", "kaval-prod-database-data", "prod", ManagedBy="terraform")
# The copy staging's schedule once made of production's disk (KAV-128): its labels were copied.
STRAY_PROD_COPY = snap("snap-0b3", "kaval-prod-database-data", "prod", ManagedBy="terraform")
PROD_BY_HAND = snap("snap-0b4", "kaval-prod-database-pre-stop", "prod", ManagedBy="manual")
UNKNOWN_ENV = snap("snap-0c1", "kaval-hard-stop-pre-stop", "unknown", ManagedBy="kaval-lambda")
SOMEONE_ELSES: Snap = {"id": "snap-0d1", "tags": {"Name": "laptop-backup"}}
SURVIVORS = {"snap-0b1", "snap-0b2", "snap-0b3", "snap-0b4", "snap-0c1", "snap-0d1"}

EVERYTHING: list[Snap] = [
    NIGHTLY_STOP, DAILY_SCHEDULE, IDLE_STOP, BY_HAND, PROD_NIGHTLY_STOP, PROD_SCHEDULE,
    STRAY_PROD_COPY, PROD_BY_HAND, UNKNOWN_ENV, SOMEONE_ELSES,
]


class Account:
    """A pretend AWS account holding `snapshots`, and the means to run `staging.sh down` on it."""

    def __init__(self, tmp_path: Path, snapshots: list[Snap]) -> None:
        self.tmp = tmp_path
        self.store = tmp_path / "snapshots.json"
        self.log = tmp_path / "calls.log"
        self.store.write_text(json.dumps(snapshots), encoding="utf-8")
        self.log.write_text("", encoding="utf-8")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake = tmp_path / "fake_aws.py"
        fake.write_text(FAKE_AWS, encoding="utf-8", newline="\n")
        python = Path(sys.executable).as_posix()
        launcher = f'#!/bin/bash\nexec "{python}" "{fake.as_posix()}" "$@"\n'
        (bin_dir / "aws").write_text(launcher, encoding="utf-8", newline="\n")
        (bin_dir / "terraform").write_text(FAKE_TERRAFORM, encoding="utf-8", newline="\n")
        for stand_in in bin_dir.iterdir():
            stand_in.chmod(0o755)
        self.bin = bin_dir

    def teardown(self, **switches: str) -> subprocess.CompletedProcess[str]:
        env = {
            k: v
            for k, v in os.environ.items()
            if k.upper() != "PATH" and not k.upper().startswith("AWS_")
        }
        env.update(switches)
        env.update({
            "PATH": f"{self.bin.as_posix()}:/usr/bin:/bin",
            "ASSUME_YES": "1",
            "FAKE_STORE": self.store.as_posix(),
            "FAKE_LOG": self.log.as_posix(),
            # Even if the stand-ins were skipped, the real AWS command would find no login.
            "AWS_PROFILE": "kaval-test-no-such-profile",
            "AWS_CONFIG_FILE": (self.tmp / "no-config").as_posix(),
            "AWS_SHARED_CREDENTIALS_FILE": (self.tmp / "no-credentials").as_posix(),
            "AWS_EC2_METADATA_DISABLED": "true",
        })
        return subprocess.run(
            ["bash", str(SCRIPT), "down"], capture_output=True, text=True,
            encoding="utf-8", check=False, env=env, timeout=120,
        )

    def remaining(self) -> set[str]:
        return {str(s["id"]) for s in json.loads(self.store.read_text(encoding="utf-8"))}

    def deleted(self) -> list[str]:
        lines = self.log.read_text(encoding="utf-8").splitlines()
        return [ln.split()[1] for ln in lines if ln.startswith("DELETE ")]

    def unexpected(self) -> list[str]:
        lines = self.log.read_text(encoding="utf-8").splitlines()
        return [ln for ln in lines if ln.startswith("UNEXPECTED")]


def test_the_script_has_no_syntax_errors() -> None:
    assert subprocess.run(["bash", "-n", str(SCRIPT)], check=False).returncode == 0


def test_every_copy_labelled_as_stagings_is_deleted_including_the_nightly_stops(
    tmp_path: Path,
) -> None:
    """The bug: the nightly stop's copy has staging's labels but not staging's name."""
    account = Account(tmp_path, EVERYTHING)
    done = account.teardown()
    assert done.returncode == 0, done.stdout + done.stderr
    assert set(account.deleted()) == STAGING_COPIES
    assert account.unexpected() == []


def test_nothing_that_is_not_stagings_is_touched(tmp_path: Path) -> None:
    """Production's copies (the nightly stop's, the schedule's, the one staging's schedule once
    made of production's disk), a copy of unknown environment and someone else's snapshot stay."""
    account = Account(tmp_path, EVERYTHING)
    assert account.teardown().returncode == 0
    assert account.remaining() == SURVIVORS


def test_a_second_teardown_finds_nothing_and_still_succeeds(tmp_path: Path) -> None:
    account = Account(tmp_path, EVERYTHING)
    assert account.teardown().returncode == 0
    first = account.deleted()
    again = account.teardown()
    assert again.returncode == 0, again.stdout + again.stderr
    assert account.deleted() == first
    assert account.remaining() == SURVIVORS


def test_a_teardown_in_an_account_with_no_staging_copies_deletes_nothing(tmp_path: Path) -> None:
    account = Account(tmp_path, [PROD_NIGHTLY_STOP, PROD_SCHEDULE, PROD_BY_HAND])
    assert account.teardown().returncode == 0
    assert account.deleted() == []
    assert account.remaining() == {"snap-0b1", "snap-0b2", "snap-0b4"}


def test_it_refuses_to_delete_a_suspicious_number_of_copies(tmp_path: Path) -> None:
    """Staging holds a handful at most. Thirty-one means the search found what it should not."""
    many = [snap(f"snap-1{n:03d}", "kaval-hard-stop-pre-stop", "staging") for n in range(31)]
    account = Account(tmp_path, [*many, PROD_SCHEDULE])
    done = account.teardown()
    assert done.returncode != 0
    assert "refusing" in (done.stdout + done.stderr).lower()
    assert account.deleted() == []
    assert len(account.remaining()) == 32


def test_a_listing_that_fails_stops_the_teardown_rather_than_reading_as_no_copies(
    tmp_path: Path,
) -> None:
    """An expired login must not read as "no copies": the closing line would say all is clean."""
    account = Account(tmp_path, EVERYTHING)
    done = account.teardown(FAKE_LIST_FAILS="1")
    assert done.returncode != 0
    assert "nothing was deleted" in (done.stdout + done.stderr)
    assert account.deleted() == []
    assert "Left tagged" not in done.stdout


def test_the_closing_line_counts_the_copies_left(tmp_path: Path) -> None:
    """Anything staging-labelled that is still there after a teardown must show on the last line."""
    account = Account(tmp_path, EVERYTHING)
    done = account.teardown()
    assert done.returncode == 0
    closing = [ln for ln in done.stdout.splitlines() if ln.startswith("Left tagged Env=staging")]
    assert closing == [
        "Left tagged Env=staging: 0 instances, 0 volumes, 0 VPCs, 0 snapshots"
    ], done.stdout
