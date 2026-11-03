# Lab 74 — A sync that cannot hang

**Phase:** 8 (hardening) · **Story:** `KAV-122` · **Time:** about 20 minutes · **Cost:** $0 (nothing here touches AWS, and Parts 1 to 3 do not touch Atlassian either)

Before this lab, a stalled connection made `make docs-sync` wait for ever, and a dropped one ended it with a traceback. This lab gives every Jira and Confluence request a time limit, retries reads and never writes, and makes a failure say what failed. The decision is [ADR-0074](../adr/0074-the-atlassian-scripts-get-a-time-limit-and-only-reads-are-retried.md).

## Prerequisites

- The project's Python environment (`make sync`).
- For Part 4 only: the repo's `.env` with a Jira login (the same one `make docs-sync` uses).

## Part 1 — See it give up, with no login

A listening socket takes a connection and never answers, which is what a stalled network looks like to a client. From the repo root:

```bash
python - <<'EOF'
import socket, sys, time
sys.path.insert(0, "scripts/tracking")
import atlassian_http as ah

server = socket.socket()
server.bind(("127.0.0.1", 0))
server.listen()
base = f"http://127.0.0.1:{server.getsockname()[1]}"
started = time.monotonic()
try:
    ah.send("GET", base, "/rest/api/3/myself", {}, timeout=1, retry_wait=0.2)
except ah.AtlassianUnreachable as exc:
    print(exc)
print(f"gave up after {time.monotonic() - started:.1f} seconds")
EOF
```

Expect two "trying again" lines and then the plain one, in about four seconds:

```
GET /rest/api/3/myself: no answer (no reply within 1 seconds); trying again (2 of 3)
GET /rest/api/3/myself: no answer (no reply within 1 seconds); trying again (3 of 3)
GET /rest/api/3/myself: no answer from Atlassian (no reply within 1 seconds), tried 3 times
gave up after 3.9 seconds
```

On the code before this lab (commit `be6689a`) the same request, through `call()`, never returned: it was still waiting after 8 seconds, when the check was stopped.

## Part 2 — The tests

```bash
python -m pytest scripts/tracking/test_atlassian_http.py -q
```

34 tests when this lab was written (the file has 57 now: `KAV-123` added 23, see [Lab 75](lab-75-a-login-that-stays-home.md)), about 15 seconds then, no login and no network beyond `127.0.0.1`. The server in the test file is a real socket that can accept and stay silent, hang up, reset the connection, send half an answer, or answer properly. What they prove:

- **A time limit on every request.** `test_a_server_that_never_answers_ends_a_read_in_time_and_it_is_tried_three_times`. If the limit is ever removed, the test fails with "still waiting after 5 seconds" rather than freezing the build.
- **Reads retried, writes never.** The same silent server gives three connections for a GET and exactly one for a POST, PUT or DELETE; a GET that fails once and then works returns the answer; the pauses are 1 and 3 seconds.
- **A clear failure that never shows the login.** The message names the method and the path. A test searches the message, its representation, the whole traceback and the screen for the login and the token, and checks that the login *was* sent to the server.
- **Normal answers unchanged.** A 200 with a body, a 204 with nothing in it, a 4xx (with and without JSON), a 5xx: each comes back as `(status, body)` and is not retried.
- **The settings.** `KAVAL_ATLASSIAN_TIMEOUT` and `KAVAL_ATLASSIAN_RETRY_WAIT` are honoured, and a bad value is refused by name before anything is sent.

## Part 3 — The real scripts, and `make docs-sync`

The same file runs the real `publish-confluence.py`, `jira-sync.py` and `jira-dashboards.py`, and `make docs-sync` itself, in a throwaway copy of the repo whose `.env` points at the local server (so nothing real is touched, and the real dashboard is not rewritten):

- Against the silent server, each script exits non-zero with one plain line and no traceback, after three connections.
- `make docs-sync` stops at the Confluence step: no "done. space home" line and no "Now republish" line.
- The other half of the proof, so the harness is not simply failing for every reason: against a server that answers properly, the same `make docs-sync` ends with both lines and exit code 0.
- When Confluence answers "no" to every page update, the script still tries all the pages, then exits non-zero and lists the titles. (Before this lab it printed `UPDATE FAILED` and ended "done".)
- A separate test reads the Makefile and fails if the `docs-sync` recipe ever gets a leading `-` or a `|| true`, either of which would make a failure invisible. It does not need `make`; the two tests that run `make` are skipped where it is not installed.

## Part 4 — The normal path, against the real Atlassian (read-only)

```bash
python scripts/tracking/jira-sync.py show KAV-14
```

It lists Phase 8's stories, `KAV-122` among them. Nothing is written. This is the check that a real answer still comes back as before. The full proof is a real `make docs-sync`, which makes about two hundred calls; the first one on the merged code ran clean (see the last box below).

## When it says no answer

| What you see | What it means | What to do |
|---|---|---|
| `GET /…: no answer from Atlassian (no reply within 30 seconds), tried 3 times` | Atlassian or your connection did not answer a read, three times | Check your connection and [Atlassian's status page](https://status.atlassian.com), then run it again. A sync is safe to repeat |
| `GET /…: no answer from Atlassian (ConnectionResetError: …)`, `(gaierror: …)` | The connection dropped, or the address could not be found (a lookup failure usually points at the laptop's connection) | The same |
| `POST /…: no answer from Atlassian (…). Not repeated: it may already have gone through` | A change was sent and nothing came back | **Look first**: is the issue, the comment or the page there? Repeat only if it is not. The line names the request |
| `3 page(s) were not updated: …` | Confluence answered "no" for those pages; the `UPDATE FAILED` lines above give its reason | Fix what the reason says, then run it again |
| `KAVAL_ATLASSIAN_TIMEOUT must be a number of seconds above zero, not 'soon'` | A setting is wrong | Fix it, or unset it for the default of 30 |

On a slow connection: `KAVAL_ATLASSIAN_TIMEOUT=120 make docs-sync`.

**Seen for real.** The table was written before the first real failure. It came on the night of 2026-11-02: the refresh after the last merge stopped after 15 pages with `GET /wiki/api/v2/spaces/…/pages?title=…: no answer from Atlassian (gaierror: [Errno 11001] getaddrinfo failed), tried 3 times`, no traceback and no done line (the second row above: a lookup failure, which usually points at the laptop's connection). The run had lasted from 23:24 to 00:32, so the laptop had probably gone to sleep. What fixed it was what the table says: check the connection with a read, run it again. The rerun (2026-11-03, 21:32 to 21:37) updated 107 pages with no failure and no retry.

A redirect is the other thing a script can now stop on: [Lab 75](lab-75-a-login-that-stays-home.md).

## Done when

- [x] Every request has a time limit, proven against a server that accepts and never answers *(2026-11-02)*
- [x] A time-out or dropped connection is a plain failure naming the method and path, and never shows the login *(2026-11-02)*
- [x] Reads are retried three times, writes never *(2026-11-02)*
- [x] `make docs-sync` exits non-zero on such a failure and prints no done line, proven by the negative test *(2026-11-02)*
- [x] A 200, a 204 with an empty body and a 4xx come back as before, tested *(2026-11-02)*
- [x] A sync no longer ends "done" when Confluence refused a page *(2026-11-02)*
- [x] The first real `make docs-sync` after the merge finishes normally *(2026-11-02, 22:57 to 23:01 IST: `DONE exit=0`, 106 pages updated, 1 created, no failed page, no retry line)*

## Cost

$0. The tests run on the laptop and in CI; nothing is deployed.
