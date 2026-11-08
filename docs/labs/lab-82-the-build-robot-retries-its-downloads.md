# Lab 82 — The build robot retries its downloads

**Phase:** 8 (hardening) · **Story:** `KAV-133` · **Time:** about 30 minutes · **Cost:** $0 (CI only, no AWS)

Every CI run starts by downloading a few tools: the scanners (Trivy, gitleaks), OPA, helm and kubeconform. Each is fetched with `curl` from GitHub's or helm's servers and then checked against a checksum written in the repository. On 8 November 2026 those servers answered `503` or `504`, or dropped the connection, and seven jobs went red for no reason of ours (four in the afternoon, three on pull request #339 in the evening). Each was fixed by re-running the failed job by hand. This lab makes the robot do that retry itself.

## Prerequisites

- The repository checked out, the project's Python environment (`make sync`)

## Part 1 — See the problem

```bash
gh run list --status failure --limit 10
gh run view <run-id> --log-failed | grep -i "curl\|503\|504\|died"
gh run rerun <run-id> --failed
```

The failed step is always an `install ...` step, the message is a server error or `Connection died`, and the re-run passes without any change.

## Part 2 — The change

The six download lines (`ci.yml`: gitleaks, OPA, helm, kubeconform, Trivy in the image job; `release.yml`: Trivy) changed from

```bash
curl -fsSLo tool.tgz "https://..."
```

to

```bash
curl -fsSL --retry 5 --retry-all-errors -o tool.tgz "https://..."
```

| Flag | What it does | Why |
|---|---|---|
| `--retry 5` | try again up to five more times, waiting longer between tries (1 s, 2 s, 4 s, ...) | a server hiccup lasts seconds |
| `--retry-all-errors` | retry on any failure, not only on the few curl treats as "temporary" | a `503` from GitHub is not one of those by default |
| `-f` (unchanged) | fail on an HTTP error instead of saving the error page as the tool | the checksum would catch it anyway, but the message is clearer |

**Why retrying is safe.** The line after every download is `sha256sum --check --strict` against a checksum pinned in the workflow. A half-fetched, swapped or corrupted file fails that check no matter how many tries it took, so a retry can only help the robot get the real file; it cannot let a wrong one through.

## Part 3 — The tests, written first

```bash
python -m pytest scripts/dev/test_workflow_downloads.py -q
```

`scripts/dev/test_workflow_downloads.py` reads both workflow files and checks, for every step that runs `curl`: it retries (`--retry 5`), it retries on any error (`--retry-all-errors`), it still fails on an HTTP error (`-f`), and it still checks the checksum afterwards. A fourth test counts the downloads (six), so a change to the file layout that makes the test find nothing fails loudly instead of passing for no reason.

Watched failing first: with the workflow files as they were, six checks failed (one per download); after the change all 13 pass.

## What this does not cover

- A server that is down for longer than the retries last (about half a minute in all): the job still fails and is re-run by hand, as before.
- Downloads that are not `curl` lines in the two workflow files: the third-party actions are fetched by GitHub itself, and the Docker builds pull their base images through Docker.
- The k3s and Flux installers on the servers (cloud-init) have their own checksum checks and are unchanged.

## Done when

- [x] All six download lines retry on any error and keep their checksum check
- [x] The tests above fail without the change and pass with it
- [x] CI is green on the pull request and it is merged (14 of 14 checks, merged as #342 on 2026-11-08)
- [x] A later release round finishes without a manual re-run of a download step (evidence, not a gate: the round that built `sha-ba87981` after #337 merged on 2026-11-08 finished green on its first attempt, no re-run; the two earlier rounds that day had needed re-runs)

## Clean up

Nothing was created outside the repository.
