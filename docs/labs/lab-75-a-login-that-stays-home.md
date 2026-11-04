# Lab 75 — A login that stays home

**Phase:** 8 (hardening) · **Story:** `KAV-123` · **Time:** about 20 minutes · **Cost:** $0 (nothing here touches AWS, and Parts 1 to 4 do not touch Atlassian either)

Before this lab, an answer that said "go to a different address" made the Jira and Confluence scripts, and the release workflow's Jira read, repeat the request there with the Atlassian login attached. This lab makes them refuse: a redirect is never followed, and it ends the script with one plain line. The decision is [ADR-0075](../adr/0075-a-redirect-is-never-followed-so-the-atlassian-login-stays-home.md).

## Prerequisites

- The project's Python environment (`make sync`).
- For Part 5 only: the repo's `.env` with a Jira login (the same one `make docs-sync` uses).

## Part 1 — See it refuse, with no real login

Two small web servers on your own computer. One plays Atlassian and answers every request with "go to the other one"; the other records what it is sent. From the repo root:

```bash
python - <<'EOF'
import base64
import http.server
import sys
import threading

sys.path.insert(0, "scripts/tracking")
import atlassian_http as ah


def serve(handler):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}"


arrived = []


class Elsewhere(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        arrived.append((self.path, bool(self.headers.get("Authorization"))))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


elsewhere = serve(Elsewhere)


class Atlassian(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", elsewhere + self.path)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


site = serve(Atlassian)
fake = "-".join(["not", "a", "real", "login"])  # built from pieces, like any fake secret
login = {"Authorization": "Basic " + base64.b64encode(f"nobody:{fake}".encode()).decode()}
try:
    print("answer:", ah.send("GET", site, "/rest/api/3/myself", login))
except Exception as exc:
    print("stopped:", exc)
print("the other server saw:", [(p, "the login" if a else "no login") for p, a in arrived] or "nothing")
EOF
```

Expect:

```
stopped: GET /rest/api/3/myself: Atlassian answered with a redirect (HTTP 302). It was not followed, so the login stays on this site. If the site has moved, update JIRA_SITE_URL in .env.
the other server saw: nothing
```

On the code before this lab (commit `85cd66d`, the transport as merged for `KAV-122`) the same script printed:

```
answer: (200, {})
the other server saw: [('/rest/api/3/myself', 'the login')]
```

That second line is the finding of `KAV-123`: the request was repeated at the other address, with the login.

## Part 2 — The tests

```bash
python -m pytest scripts/tracking/test_atlassian_http.py -q      # 57 tests, about 25 seconds
python -m pytest scripts/release/test_promote.py -q -k jira      # 9 tests
```

No login and no network beyond `127.0.0.1`. The first file's 34 older tests are `KAV-122`'s ([Lab 74](lab-74-a-sync-that-cannot-hang.md)); 23 are new. What the new ones prove:

- **The login never goes to another address.** For each of 301, 302, 303, 307 and 308, the second server receives nothing, not even a request, and the first is asked exactly once. This test does not depend on how the call ends, so it keeps its meaning if the message ever changes.
- **A redirect to the same address is not followed either.** The first server sees exactly one request. This is the decision the ticket asked to have written down: one rule, no comparison of scheme, host and port.
- **A write is not followed and not repeated** (POST with 301, 302, 303 and 307; PUT and DELETE with 307), and the message says it may already have gone through.
- **The message never shows the login or where the answer pointed.** A test searches the message, its representation, the whole traceback and the screen for the login, the token and a stand-in for a signed link in the redirect's address.
- **Every other status is unchanged.** A 304 still comes back as `(304, …)`; a copy of the code made to refuse every 3xx fails that test, which is what it is there for.
- **`guard` ends a script with the one line**, with exit code 1.

## Part 3 — The real scripts, and why it is a stop and not a status

