# Lab 40 — The nightly dump, taken by the app node, and proven before anything is switched on

**Phase:** 4 · **Story:** `KAV-74` · **Cost:** $0. Nothing in this lab starts an AWS resource (one read-only
`terraform plan` talks to AWS).
Decision: [ADR-0038](../adr/0038-nightly-dump-is-taken-by-the-app-node.md).

Staging is seeded from last night's production dump ([Lab 39](lab-39-scrubbing-a-copy-of-production.md)), but
the backup bucket is empty: the nightly dump has never run. This lab builds the piece that takes the dump and
runs it end to end on your own machine, with stand-ins only for the three AWS calls.

**Result:** a systemd timer on the app node runs `scripts/ops/backup.sh` at 19:30 UTC with the node's own role.
Locally: the dump is uploaded, readable and contains the data; the `LATEST` pointer moves; an old dump expires;
four failure cases upload nothing; removing the new readable-file check lets junk through.

**Not covered:** anything on AWS. See ADR-0038, "Not proven".

## 0. What you need

The same throwaway Postgres as Lab 39 (Docker `pgvector/pgvector:pg16`, or `pgserver` in a virtual environment
outside the repository), reachable on `localhost`, plus Terraform. Create a database called `kaval` owned by a
role called `kaval` (the timer connects as that name) and put the real tables in it:

```bash
psql -d postgres -c "create role kaval superuser login" -c "create database kaval owner kaval"
POSTGRES_USER=kaval POSTGRES_PASSWORD=x POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=<port> POSTGRES_DB=kaval \
  POSTGRES_SSLMODE=disable alembic upgrade head
psql -U kaval -d kaval -c "insert into signal(id,source,kind,target,value,observed_at)
  values (gen_random_uuid(),'k8s_event','oom_killed','kaval-demo/checkout','{\"restart_count\":4}',now())"
```

## 1. Render the real boot script and check it

The node's first-boot script is a Terraform template. Render it exactly as Terraform would, once with a bucket
and once without, in a scratch folder **outside** the repository:

```hcl
# main.tf (scratch folder, with a copy of infra/modules/node/user_data.sh.tftpl next to it as u.tftpl)
locals { vars = { ssh_public_key = "x", k3s_version = "v", k3s_sha256_arm64 = "a", flux_version = "v",
                  flux_sha256_arm64 = "b", name_prefix = "kaval-prod", gitops_env = "prod" } }
output "with"    { value = templatefile("${path.module}/u.tftpl", merge(local.vars, { backup_bucket_name = "test-bucket" })) }
output "without" { value = templatefile("${path.module}/u.tftpl", merge(local.vars, { backup_bucket_name = "" })) }
```

```bash
terraform init && terraform apply -auto-approve
terraform output -raw with > with.sh;  terraform output -raw without > without.sh
bash -n with.sh && bash -n without.sh && echo "both valid shell"
grep -c kaval-db-backup with.sh without.sh        # with: several, without: 0
```

(On Windows, give `templatefile` a path next to the `.tf` file, as above. Terraform for Windows does not
understand a Git Bash `/c/Users/...` path.)

Pull the node's backup command out of the rendered script:

```bash
sed -n "/<<'BACKUPSCRIPT'/,/^BACKUPSCRIPT/p" with.sh | sed '1d;$d' > wrapper.sh
sed 's/POSTGRES_SSLMODE=require/POSTGRES_SSLMODE=disable/' wrapper.sh > wrapper-nossl.sh   # the throwaway server has no TLS
```

## 1b. Measure the rendered size (found live, 2026-10-10)

EC2 refuses user data over 16,384 bytes, and `terraform plan` does not check it. The first real `make up` failed
with `InvalidUserData.Malformed` because this script had grown to 17,643 bytes. With the rendered `with.sh` from
step 1:

```bash
wc -c with.sh                                            # raw size: must be under 16384, or it must be zipped
python -c "import gzip;print(len(gzip.compress(open('with.sh','rb').read(),9)))"   # ~6550 once zipped
```

The node module now sends the script with `base64gzip` (cloud-init unzips it), so the second number is the one AWS
counts. Run both after any edit to the boot script.

## 2. Stand-ins for the AWS calls

Put these two scripts in a folder called `fakebin` and make them executable. `curl` answers the node's lookup of
its own region and serves `backup.sh` from your working copy; `aws` answers the database lookup and the password
read, and treats a folder (`$BUCKETDIR`) as the bucket.

