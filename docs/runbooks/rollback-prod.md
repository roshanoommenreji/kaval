# Roll prod back to an earlier version

A new release is misbehaving in prod and you want the previous version back. The tool is `rollback.yml`
([ADR-0034](../adr/0034-rollback-workflow-and-what-a-rollback-may-go-back-to.md),
[Lab 35](../labs/lab-35-rollback-workflow.md)). It opens a pull request; merging it is the go/no-go. It never
touches the cluster and never asks Jira. Measured on staging: merge to healthy in about 80 seconds.

## Signals

- Pods in `kaval-prod` in `CrashLoopBackOff` or `Error` right after a promotion
- The gateway answering errors where the previous version did not
- A migration job that failed on the last release (`kaval-prod-migrate-*`)
- Someone says "put it back"

## Likely causes

1. The new release has a bug staging did not show (most often)
2. The new release's migration job failed or left the schema half-changed
3. A dependency or secret changed at the same time as the release

## Diagnosis

1. **Which version?** The one prod ran before. Prod's pins in `deploy/gitops/prod/helmrelease.yaml` show the
   current tag; `git log -p -- deploy/gitops/prod/helmrelease.yaml` shows the earlier ones. ECR keeps only the
   newest 15 images per repository ([ADR-0036](../adr/0036-ecr-keeps-the-newest-fifteen-tagged-images.md)), so a
   very old version may be gone; `rollback.yml` says so and refuses.
2. **Did the bad release add a database migration?** Compare `migrations/versions/` at the two commits
   (`git diff --stat <old-commit> <new-commit> -- migrations/versions`). The workflow also tells you.
   - **No migration:** go to step 1 below.
   - **A migration, and prod has not run the bad release yet** (the database never reached that revision):
     nothing to undo; go to step 1 and tick `accept_migrations`.
   - **A migration and prod's database already has it:** do "Undo the migration" first. Skipping this is
     the failure Lab 35 reproduced: the old version's database step cannot find the newer revision, the
     upgrade stalls, and Flux puts the newer release back.

## Remediation

### 1. Undo the migration (only when prod's database is ahead; reversible only with a snapshot, blast radius: the database)

The database holds real data. Take a snapshot of its data volume first (Lab 27 shows restoring one).

1. Read the migration's `down_revision` in `migrations/versions/<the new file>.py`; that is the revision to go
   back to. Check its `downgrade()` does what you expect.
2. On the prod node, over Session Manager, run a one-off job **with the newer image prod is running now**
   (the older image does not know the newer revision):

   ```bash
   export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
   IMG=$(kubectl get deploy kaval-prod-gateway -n kaval-prod -o jsonpath='{.spec.template.spec.containers[0].image}')
   cat <<EOF | kubectl apply -f -
   apiVersion: batch/v1
   kind: Job
   metadata: {name: db-downgrade, namespace: kaval-prod}
   spec:
     backoffLimit: 0
     template:
       spec:
         restartPolicy: Never
         imagePullSecrets: [{name: ecr-cred}]
         containers:
           - name: downgrade
             image: $IMG
             command: ["alembic", "downgrade", "<down_revision>"]
             envFrom: [{secretRef: {name: kaval-postgres-admin}}]
   EOF
   kubectl wait --for=condition=complete job/db-downgrade -n kaval-prod --timeout=120s
   kubectl logs job/db-downgrade -n kaval-prod | tail -3    # "Running downgrade <new> -> <old>"
   kubectl delete job db-downgrade -n kaval-prod
   ```

3. Expect the running services to keep working: the older schema is missing only what the newer code added,
   which for additive migrations (a new nullable column) the running code already tolerates for the minutes
   this takes. If the migration was destructive, stop and treat this as a data incident
   ([restore-from-backup.md](restore-from-backup.md)).

### 2. Roll back (reversible: promote the newer tag again; blast radius: prod's four services)

1. Actions tab → **rollback** → Run workflow (branch `main`). `tag`: the earlier tag (for example
   `sha-ffb436b`). `reason`: one sentence. `accept_migrations`: tick it only if the section above applies.
   Or: `gh workflow run rollback.yml --ref main -f tag=sha-ffb436b -f reason="..."`.
2. The `gate` job prints four PASS/FAIL lines. A FAIL says why; fix that, do not work around it. In ~30
   seconds a pull request `fix(release): roll back prod to <tag>` appears.
3. Actions tab → the pull request's first run → **Approve and run**. Wait for every check (about 2 minutes).
4. Read the pull request description (why, images, what the gate checked), then **merge**.
5. Flux on prod pulls `main` within a minute and the services are healthy about 25 seconds later.
   Verify on the node: `kubectl get pods -n kaval-prod` (all `1/1 Running`, images on the old tag) and
   `kubectl get helmrelease -n flux-system` (`Released=True`).

## Do not

- Do not edit prod's tags by hand: `promotion-guard` allows past tags, but you skip the gate's image and
  database checks.
- Do not tick `accept_migrations` to "see if it works": the older version cannot run its database step against
  a database that is ahead, and the attempt takes minutes to fail.
- Do not promote the bad version again until the cause is fixed.

## Afterwards

- Prod now runs the old version; `main` says so. The bad version stays on the passed-staging record, so
  `promote.yml` could promote it again: do not, until the cause is fixed and staging has passed afresh.
- Open a Jira story for the cause. If a migration was undone, record which one and that the data it held
  (if any) was dropped.
- If the rollback stalled instead (pods unchanged, `HelmRelease` `Stalled`), read the migrate job's log
  (`kubectl logs -n kaval-prod job/kaval-prod-migrate-<n>`); `Can't locate revision` means the section
  "Undo the migration" was needed.
