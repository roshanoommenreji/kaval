"""The server's daily AWS cost pull (KAV-92, ADR-0060): small, capped, and honest about failure.

The script is cut out of infra/modules/node/user_data.sh.tftpl and run with stand-ins for `aws` and
`kubectl`. What matters is what it refuses to do: ask AWS more than the cap allows, ask at all when a
good answer is fresh or the cluster is not ready, run the one billed call with anything but the
borrowed read-only login, or tell the cluster anything about a failure except a fixed word (AWS's
own error text carries account details and the page it would end up on is readable by every pod)."""

from __future__ import annotations

import base64
import gzip
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "infra" / "modules" / "node" / "user_data.sh.tftpl"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")

# Built from pieces: a twelve-digit number in a file reads like a real account id to the repository's
# secret checks, and this one is not.
ACCOUNT = "1111" + "2222" + "3333"
THIS_MONTH = time.strftime("%Y-%m", time.gmtime())


def _script() -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    start_marker = "cat > /usr/local/sbin/kaval-cost-pull <<'COSTSCRIPT'\n"
    start = text.index(start_marker) + len(start_marker)
    end = text.index("\nCOSTSCRIPT\n", start) + 1
    # what Terraform's templatefile makes of it: ${name_prefix} filled in, $${ turned into ${
    return text[start:end].replace("${name_prefix}", "kaval-test").replace("$${", "${")


class Run:
    def __init__(self, code: int, err: str, aws: list[str], applied: str, state: Path,
                 ce_env: list[str]) -> None:
        self.code, self.err, self.aws, self.applied, self.state, self.ce_env = (
            code, err, aws, applied, state, ce_env)

    @property
    def asked_for_the_bill(self) -> bool:
        return any(c.startswith("ce get-cost-and-usage") for c in self.aws)


def _run(tmp_path: Path, *, calls: int | None = None, last_ok_age: float | None = None,
         namespace: bool = True, assume_ok: bool = True, ce_ok: bool = True,
         identity_ok: bool = True, last_month_calls: int | None = None,
         counter_raw: str | None = None) -> Run:
    state = tmp_path / "state"
    state.mkdir()
    if calls is not None:
        (state / f"calls-{THIS_MONTH}").write_text(f"{calls}\n", encoding="utf-8")
    if counter_raw is not None:
        (state / f"calls-{THIS_MONTH}").write_text(counter_raw + "\n", encoding="utf-8")
    if last_month_calls is not None:
        (state / "calls-1999-01").write_text(f"{last_month_calls}\n", encoding="utf-8")
    if last_ok_age is not None:
        (state / "last-ok").write_text(f"{int(time.time() - last_ok_age)}\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    aws_log, ce_env, applied = tmp_path / "aws.txt", tmp_path / "ce-env.txt", tmp_path / "applied.txt"
    identity = (f"echo {ACCOUNT}" if identity_ok else
                f'echo "AccessDenied for user in account {ACCOUNT}" >&2; exit 255')
    assume = ('printf "borrowed-id\tborrowed-secret\tborrowed-token\n"' if assume_ok else
              f'echo "not authorized to perform sts:AssumeRole on arn:aws:iam::{ACCOUNT}:role/x" '
              ">&2; exit 255")
    bill = ("echo '{\"ResultsByTime\": []}'" if ce_ok else
            f'echo "User: arn:aws:sts::{ACCOUNT}:assumed-role/x is not authorized" >&2; exit 255')
    aws_stub = f"""
echo "$*" >> "{aws_log.as_posix()}"
case "$1 $2" in
  "sts get-caller-identity") {identity} ;;
  "sts assume-role") {assume} ;;
  "ce get-cost-and-usage")
    echo "id=$AWS_ACCESS_KEY_ID secret=$AWS_SECRET_ACCESS_KEY token=$AWS_SESSION_TOKEN" >> "{ce_env.as_posix()}"
    {bill} ;;
  *) exit 99 ;;
esac
"""
    namespace_cmd = "exit 0" if namespace else "exit 1"
    kubectl_stub = f"""
case "$1" in
  get) {namespace_cmd} ;;
  create) echo "$*" ;;
  apply) cat >> "{applied.as_posix()}" ;;
  *) exit 99 ;;
esac
"""
    for name, body in {"aws": aws_stub, "kubectl": kubectl_stub}.items():
        path = bin_dir / name
        path.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8", newline="\n")
        path.chmod(0o755)
    script = tmp_path / "cost-pull.sh"
    script.write_text(_script(), encoding="utf-8", newline="\n")
    result = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, check=False,
        env={"PATH": f"{bin_dir.as_posix()}:/usr/bin:/bin", "HOME": str(tmp_path),
             "KAVAL_COST_STATE": state.as_posix(),
             # the node's own login: the billed call must not be made with this
             "AWS_ACCESS_KEY_ID": "the-servers-own-id", "AWS_SECRET_ACCESS_KEY": "the-servers-own"})
    read = lambda p: p.read_text(encoding="utf-8").splitlines() if p.exists() else []  # noqa: E731
    return Run(result.returncode, result.stderr, read(aws_log),
               "\n".join(read(applied)), state, read(ce_env))


