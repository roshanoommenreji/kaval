# Security policy

Kaval is a learning project that is also a real, working system, and its repository is public.
If you find a way to break it or to see something you should not, please tell me privately first.

## How to report a problem

Use GitHub's private report form: **Security tab → "Report a vulnerability"**
(`https://github.com/roshanoommenreji/kaval/security/advisories/new`).

Please do **not** open a public issue or pull request for it, and do not post it anywhere public
before there is a fix.

A useful report says what you found, where (file, line or setting), how to see it for yourself,
and what an attacker could do with it. A short report is fine; I will ask if I need more.

## What to expect

| When | What happens |
|---|---|
| Within 7 days | I confirm I received it and tell you whether I think it is a real problem. |
| Within 30 days | A fix, or a written decision with the reason if I will not fix it. |
| After the fix | A public note in the repository's security advisories, crediting you if you want that. |

This is one person working in short sessions, so these are honest targets, not a contract.

## What is in scope

- The code in this repository: the four services (`services/`), the policy rules (`policy/`),
  the migrations, the scripts and the GitHub Actions workflows.
- The Helm chart and the Terraform in `deploy/` and `infra/`.
- **Anything secret that should not be in the repository or its history.** If you find a key,
  token, account number, endpoint or password in a file or an old commit, report it the same way.
  I treat the history as part of the product: the secret is rotated first, then the history is
  dealt with.
- Ways the agent could be made to do more than it is allowed to. By design the agent only writes
  proposals; only the executor changes anything, and only after the policy rules and (where
  required) a human approval. A way around that is a serious finding.

## What is out of scope

- **Do not test against my running systems.** The production system is switched off most of
  the time, and staging exists only for short test runs. Read the code and the deploy files
  instead, or run the project on your own machine (`make dev`, the k3d steps in the README).
- Denial-of-service, load or "find the open port" scanning, and social engineering.
- Problems in a third-party tool itself (Kubernetes, Postgres, Flux and so on). Report those to
  that project; tell me if Kaval's own use of it makes the problem worse.

If you act in good faith and stay within this page, I will not pursue you for it.

## Versions

Only the current `main` and the version that production runs are supported. Releases are listed
under `docs/releases/`.

## How this repository protects itself

So you know what is already covered: secrets are scanned on every push (gitleaks, full history),
images are scanned (Trivy), every third-party GitHub Action is pinned to an exact commit,
dependency updates arrive weekly through Dependabot, `main` accepts changes only through pull
requests with all checks green, and an OpenSSF Scorecard run reports on the repository's own
practices every week. The reasons are written down in `docs/adr/`.
