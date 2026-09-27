# Lab 07 — Commit messages that link themselves to Jira

**Phase:** 1 · **Time:** ~30 min · **Cost:** $0 (the GitHub for Atlassian app is free; the CI step adds
seconds to a job that already runs)

A commit message is data. If every one starts with a type and carries its Jira key, three things
come for free:
- release notes group themselves by type;
- the version bump can be derived (Phase 4);
- every Jira issue shows the code that delivered it.

This lab makes the convention checked rather than hoped for, and connects Jira to GitHub so the
link actually appears. The decisions are in
[ADR-0011](../adr/0011-commit-convention-and-jira-link.md); the rules are in
[contributing](../contributing.md#commits).

## Prerequisites

- [Lab 06](lab-06-ci-pipeline.md) done: CI runs on every pull request
- Admin on the Jira site, and owner of the GitHub repository
- Python 3.11 on the `PATH` (`python3 --version` or `python --version`)

---

## Step 1 — Read the checker

`scripts/dev/check_commits.py` is about 100 lines of stdlib Python. It's deliberately stdlib-only,
because the hook must work on a fresh clone before `make sync` has installed anything. Its
docstring lists the five rules. The heart of it is one function:

```python
problems(message, branch, in_ci=False) -> list[str]   # empty list = the message passes
```

Both callers use that function. That's why the hook and CI can't drift apart: there's only one
copy of the rules.

- `--file` mode is for the `commit-msg` hook. Git hands it the path of the message you just typed.
- `--range base..head` mode is for CI. It reads every commit a pull request adds.

The tests are in `scripts/dev/test_check_commits.py`: 21 cases, good messages and bad ones.

```bash
make test        # now runs services/ and scripts/dev/
```

## Step 2 — Install the hooks

```bash
./scripts/dev/install-hooks.sh
```

```
installed: .git/hooks/pre-commit
installed: .git/hooks/commit-msg
```

Hooks live in `.git/hooks/`, which isn't part of the repository. That's why every clone has to
run this once, and why CI checks the same rules again.

## Step 3 — Watch it refuse a bad message

On a story branch, with something staged:

```bash
git checkout -b feat/KAV-99-try-it
echo "# scratch" > scratch.md && git add scratch.md
git commit -m "added a file"
```

```
  this commit: the subject must start with one of feat, fix, docs, infra, chore, then ': ' ...
  this commit: branch 'feat/KAV-99-try-it' is a story branch, so the subject needs its KAV key; ...
```

The commit doesn't exist: `git log -1` still shows the previous one. Now try a smart-commit
command:

```bash
git commit -m "feat: KAV-99 scratch file #done"
```

That's refused too, with the reason: Jira would ignore the command (Step 5). Finally, a good
message:

```bash
git commit -m "feat: KAV-99 scratch file"
```

Clean up: `git checkout main && git branch -D feat/KAV-99-try-it && rm scratch.md`.

## Step 4 — Connect Jira to GitHub

The app is **GitHub for Atlassian**, by Atlassian (it used to be called "GitHub for Jira"). The
marketplace also lists third-party look-alikes, some of them paid, so check the publisher.

1. In Jira: **Apps → Explore more apps**. Search `GitHub`, pick **GitHub for Atlassian**
   (by Atlassian, Free), then **Get app**. Jira says "Your app is ready for use".
2. That only installs the Jira half. The GitHub half is on the app's setup page, not on the
   admin page that "manage your app" leads to:
   `https://<your-site>.atlassian.net/plugins/servlet/ac/com.github.integration.production/github-post-install-page`
3. **Select an organization in GitHub** opens a pop-up window. If Chrome blocks it, click the
   blocked-pop-up icon at the right end of the address bar, choose **Always allow pop-ups from**
   your Atlassian site, and click the button again.
4. On GitHub's **Install Atlassian** screen, change **All repositories** (the default) to
   **Only select repositories**, pick `kaval`, then **Install**. Least privilege applies here too:
   the app asks for read and write on code, issues and pull requests (see ADR-0011's
   consequences), so give it one repository, not every one you own.
5. Jira shows "`<account>` is now connected!", with "Only select repos connected". A **backfill**
   of existing history starts; CI builds appear first, then commits and pull requests.

## Step 5 — See the link, and why there are no `#` commands

Open any issue whose key appears in commits, e.g. `KAV-24`. The **Development** panel on the right
now lists its commits, the branch and the pull request (with its CI result).

To check the same thing from a terminal, `jira-sync.py`'s shared helper can read the panel's
summary:

```bash
python - <<'EOF'
import sys; sys.path.insert(0, "scripts/tracking")
from atlassian import call
_, issue = call("GET", "/rest/api/3/issue/KAV-24?fields=summary")
_, dev = call("GET", f"/rest/dev-status/latest/issue/summary?issueId={issue['id']}")
for kind in ("repository", "branch", "pullrequest"):
    print(kind, dev["summary"][kind]["overall"]["count"])
EOF
```

Before this step, all three were 0. Afterwards they're not.

Why no `KAV-24 #comment ...` or `#done`: Jira runs a smart-commit command only when the commit's
**author email** exactly matches a Jira user's email. Compare the two:

```bash
git config user.email            # the GitHub noreply address
```

and your Jira profile's email. They differ on purpose. The repo goes public at v1, and every
commit carries its author email permanently. So the commands would be silently dropped, and the
checker refuses them rather than let a commit *look* as if it moved an issue. Status moves with
`python scripts/tracking/jira-sync.py transition KAV-<n> <status>`.

## Step 6 — CI checks it too

Open a pull request. The **lint (ruff + mypy + commit messages)** job has a step called
**commit messages**:

```
3 commit(s) follow the convention
```

It checks `base..head`, i.e. only the commits the PR adds. History before the convention (one
`style: terraform fmt` commit from Phase 0) isn't re-litigated.

---

## Done when

- [ ] `make test` passes, including `scripts/dev/test_check_commits.py`
- [ ] `.git/hooks/commit-msg` exists, and `git commit -m "added a file"` on a story branch is refused
- [ ] A `#done` smart-commit command is refused, with the reason
- [ ] Jira's Development panel on `KAV-24` shows its commits, branch and pull request
- [ ] The **commit messages** step is green on a pull request

## What went wrong, and why (2026-09-27)

- **The app had been renamed.** Searching "GitHub for Jira" lists "GitHub for Atlassian" (the
  real one) among paid look-alikes. Installing it in Jira then connects nothing until the GitHub
  half is done, and Chrome blocked that pop-up the first time.
- **Its permissions were wider than expected.** The install screen asks for write access to code,
  which linking doesn't need. There's no narrower option, so the scope is limited by giving it one
  repository instead, and the ADR says so rather than calling it read-only.

- **The plan assumed smart commits would just work.** Reading the vendor's docs, rather than
  remembering them, turned up the email-match requirement before anything was built on it. The
  plan's "one commit updates code, Jira and release notes" became "the key links everything; a
  script moves status". That's less magic, and it actually works.
- **The hook and CI could have been two implementations.** A shell regex in the hook and a
  different one in the workflow would drift within a month. One Python function, called by both,
  can't drift.
