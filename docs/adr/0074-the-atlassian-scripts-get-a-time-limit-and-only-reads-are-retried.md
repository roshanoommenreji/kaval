# ADR-0074 — The Atlassian scripts get a time limit, and only reads are retried

- **Status:** Accepted (plan shown to Roshan 2026-11-02, who said "go"). Built on `fix/KAV-122-atlassian-time-limit` and merged as #309 (2026-11-02, 22:56 IST); the first real `make docs-sync` on the merged code ran clean (22:57 to 23:01 IST, 106 pages updated, 1 created, no failure, no retry), which was its live proof
- **Date:** 2026-11-02
- **Deciders:** Roshan
- **Story:** `KAV-122` (found during the #307 refresh on 2026-11-01 and recorded in [the journal of 2026-11-02](../journal/2026-11-02.md)) · the redirect finding below is `KAV-123` · builds on [ADR-0011](0011-commit-convention-and-jira-link.md) (Jira is moved by `jira-sync.py`, so those scripts must be trustworthy)

## Context

Every Jira and Confluence script reaches Atlassian through one function, `call()` in `scripts/tracking/atlassian.py`. It had no time limit and caught only an HTTP error answer. Two things followed, both seen on 2026-11-01:

- **A stalled connection waited for ever.** A `make docs-sync` started at 20:19 was still running at 23:30. It was stopped by hand.
- **A dropped connection ended the whole run with a traceback** (`WinError 10060`), and a name-lookup failure at 00:53 the next morning ended a read-only check the same way. Which end caused these is not known; the laptop's connection is the likelier one, but it is not proven.

The first was reproduced on 2026-11-02 before any fix: a local server that accepts the connection and never answers, and the old `call()` was still waiting after 8 seconds.

Reading the callers turned up a second, quieter fault. `publish-confluence.py` printed `UPDATE FAILED` when Confluence refused a page update and carried on, so a sync could end with its "done" line and exit code 0 with a stale page behind it. That is the same kind of failure (a sync that says it worked when it did not), and `CLAUDE.md` says a stale mirror that looks current is worse than an honestly empty one.

## Decision

1. **Every wait has a time limit: 30 seconds.** Connecting, waiting for the reply and reading it are each limited. `KAVAL_ATLASSIAN_TIMEOUT=<seconds>` sets it for one run (`KAVAL_ATLASSIAN_TIMEOUT=120 make docs-sync` on a slow connection). A value that is not a number above zero is refused by name before anything is sent.
2. **A read is tried up to three times; a write is never repeated by itself.**
   - A GET changes nothing, so a repeat is always safe. The tries are spaced 1 and then 3 seconds apart (`KAVAL_ATLASSIAN_RETRY_WAIT` scales the 1), and each retry prints one line so a hiccup shows in the log instead of a silent pause.
   - A POST, PUT or DELETE that timed out may already have landed. Repeating it could make a second Jira issue or comment, or collide with the version it just wrote. Safe repetition needs the server to recognise the repeat (an idempotency key), and none of the calls these scripts make carries one.
3. **No answer is one kind of failure with one plain message.** `AtlassianUnreachable` covers timed out, refused, dropped, name not found, and a reply that broke off. Its message names the method and the path and nothing else: no header, so never the login (a test searches the message, its representation, the full traceback and the screen for the login and the token). For a write it adds that the change may already have gone through, so look before running it again.
4. **A script ends with that one line and exit code 1**, not a traceback: each runs `guard(main)`. `make docs-sync` stops at the failing step, so it prints no "done" line and not the Makefile's "Now republish" line either.
5. **An answer is an answer.** Any status the server sends, 4xx and 5xx included, is returned as `(status, body)` exactly as before, never raised and never retried. A 200, a 204 with nothing in it and a 4xx are pinned by tests. (A 429 or 503 with a `Retry-After` is a separate decision if it ever matters; today's scripts make a few hundred calls a run.) **Amended 2026-11-03 by [ADR-0075](0075-a-redirect-is-never-followed-so-the-atlassian-login-stays-home.md):** a redirect (301, 302, 303, 307, 308) is the one exception. It is never followed and it stops the script, so the login cannot be taken to another address.
6. **A sync that left a page stale no longer says "done".** `publish-confluence.py` still tries every page, so one refusal does not hide the rest, then ends with a non-zero exit and the list of titles. This goes one step past the five written acceptance criteria of `KAV-122`; it is the same failure seen from the other side.
7. **The request lives in its own module**, `scripts/tracking/atlassian_http.py`, with no `.env` and no login in it, the way `jira_adf.py` and `jira_release.py` were split out. `atlassian.py` keeps the login and `call()`, and hands the request over. That is what lets the tests run in CI, which has no credentials, against a real socket on `127.0.0.1`.

## What real companies do, and why this one

1. **A time limit on every outbound call, and retries only for what is safe to repeat, with growing pauses** (chosen): the standard in Google's SRE book and Amazon's Builders' Library on timeouts, retries and back-off, and what the `requests` documentation tells every production user to do.
2. **A mature HTTP library** (`requests` with `urllib3`'s `Retry`, or `httpx`): a few lines for the same result, and well tested. It would add a dependency to scripts that are deliberately standard-library-only (`atlassian.py` says so, and `publish-confluence.py` is "dependency-free" by design) for the sake of one function, and CI would have to install it for them.
3. **A watchdog around the whole script** (`timeout 900 make docs-sync`): what was being done by hand in the meantime. It bounds the damage, but it kills a healthy slow run and says nothing about which request stalled.
4. **Retry everything, writes included**: right only when the API recognises repeats. Here it would sometimes mean two Jira issues.

## Consequences

- A sync can no longer hang. One request now ends within three tries, a few waits of at most 30 seconds each, plus 4 seconds of pauses, and the run stops at the first request that gets no answer instead of working through every page.
- The limit is per wait, as with any HTTP client. A server that drips one byte a minute would not be caught; that is not what a stalled connection looks like, and a total-time cap can be added if one is ever seen.
- A connection so slow that a wait takes more than 30 seconds now fails. Raise the limit for that run.
- A write that gets no answer leaves the question "did it land?" with a person. The message says so and names the request. A multi-step Jira command (`uat pass`, `create`) can still stop part-way, as it could when a crash did the same; the line says which request it was.
- Rerunning a sync is safe: it updates pages in place.
- **Seen for real (2026-11-02, 23:24 IST):** the refresh after the last merge stopped after 15 pages when a name lookup failed (`gaierror: [Errno 11001] getaddrinfo failed`), after the third try. It ended with one plain line naming the request (`GET /wiki/api/v2/spaces/…/pages?title=…: no answer from Atlassian (…), tried 3 times`), no traceback, no done line and `make` Error 1. The run had lasted from 23:24 to 00:32, so the laptop had probably gone to sleep (not proven). A name-lookup failure points at the laptop's connection rather than at Atlassian, which is still not proven either. A rerun on 2026-11-03 (21:32 to 21:37) finished cleanly: 107 pages updated, no failure, no retry.
- **Noticed, then fixed (`KAV-123`, [ADR-0075](0075-a-redirect-is-never-followed-so-the-atlassian-login-stays-home.md)):** Python's `urllib` passes the `Authorization` header on if a request is redirected to a different address (checked with two local servers on 2026-11-02: the second received it). Atlassian does not redirect these calls off-site, so the risk was small, but the token has full rights on the site. It was its own one-point story, because the five criteria here are about time limits. Fixed on 2026-11-03: a redirect is never followed.

## Delivery

One pull request (#309): `atlassian_http.py` and its 34 tests (`test_atlassian_http.py`), `call()` handing over to it, `guard(main)` in `jira-sync.py`, `jira-dashboards.py` and `publish-confluence.py`, the refused-page fix, the two new files added to `make lint`'s lists (ruff and strict mypy). Nothing is deployed: these are laptop tools, so there is no staging or production step, and it costs nothing. See [Lab 74](../labs/lab-74-a-sync-that-cannot-hang.md).
