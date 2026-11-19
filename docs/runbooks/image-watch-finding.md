# The weekly image watch found a known problem (or could not look)

The weekly image watch ([ADR-0106](../adr/0106-a-weekly-scan-of-what-production-runs-and-a-monthly-check-in.md))
opened a GitHub issue "Weekly image watch needs a person". It says one of: a library or an image that Kaval ships
has a newly known HIGH or CRITICAL problem **with a fix available**; or the scan could not look properly, so
nobody knows whether all is well.

## Signals

- A GitHub issue labelled `image-watch`, and a red `image-watch` run on a Monday (about 10:53 India time)
- A line such as `main: uv.lock: CRITICAL CVE-2099-0001 in libfoo 1.0 (fixed in 1.1)`. The part before the first
  colon says what was scanned: `main` is what the next release would ship; `production pin sha-…` is what the
  next production start-up would run
- Or "read only N packages … so this is not an all-clear", or "the check could not run" (then treat it as a
  problem, not as good news)

## Likely causes

1. **A library in `uv.lock`.** A flaw was published for a version we pin. Dependabot (Saturdays) often has the
   update pull request open already
2. **The base image** (`python:3.11-slim-bookworm`, pinned by digest). The Debian packages inside it have a new
   flaw with a fix; the pin has to move to a newer digest
3. **A third-party image the chart names** (pgvector, Ollama, Prometheus). No updater covers these: they are named in `deploy/charts/kaval/values.yaml`, which Dependabot does not read. On 2026-11-19 all three had findings (`KAV-169`)
4. **The watch could not look.** The vulnerability list did not download, a registry was unreachable, or a pin no
   longer exists. A scan that reads too little is reported as "broken", never as "clean"
5. **A test run.** The issue says "THIS WAS A TEST RUN". Ignore it; the next normal run closes it

## Diagnosis

Open the run linked in the issue; the log lists every target with its package count and findings.

To repeat it by hand on the laptop, with Trivy installed (the version in `.github/workflows/ci.yml`):

```bash
python scripts/ops/image_watch.py            # the same scan; add --plant to see it find a planted old library
```

Exit status 0 is clean, 1 is findings, anything else is "the check itself broke". It needs no AWS login.

## Remediation

Which fix depends on where the finding is. Production is parked between sessions, so there is time; the order is
what matters. Every step below is reversible (a pull request can be reverted) and changes nothing live until a
release is promoted.

| Finding is in | Do this |
|---|---|
| A library (`uv.lock`) | Merge Dependabot's update pull request if there is one; otherwise `make lock` after raising the pin in `pyproject.toml`. Then a normal release round (`release-a-version.md`), staging, and only then a promotion. |
| The base image | Move the digest in the Dockerfiles to the newest one (Dependabot's `docker` updates do this), then a release round as above. |
| A third-party image in the chart | Raise the tag and digest in `deploy/charts/kaval/values.yaml` (nothing opens this for you), prove it on staging, then promote. Pick a release built with a fixed Go, then check by hand that the finding is gone: `python scripts/ops/image_watch.py` |
| "Could not look" | Re-run the workflow by hand once (Actions, image-watch, Run workflow). If it fails twice, open the log: a download failure is usually gone on the next try; a missing pin means `deploy/environments/prod/values.yaml` names a tag whose commit is gone. |

Production does not need to be started for any of this. The fix reaches production at its next start-up, after
it has passed staging, like any other change.

## Do not

- Do not add the finding to `.github/trivy-ignore.yaml` to make the issue go away. An exception needs a reason and
  an end date (ADR-0033), and only for something that cannot be fixed yet.
- Do not promote a rebuilt image straight to production. Build once, pass staging, promote that digest
  (ADR-0004).
- Do not close the issue by hand. The next clean run closes it, and a hand-closed issue reopens as a new one
  while the problem is still there.
