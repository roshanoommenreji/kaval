# Lab 41 — Filling staging with a cleaned copy of production's data

**Phase:** 4 · **Story:** `KAV-73` · **Cost:** $0 for steps 0-6 (Docker on your own machine; one read-only
`terraform plan`). Step 7, the live run, is staging for one to two hours: about 10 cents.
Decision: [ADR-0039](../adr/0039-staging-is-filled-from-a-copy-cleaned-on-the-laptop.md).

[Lab 39](lab-39-scrubbing-a-copy-of-production.md) built the scrub and [Lab 40](lab-40-the-nightly-dump-from-the-app-node.md)
the dump. This lab joins them: a script on your machine turns production's dump into a **cleaned** file, and
staging's node fills its empty database from it, before any service starts. Everything is proven on your machine
with a real Postgres; only AWS is stood in for.

**Result:** `make seed-refresh` produces `.build/seed/clean.dump` and refuses to if anything still looks like a
secret. `make staging-up` hands it over and prints `Seed status: ... "status":"ok"`. Four failure cases leave no
unclean data behind.

## 0. What you need

Docker running (Docker Desktop on Windows; wait for it to say it is running), the repository's virtual
environment (`alembic`), Terraform, and a scratch folder **outside** the repository (called `$S` below).

## 1. A dirty stand-in for production's dump

Start a Postgres 16 with pgvector, migrate it with the repository's migrations, and put in a row carrying the
three things that must not reach staging. Build the strings from pieces: the repository's pre-commit check
refuses a 12-digit literal, rightly.

```bash
export MSYS_NO_PATHCONV=1        # Git Bash on Windows: stop it rewriting /paths given to docker
docker run -d --rm --name kaval-src -e POSTGRES_USER=kaval -e POSTGRES_PASSWORD=x -e POSTGRES_DB=kaval \
  -p 55432:5432 pgvector/pgvector:pg16
# wait until `docker logs kaval-src` has said "ready to accept connections" TWICE (the image starts a
# temporary server, stops it, then starts the real one)
POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55432 POSTGRES_DB=kaval POSTGRES_USER=kaval POSTGRES_PASSWORD=x \
  POSTGRES_SSLMODE=disable alembic upgrade head
ACC="123456""789012"; ID="AKIA""ABCDEFGHIJKLMNOP"
docker exec -i kaval-src psql -U kaval -d kaval -q <<SQL
insert into signal(id,source,kind,target,value,observed_at) values
 (gen_random_uuid(),'k8s_event','oom_killed','kaval-demo/checkout',
  '{"restart_count":4,"note":"role arn:aws:iam::${ACC}:role/kaval-prod-node failed, key ${ID}, mail ops.person@corp-example.org"}',now());
SQL
mkdir -p $S/bucket/postgres
docker exec kaval-src pg_dump -U kaval -d kaval -Fc > $S/bucket/postgres/kaval-20261010T000000Z.dump
printf 'postgres/kaval-20261010T000000Z.dump' > $S/bucket/postgres/LATEST
```

## 2. Stand-ins for the AWS calls

Two small scripts in `$S/fakebin` (made executable). `aws` treats a folder (`$BUCKETDIR`) as the bucket; `curl`
answers the node's lookups and serves `restore.sh` and `anonymise.sql` from your working copy at `/repo`.

```bash
# fakebin/aws
#!/usr/bin/env bash
B="$BUCKETDIR"
case "$1 $2" in
  "ec2 describe-instances") echo "${FAKE_DB_HOST:-stg}" ;;
  "ssm get-parameter") echo x ;;
  "s3 ls") key="${3#s3://*/}"; [[ -e "$B/$key" ]] ;;
  "s3 cp") src="$3"; dst="$4"
     if [[ "$src" == s3://* ]]; then f="$B/${src#s3://*/}"; [[ -e "$f" ]] || exit 1
        if [[ "$dst" == "-" ]]; then cat "$f"; else cp "$f" "$dst"; fi
     else k="$B/${dst#s3://*/}"; mkdir -p "$(dirname "$k")"; if [[ "$src" == "-" ]]; then cat > "$k"; else cp "$src" "$k"; fi; fi ;;
  *) echo "fake aws: unexpected $*" >&2; exit 2 ;;
esac

# fakebin/curl
#!/usr/bin/env bash
url=""; out=""; prev=""
for a in "$@"; do [[ "$a" == http* ]] && url="$a"; [[ "$prev" == "-o" ]] && out="$a"; prev="$a"; done
case "$url" in
  */latest/api/token) echo tok ;;  */placement/region) echo ap-south-1 ;;
  */scripts/ops/restore.sh) cp /repo/scripts/ops/restore.sh "$out" ;;
  */scripts/ops/anonymise.sql) if [[ -n "${BROKEN_SCRUB:-}" ]]; then echo "select 1/0;" > "$out"; else cp /repo/scripts/ops/anonymise.sql "$out"; fi ;;
  *) echo "fake curl: unexpected $url" >&2; exit 22 ;;
esac
```

