# ADR-0075 — A redirect is never followed, so the Atlassian login stays home

- **Status:** Accepted (plan shown to Roshan 2026-11-03, who said "go"). Built on `fix/KAV-123-no-login-on-redirect`; merged as #311 (2026-11-03, 22:46 IST, all 11 checks green) and proven by the first real `make docs-sync` on the merged code (22:52:53 to 22:57:38, `DONE exit=0`, 107 pages updated, 1 created, no failure, retry or redirect)
- **Date:** 2026-11-03
- **Deciders:** Roshan
- **Story:** `KAV-123` (found while building `KAV-122` and filed from [ADR-0074](0074-the-atlassian-scripts-get-a-time-limit-and-only-reads-are-retried.md), whose decision 5, "an answer is an answer", this amends) · the approval command's version of the same gap is `KAV-124`

## Context

The Atlassian login (an email and an API token, with the same rights on the site as the person) is sent by two pieces of code:

- `call()` in `scripts/tracking/atlassian.py`, through `atlassian_http.py`: the laptop's Jira, Confluence and dashboard scripts.
- `jira_issue()` in `scripts/release/promote.py`: the release workflow's check that every `uat` story in a release is signed off. It runs in GitHub Actions with the real token from the repository secrets.

Both used Python's `urllib.request.urlopen`. Its redirect handler follows a 301, 302, 303 or 307 (and a 308, on the Python 3.11 used here) and copies the request's headers to the new address, `Authorization` included. It does so whether the new address is another site or the same one over a plain connection. This was found while building `KAV-122` (checked with two local servers on 2026-11-02: the second received the login) and became the first failing tests of this story: for each of the five statuses, in both pieces of code, the second server received the login.

Atlassian does not redirect these calls, so the risk was small, but not nothing: the token has full rights on the site, a site address that has moved or is mistyped is one redirect away from a different server, and the release workflow holds the same token.

Reading the callers turned up a second problem with the obvious fix, which is to return the redirect as an ordinary status. `publish-confluence.py` takes any answer to a lookup that is not a 200 with results to mean "that page is not there", and goes on to create it. A test showed it: when the redirect was followed to a server that knew nothing of the page, the script concluded the space did not exist, tried to create it and ended in a traceback.

## Decision

1. **A redirect is never followed**, not to another address and not to the same one. The five answers that say "go there instead" (301, 302, 303, 307, 308) are refused. It is one rule, with no comparison of scheme, host and port to get wrong, so it also covers the same site over plain http, a loop, and the re-sending of a request body by a 307 or 308.
2. **It is a stop, not a status.** `send()` raises `AtlassianRedirected`. The ticket offered "followed, or returned as a status"; this is a third option, chosen because of the Confluence script above. The message names the method, the path and the status, and nothing else: not the login, and not where the answer pointed (an address can carry a secret of its own, such as a signed link). It ends with "If the site has moved, update `JIRA_SITE_URL` in `.env`".
3. **For a write, the message adds that it may already have gone through.** A 303 is what a server sends after a write that landed. A redirect is an answer, so nothing is retried.
4. **A script ends with that one line and exit code 1**, as for no answer: `guard(main)` catches both.
5. **Only those five are refused.** Every other status, other 3xx included (a 304, say), comes back as `(status, body)` as before; a test pins a 304.
6. **The release workflow's Jira read follows the same rule**, with its own copy of the short handler: the release scripts do not import from `scripts/tracking`, so they stay self-contained. A redirect raises a `RuntimeError` that `verify` already reports as "cannot decide" (exit 2), and a gate that cannot decide refuses (its docstring says so): it never says yes by default.
7. **The mechanism is urllib's own:** a subclass of its redirect handler whose `redirect_request` returns `None`, built into an opener. Nothing else about a request changed.

## What real companies do, and why this one

1. **Strip the credential when a redirect leaves the site, and follow it otherwise.** The default of `curl` (unless told `--location-trusted`), Python's `requests`, Go's `net/http` and browsers. Right for a general client that meets servers it does not know.
2. **Never follow a redirect on a call that carries a credential, and treat one as a fault to look at** (chosen). `requests` with `allow_redirects=False`, the browser's `fetch` with `redirect: "error"`. Right for a client that talks to one known site, as these scripts do.
3. **Follow only to the same origin (scheme, host and port) and refuse the rest.** Stricter than the first, but it needs the comparison, a limit on hops and rules for 307 and 308, for a case nobody has seen on these calls.
4. **Leave Python's default.** What there was. The risk was small, the token is not, and the fix is small.