```bash
# fakebin/curl
#!/usr/bin/env bash
url=""; out=""; prev=""
for a in "$@"; do [[ "$a" == http* ]] && url="$a"; [[ "$prev" == "-o" ]] && out="$a"; prev="$a"; done
case "$url" in
  */latest/api/token) echo tok ;;  */placement/region) echo ap-south-1 ;;
  */scripts/ops/backup.sh) cp "$REPO/scripts/ops/backup.sh" "$out" ;;
  *) echo "unexpected $url" >&2; exit 22 ;;
esac

# fakebin/aws
#!/usr/bin/env bash
B="$BUCKETDIR"
case "$1 $2" in
  "ec2 describe-instances") echo "${FAKE_DB_HOST:-127.0.0.1}" ;;
  "ssm get-parameter") echo x ;;
  "s3 cp") src="$3"; dst="$4"; key="${dst#s3://*/}"; mkdir -p "$(dirname "$B/$key")"
           if [[ "$src" == "-" ]]; then cat > "$B/$key"; else cp "$src" "$B/$key"; fi ;;
  "s3 ls") ls -l --time-style=long-iso "$B/postgres/" | awk 'NR>1{print $6, $7, $5, $8}' ;;
  "s3 rm") rm -f "$B/${3#s3://*/}" ;;
  *) echo "unexpected $*" >&2; exit 2 ;;
esac
```

```bash
export REPO="$PWD/.." BUCKETDIR="$PWD/bucket" PGPORT=<port> PATH="$PWD/fakebin:$PATH"   # REPO = the repository root
```

## 3. A normal night

Seed the fake bucket with one dump 40 days old and one 3 days old, then run the node's command:

```bash
mkdir -p bucket/postgres
echo old    > "bucket/postgres/kaval-$(date -u -d '40 days ago' +%Y%m%d)T000000Z.dump"
echo recent > "bucket/postgres/kaval-$(date -u -d '3 days ago'  +%Y%m%d)T000000Z.dump"
bash wrapper-nossl.sh; echo "exit=$?"
ls bucket/postgres; cat bucket/postgres/LATEST
```

Expected, exit 0: `dumping kaval...`, a size and the destination, `expiring` the 40-day-old file only, the
3-day-old file kept, and `LATEST` naming the new dump. Then prove the new file is a real archive with the data
in it:

```bash
f="bucket/$(cat bucket/postgres/LATEST)"
pg_restore --list "$f" | grep -c "TABLE DATA"                   # 10 tables
pg_restore -f - --data-only -t signal "$f" | grep -c oom_killed # 1
```

## 4. Four ways a night can go wrong; none may upload anything

Put a fake `pg_dump` first on `PATH` (a folder `fakepg`) for each, and note the bucket listing and `LATEST`
before and after.

| Case | How | Expected |
|---|---|---|
| Unreadable dump | fake `pg_dump` writes `not a real archive` to its `-f` file and exits 0 | `the dump cannot be read back by pg_restore -- NOT uploading it`, exit 1 |
| Client too old | fake `pg_dump` prints `aborting because of server version mismatch`, exits 1 | that message, exit 1 |
| No database server | `FAKE_DB_HOST=None bash wrapper-nossl.sh` | `no running database server found -- nothing to dump`, exit 1 |
| Login refused | fake `pg_dump` prints `password authentication failed`, exits 1 | that message, exit 1 |

In all four the listing and `LATEST` are unchanged. (The second row proves the script stops on that error; it does
not prove what the real Postgres 15 tool does, which is the first thing the live run checks.)

**Prove the readable-file check can fail.** Copy the repository's `backup.sh` into a scratch `scripts/ops/`,
delete the `if ! pg_restore --list ... fi` block from the copy, point `REPO` at the scratch folder and rerun the
"unreadable dump" case. A 1 KB junk file is uploaded and `LATEST` is moved to it. That is what the check prevents.

## 5. What Terraform will do (read-only)

```bash
cd infra/envs/prod && terraform plan -lock=false
```

Expected: `Plan: 0 to add, 2 to change, 0 to destroy.` The two changes are the launch template's `user_data` and
the Auto Scaling group's pointer to the new template version. Nothing is running to be disturbed, and nothing is
applied. The change reaches a machine on the next `make up`.

## What to take from this

- **A schedule has to be checked against the other schedules.** The dump was set for a time at which the
  auto-stop had already switched everything off. Nothing failed; it would simply never have run.
- **Check the tools match the server, not just that they exist.** The node had a Postgres client; it was one
  major version behind the database.
- **A backup you cannot read is worse than none**, because it moves the pointer that the restore trusts.
- **Prefer using an identity to storing a key.** The node already is the identity; the timer needs no secret
  that did not exist before.