## 3. The cleaning script, on a normal day

```bash
PATH="$S/fakebin:$PATH" BUCKETDIR="$S/bucket" PROD_BACKUP_BUCKET=t bash scripts/ops/seed-refresh.sh; echo "exit=$?"
```

(`PROD_BACKUP_BUCKET` skips the bucket lookup; for real, the script finds the one bucket called
`kaval-prod-db-backups-*`.) Expected, exit 0: it names the dump, restores into a throwaway Postgres, scrubs,
dumps, and ends with `cleaned copy ready: .build/seed/clean.dump`. No `kaval-seed-*` container is left. Then
look at what is in the file:

```bash
docker exec -i kaval-src pg_restore -f - < .build/seed/clean.dump | grep -c -E "corp-example|AKIAABCDEFGHIJKLMNOP|:role/kaval-prod-node"   # 0
docker exec -i kaval-src pg_restore -f - < .build/seed/clean.dump | grep -E "oom_killed"   # the row, with REDACTED / user@example.com
```

## 4. Three ways it must refuse; none may leave a cleaned file

Copy the script to a scratch folder shaped like the repository (`$S/root/scripts/ops/seed-refresh.sh`) so you can
swap in a different `anonymise.sql` next to it, and run it three times:

| Case | How | Expected |
|---|---|---|
| A scrub that does nothing | `anonymise.sql` is `select 1;` | `the cleaned copy STILL contains something that looks like a secret ... Not keeping it`, exit 1, no `.build/seed` |
| A scrub that fails | `anonymise.sql` is `select 1/0;` | `division by zero`, exit non-zero, no `.build/seed` |
| No dump | rename `postgres/LATEST` | `no postgres/LATEST in the production bucket`, exit 1 |

The first row is the point of the independent check: the scrub's own "second pass changes nothing" test would
pass a scrub that does nothing, because there is nothing to change. Matching on the shape of the data does not.

## 5. The staging node's side

Render the real boot script as Terraform does for staging (as in [Lab 40](lab-40-the-nightly-dump-from-the-app-node.md)
step 1, with `seed_database = true`), and cut the seed script out of it:

```bash
sed -n "/<<'SEEDSCRIPT'/,/^SEEDSCRIPT/p" staging.sh | sed '1d;$d' > seed.sh
sed -e 's/sslmode=require/sslmode=disable/; s/PGSSLMODE=require/PGSSLMODE=disable/; s/seq 1 40/seq 1 2/; s/sleep 15/sleep 1/' seed.sh > seed-test.sh
```

(The throwaway server has no TLS, and the waits are shortened from ten minutes to seconds.) Start an empty
"staging" database and run the script from a Linux container on the same Docker network, so `psql` and
`pg_restore` are the real ones:

```bash
docker network create seednet
docker run -d --rm --name kaval-stg --network seednet --network-alias stg -e POSTGRES_USER=kaval \
  -e POSTGRES_PASSWORD=x -e POSTGRES_DB=kaval pgvector/pgvector:pg16
run_seed() { docker run --rm --network seednet -v "$S/seedtest":/t -v "<repo>":/repo:ro \
  -e BUCKETDIR=/t/bucket-stg -e BROKEN_SCRUB="${BROKEN_SCRUB:-}" -e PATH=/t/fakebin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  pgvector/pgvector:pg16 bash -c 'mkdir -p /var/lib/kaval; bash /t/seed-test.sh; echo "exit=$?"; cat /var/lib/kaval/seed-status.json'; }
```