These scripts talk to exactly one site whose API does not redirect, so refusing and saying so costs nothing, and removes a class of cases instead of handling each one.

## Consequences

- If Atlassian ever starts redirecting one of these calls (a renamed site is the likeliest cause), the script stops with a plain line and exit code 1 instead of working on. The fix is to update `JIRA_SITE_URL` (the repository secret of that name, for the release workflow) or, if an endpoint really moved, its path in the script. It is noticed at once, not found later in a stale mirror.
- A write that is redirected leaves "did it land?" with a person; the message says so and names the request.
- Nothing about a normal request changed. On 2026-11-03, read-only against the real site, an issue read, the space and page lookups that `publish-confluence.py` makes for every page, `jira-sync.py show` and the release workflow's Jira read all came back as before, none with a redirect.
- A promotion that cannot read Jira refuses, as it already did for an unreachable Jira.
- **Noticed here, fixed afterwards (`KAV-124`, 2026-11-04):** `scripts/ops/approve.py` sends the gateway's approver token with the same `urlopen`. Checked on 2026-11-03 with two local servers: a 302 from the "gateway" made it repeat the request, as a GET, to the second server, which received the token. The gateway does not redirect today, and the token approves real changes to the cluster, so it was a story of its own: the approval flow is the project's most sensitive path, and a change to it wants its own proof, which did not belong in a $0 laptop-tooling change. (Correction, 2026-11-04: this said the flow "is proved live on staging" and that the new change would need that proof again. That was a wrong guess about `approve.py`. On staging, `make chaos-approve` approves with `curl` run on the node itself, using the token read from the cluster's own Secret; `approve.py` and `make approve` only talk to a gateway through the laptop's tunnel to the dev server. See `KAV-124` below for how it was proved.) The other callers of `urlopen` send no credential (the model benchmark, and the collector's read of Prometheus inside the cluster), and no service sets `follow_redirects`.
- **`KAV-124`: the approval command follows the same rule.** `approve.py` has its own copy of the handler, as `promote.py` does (the scripts stay self-contained), and `decide()` goes through it. Any redirect (301, 302, 303, 307, 308) stops the command with one plain line: "the gateway answered with a redirect (HTTP 302). It was not followed, so the approver token stays on this address. The decision may already have been recorded: look before running it again. If the gateway has moved, update `KAVAL_GATEWAY_URL`." The line names neither the token nor where the answer pointed. Python's own handler already refused to re-send a `POST` on a 307 or a 308, so the leak was real for 301, 302 and 303 (three of the five tests failed first; the other two pin that it stays refused). Every other answer, a 4xx included, and an unreachable gateway are reported exactly as before. The tests use two real local servers, as here. **The proof against the real thing ran on the laptop on 2026-11-04, for $0:** the real gateway (`uvicorn kaval_gateway.main:app`) against a local Postgres of CI's image, with a real pending action in it. The real `approve.py` with a wrong token got `401 Unauthorized` and exit 1, as before; with the right token it printed the decision and exit 0, and exactly one decision row (`approved`) was in the database. No staging session was needed (see the correction above); [Lab 75](../labs/lab-75-a-login-that-stays-home.md) Part 6 has the commands.

## Delivery

One pull request: the refusal and `AtlassianRedirected` in `atlassian_http.py`, its use in `guard`, the same rule in `promote.py`'s `jira_issue`, and 32 tests (23 in `test_atlassian_http.py`, which now holds 57; 9 in `test_promote.py`). 28 were seen failing first, 10 of them because the second server really received the login (five statuses in each piece of code). The other four pin what must not change (a 304 from the transport; a 200, a 404 and a 500 from the release workflow's Jira read) and passed from the start; the 304 pin was shown to be able to fail by breaking a copy of the code on purpose. Nothing is deployed: these are laptop and CI tools, so there is no staging or production step, and it costs nothing. See [Lab 75](../labs/lab-75-a-login-that-stays-home.md).