The same test file runs the real `publish-confluence.py`, `jira-sync.py` and `jira-dashboards.py` in a throwaway copy of the repo whose `.env` points at the local server. Against a server that answers every request with a redirect to a second server, each script exits non-zero with one plain line and no traceback, the first server has seen one `GET` and nothing else, and the second has seen nothing.

The last two words matter. Before the fix, the same test showed what the Confluence script does with a followed redirect: the second server knew nothing of the page, the script took "not a 200 with results" to mean "that page is not there", tried to create the space and ended in a traceback. A redirect handed back as an ordinary status would have sent it down the same path, which is why [ADR-0075](../adr/0075-a-redirect-is-never-followed-so-the-atlassian-login-stays-home.md) makes it a stop.

## Part 4 — The release workflow's Jira read

`promote.py verify`, which `promote.yml` runs with the real Jira login from the repository secrets, reads each story of a release with `jira_issue()`. The nine tests in `test_promote.py` point it at a local web server standing in for Jira:

- Against a redirect (all five statuses), the second server receives nothing, and the read stops with `Jira answered the read of KAV-1 with a redirect (HTTP 302). It was not followed, so the login stays on this site. …`, with nothing chained on that could carry the address.
- `verify` already turns that kind of failure into "cannot decide" and exit code 2, and a gate that cannot decide refuses: a promotion never goes ahead on a Jira it could not read safely.
- Pinned as before: a 200 returns the labels and status, a 404 returns nothing (an unknown issue), a 500 stops the read.

## Part 5 — The normal path, against the real Atlassian (read-only)

```bash
python scripts/tracking/jira-sync.py show KAV-14
python - <<'EOF'
import sys
sys.path.insert(0, "scripts/release")
import promote
print(promote.jira_issue("KAV-122"))   # {'labels': ['bug'], 'status': 'Done'}
print(promote.jira_issue("KAV-9999"))  # None
EOF
```

Nothing is written. This is the check that real answers still come back as before and that none of the calls these scripts make is answered with a redirect. On 2026-11-03 that included the issue read, the space lookup and the page lookups `publish-confluence.py` makes for every page. The full proof is a real `make docs-sync` on the merged code (see the last box below).

## When it says redirect

| What you see | What it means | What to do |
|---|---|---|
| `GET /…: Atlassian answered with a redirect (HTTP 301). It was not followed, so the login stays on this site. If the site has moved, update JIRA_SITE_URL in .env.` | The address in `.env` is no longer where the site answers (a renamed or moved site is the likeliest cause), or an endpoint moved | Open the address in a browser to see where it goes, then update `JIRA_SITE_URL` in `.env`. Do not make the script follow it: that is exactly what it refuses |
| `POST /…: Atlassian answered with a redirect … The request may already have gone through, so look before running it again.` | A change was sent and the answer was a redirect (a 303 is what a server sends after a change that landed) | **Look first**: is the issue, the comment or the page there? Repeat only if it is not |
| `cannot decide: Jira answered the read of KAV-n with a redirect (HTTP 302). …` (from `promote.py verify`, exit code 2) | The release workflow could not read Jira safely, so it refuses to promote | Update the repository secret `JIRA_SITE_URL` (and `.env` on the laptop), then run the promotion again |

## Done when

- [x] A redirect to a different address does not carry the login to it, proven with two local servers *(2026-11-03)*
- [x] A redirect to the same address is decided (not followed; one plain stop), written down and tested *(2026-11-03)*
- [x] The cases from `KAV-122` are unchanged and its 34 tests still pass: a 200, a 204 with an empty body, a 4xx, no answer *(2026-11-03)*
- [x] The release workflow's Jira read follows the same rule, tested *(2026-11-03)*
- [x] Merged with CI green, and the first real `make docs-sync` on the merged code finishes normally *(2026-11-03: merged as #311 at 22:46 IST with all 11 checks green; the first real sync on the merged code ran 22:52:53 to 22:57:38, `DONE exit=0`, 107 pages updated, 1 created, no failure, retry or redirect)*

## Cost

$0. The tests run on the laptop and in CI; nothing is deployed.