| Case | Setup | Expected |
|---|---|---|
| No seed in the bucket | nothing | exit 1, status `failed`, 0 tables |
| `seed/NONE` | `echo none > bucket-stg/seed/NONE` | exit 10, `skipped`, 0 tables |
| A cleaned copy | copy `.build/seed/clean.dump` to `bucket-stg/seed/clean-X.dump`; `seed/LATEST` holds `seed/clean-X.dump` | exit 0, `sanitised - the final check passed`, 10 tables, the row scrubbed, `alembic_version` at head, status `ok` |
| Run it again | same | exit 10, `the database already has tables`: it did not restore over the data |
| The scrub fails | empty the database (`drop schema public cascade; create schema public`), run with `BROKEN_SCRUB=1` | exit 1, **0 tables left**, `failed` |
| A seed with production's GRANTs | in the source database `create role kaval_gateway login; grant select on signal to kaval_gateway;`, dump to `seed/withgrants.dump`, point `LATEST` at it, empty the database | exit 0, **no** grants to `kaval_gateway` on staging |

Then show why `--no-privileges` is there: restore that same dump with `pg_restore --clean --if-exists --no-owner`
(no flag). It prints `errors ignored on restore: 1` and exits 1, because the role it names does not exist on a
fresh staging server.

The upload side, `upload_seed` and `seed_report` in `scripts/ops/staging.sh`, is tested the same way with a fake
`tf` and `on_node`: first time (uploads, pointer last), second time (`already handed over`), no cleaned copy
(writes `seed/NONE`), and the report for `ok`, `skipped`, `failed` and nothing recorded.

## 6. What Terraform will do, and the size limit (read-only)

```bash
cd infra/envs/staging && terraform validate          # valid
cd ../prod && terraform plan -lock=false             # Plan: 0 to add, 2 to change, 0 to destroy
```

Production's two changes are the launch template and the Auto Scaling group's pointer to it: the permissions step
in the shared boot script became a function, which changes production's text (not its behaviour) and takes
effect at the next `make up`, after staging has run the same text. Staging's plan creates only its own resources,
including the bucket with `force_destroy = true`. Re-measure the rendered size ([Lab 40](lab-40-the-nightly-dump-from-the-app-node.md)
step 1b): staging is about 23.7 KB raw and 11,000 characters zipped, under EC2's 16,384-byte limit with about a third to spare.

## 7. The live run on staging (about 10 cents)

After the change is merged (the node fetches `restore.sh` and `anonymise.sql` from `main`), from Git Bash in the
repository:

```bash
AWS_PROFILE=kaval make seed-refresh      # Docker running; ~1-2 minutes; reads production's dump, writes .build/seed/
AWS_PROFILE=kaval make staging-up        # type y; ~5-10 minutes; ends with "Seed status: {... "status":"ok" ...}"
```

Then check, read-only, over Session Manager on the node: the restore time in `/var/lib/kaval/seed-status.json`,
the row counts, `alembic_version`, that the services are `Running` and that `\dp signal` lists the service roles
(the second permissions run). `AWS_PROFILE=kaval make staging-down` ends it (the bucket now empties itself).

**First attempt, 2026-10-10: failed on Windows paths.** The AWS CLI on Windows is a native program and does not understand Git Bash's `/tmp/...` paths. It wrote the raw production dump to `C:	mp\` instead of the script's temp folder (where nothing would delete it), and the script carried on with no file. The stray file was deleted by hand. The script now passes `cygpath -m` paths to `aws` and stops if the file is not where it expects. My local proof used a bash stand-in for `aws`, which cannot show this.

_The result of the successful run is recorded below once it has happened._

## What to take from this

- **A stand-in proves the logic, not the platform.** The fake `aws` was a bash script, so it could never show that the real one is a Windows program with different path rules. Run the real tool on a tiny harmless file first.
- **Clean before it travels, not after it arrives.** The same scrub, run one step earlier, removes the whole
  window in which raw data sits somewhere less protected, and removes the need for any permission between
  environments.
- **A check that shares code with the thing it checks proves less than it looks.** The scrub's "second pass changes
  nothing" would accept a scrub that does nothing. A second check that only looks at the shapes of secrets does not.
- **"The tables exist" is not "the upgrade has run".** On a fresh database the first meant the second; on a
  restored one it does not, and the permissions step would have run before the migration added its tables.
- **A permission list copied from another environment is an error waiting for roles that are not there.**
  One place hands out permissions; the restore does not bring its own.
- **Make the empty case loud.** A staging that quietly starts empty looks exactly like a seeded one until
  someone tests it. The status line says which it is.