def _calls_file(run: Run) -> int:
    return int((run.state / f"calls-{THIS_MONTH}").read_text(encoding="utf-8"))


def test_a_first_pull_asks_once_as_the_borrowed_login_and_leaves_a_good_handoff(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path)
    assert (run.code, run.err) == (0, "")
    assert len([c for c in run.aws if c.startswith("ce ")]) == 1
    [ask] = [c for c in run.aws if c.startswith("ce ")]
    assert "--granularity DAILY" in ask and "--metrics UnblendedCost" in ask
    assert "Group" not in ask and "SERVICE" in ask
    assert 'RECORD_TYPE' in ask and "Credit" in ask  # usage, not usage after credits
    assert "assume-role --role-arn arn:aws:iam::" + ACCOUNT + ":role/kaval-test-cost-reader" in "\n".join(run.aws)
    assert "status=ok" in run.applied and "calls_this_month=1" in run.applied
    assert "--from-file=bill.json=" in run.applied and "reason=" not in run.applied
    assert _calls_file(run) == 1 and (run.state / "last-ok").exists()


def test_the_billed_call_uses_the_borrowed_login_and_never_the_servers_own(tmp_path: Path) -> None:
    run = _run(tmp_path)
    assert run.ce_env == ["id=borrowed-id secret=borrowed-secret token=borrowed-token"]


def test_a_fresh_good_answer_means_no_request_at_all(tmp_path: Path) -> None:
    run = _run(tmp_path, last_ok_age=3600)  # an hour ago
    assert run.code == 0 and run.aws == [] and run.applied == ""
    assert not (run.state / f"calls-{THIS_MONTH}").exists()


def test_an_answer_older_than_twenty_hours_is_asked_for_again(tmp_path: Path) -> None:
    run = _run(tmp_path, last_ok_age=21 * 3600, calls=5)
    assert run.code == 0 and run.asked_for_the_bill and _calls_file(run) == 6


def test_the_cap_stops_it_at_sixty_and_the_cluster_is_told(tmp_path: Path) -> None:
    run = _run(tmp_path, calls=60)
    assert run.code == 1 and not run.asked_for_the_bill
    assert run.aws == []  # not even the login was borrowed
    assert "status=capped" in run.applied and "reason=capped" in run.applied
    assert "calls_this_month=60" in run.applied and _calls_file(run) == 60


def test_the_sixtieth_request_is_allowed_and_the_sixty_first_is_not(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    last = _run(tmp_path / "a", calls=59)
    assert last.code == 0 and last.asked_for_the_bill and _calls_file(last) == 60
    over = _run(tmp_path / "b", calls=60)
    assert over.code == 1 and not over.asked_for_the_bill


def test_a_new_month_starts_counting_again(tmp_path: Path) -> None:
    run = _run(tmp_path, last_month_calls=60)
    assert run.code == 0 and run.asked_for_the_bill and _calls_file(run) == 1


@pytest.mark.parametrize("junk", ["", "many", "-3", "6 0"])
def test_a_damaged_counter_counts_as_zero_and_is_repaired(tmp_path: Path, junk: str) -> None:
    run = _run(tmp_path, counter_raw=junk)
    assert run.code == 0 and _calls_file(run) == 1


def test_the_cluster_not_being_ready_costs_nothing(tmp_path: Path) -> None:
    run = _run(tmp_path, namespace=False)
    assert run.code == 1 and run.aws == [] and "not there yet" in run.err
    assert not (run.state / f"calls-{THIS_MONTH}").exists()


def test_when_the_login_cannot_be_borrowed_nothing_is_asked_or_counted(tmp_path: Path) -> None:
    run = _run(tmp_path, assume_ok=False)
    assert run.code == 1 and not run.asked_for_the_bill
    assert "status=failed" in run.applied and "reason=assume_role_failed" in run.applied
    assert not (run.state / f"calls-{THIS_MONTH}").exists() and not (run.state / "last-ok").exists()


def test_when_the_account_cannot_be_found_it_is_the_same_failure(tmp_path: Path) -> None:
    run = _run(tmp_path, identity_ok=False)
    assert run.code == 1 and "reason=assume_role_failed" in run.applied


def test_a_failed_request_is_counted_and_never_marks_the_pull_good(tmp_path: Path) -> None:
    run = _run(tmp_path, ce_ok=False)
    assert run.code == 1 and run.asked_for_the_bill
    assert "status=failed" in run.applied and "reason=ce_call_failed" in run.applied
    assert "bill.json" not in run.applied
    assert _calls_file(run) == 1  # it may have been billed
    assert not (run.state / "last-ok").exists()  # so the next timer tick tries again


@pytest.mark.parametrize("scenario", [
    {"assume_ok": False}, {"ce_ok": False}, {"identity_ok": False},
])
def test_aws_error_text_never_reaches_the_cluster(tmp_path: Path, scenario: dict[str, bool]) -> None:
    run = _run(tmp_path, **scenario)
    text = run.applied + run.err
    assert ACCOUNT not in text and "arn:" not in text and "not authorized" not in text
    assert "AccessDenied" not in text


def test_the_timer_asks_every_three_hours_and_the_cap_and_rules_agree() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "OnUnitActiveSec=3h" in text
    assert "systemctl enable --now kaval-cost-pull.timer" in text
    assert "CAP=60" in text
    # the reader accepts exactly the reason words this script can write
    reader = (ROOT / "services" / "collector" / "kaval_collector" / "cost_explorer.py").read_text(
        encoding="utf-8")
    for word in ("assume_role_failed", "ce_call_failed", "capped"):
        assert f"fail {word}" in text or f"handoff capped {word}" in text
        assert f'"{word}"' in reader
    # the role name the script builds is the one Terraform creates
    role = (ROOT / "infra" / "modules" / "cost-reader" / "main.tf").read_text(encoding="utf-8")
    assert '"${var.name_prefix}-cost-reader"' in role and "$NS-cost-reader" in text


def test_the_role_can_read_costs_and_nothing_else() -> None:
    role = (ROOT / "infra" / "modules" / "cost-reader" / "main.tf").read_text(encoding="utf-8")
    # every permission named in the file, in quotes: one read, and the server's right to borrow
    assert sorted(re.findall(r'"([a-z0-9-]+:[A-Za-z*]+)"', role)) == [
        "ce:GetCostAndUsage", "sts:AssumeRole", "sts:AssumeRole"]
    reader = role.split('data "aws_iam_policy_document" "reader"')[1].split("resource ")[0]
    assert 'actions   = ["ce:GetCostAndUsage"]' in reader and 'resources = ["*"]' in reader
    assert "resources = [aws_iam_role.reader.arn]" in role  # the server may borrow this role only


def test_the_node_start_up_script_still_fits_the_ec2_limit() -> None:
    """EC2 refuses user data over 16,384 bytes once compressed and base64-encoded. The cost pull
    took about 3 KB of the room; what is left is small, so the next addition has to notice."""
    raw = TEMPLATE.read_text(encoding="utf-8").encode("utf-8")
    packed = base64.b64encode(gzip.compress(raw))
    assert len(packed) < 15_800, (
        f"{len(packed)} bytes packed: trim comments or move something out before adding more")
