# Lab 104 — A second size for the app server

**Phase:** 8 (hardening) · **Story:** `KAV-163` · **Decision:** [ADR-0103](../adr/0103-the-app-server-falls-back-to-a-second-size.md) · **Status:** built and proved on staging 2026-11-18; production's apply is recorded in Part 4 when it happens

## What you will build

The app server's group stops depending on one size. It is given a list, `t4g.medium` then `c6g.large`, and tries them in that order, so a shortage of the first does not stop a start-up.

You need the repository, the project's Python environment (`make sync`) and Terraform. Nothing here touches AWS until Part 3.

## Why

Two start-ups in two days (staging on 17 Nov, production on 18 Nov) met AWS answering "we currently do not have sufficient t4g.medium capacity" in every Mumbai zone. Terraform waits for the server, gives up, and leaves the group retrying. A real team does not wait for a pool to refill; it lets the group pick another equivalent size. See ADR-0103 for the patterns and what was rejected.

## Part 1 — Look at what AWS offers (read-only)

```bash
AWS_PROFILE=kaval aws ec2 describe-instance-types --instance-types t4g.medium c6g.large \
  --query "InstanceTypes[].[InstanceType,VCpuInfo.DefaultVCpus,MemoryInfo.SizeInMiB]" --output text
AWS_PROFILE=kaval aws pricing get-products --region us-east-1 --service-code AmazonEC2 \
  --filters Type=TERM_MATCH,Field=instanceType,Value=c6g.large Type=TERM_MATCH,Field=regionCode,Value=ap-south-1 \
  Type=TERM_MATCH,Field=operatingSystem,Value=Linux Type=TERM_MATCH,Field=tenancy,Value=Shared \
  Type=TERM_MATCH,Field=preInstalledSw,Value=NA Type=TERM_MATCH,Field=capacitystatus,Value=Used
```

Both are 2 CPUs and 4096 MiB, arm64. Prices on 2026-11-18: `t4g.medium` $0.0224/hr, `c6g.large` $0.0426/hr.

## Part 2 — The change

- `infra/modules/node/variables.tf`: `instance_type` (one size) becomes `instance_types` (a list, default `["t4g.medium", "c6g.large"]`) with two checks: at least one size and no repeats; every size Graviton by name.
- `infra/modules/node/main.tf`: the launch template takes the first size; the group gets a `mixed_instances_policy` with `on_demand_allocation_strategy = "prioritized"`, `on_demand_percentage_above_base_capacity = var.spot ? 0 : 100`, and one `override` per listed size. The spot block moved from the launch template to the distribution (AWS refuses it in the template beside a list).
- `infra/envs/staging`: a `node_instance_types` variable (same default) passed to the module, so a proof can reorder it. Production keeps the module's list.
- `scripts/dev/test_node_sizes.py`: 15 offline tests, each checker also run against a broken input (a single size, a repeat, an unknown or x86 size, a different shape, the wrong first size, a group that is not prioritised, a spot block in the template).

```bash
.venv/Scripts/python.exe -m pytest scripts/dev/test_node_sizes.py -q
terraform -chdir=infra/envs/staging init -backend=false && terraform -chdir=infra/envs/staging validate
terraform -chdir=infra/envs/prod init -backend=false && terraform -chdir=infra/envs/prod validate
```

## Part 3 — Prove it on staging, then apply to production (on the owner's typed gos)

1. A staging session with the second size first: `-var 'node_instance_types=["c6g.large","t4g.medium"]'`, plan shown first. Expect the node to be a `c6g.large` and the smoke test to pass 7 of 7.
2. Production's plan: only the group's policy changes (and the template, if it changed); then apply and check at the next start-up.

A real shortage is the only way to see the fallback itself; when it happens the group's scaling activity shows a `c6g.large` launch.

## Part 4 — Live record

### Staging proof (2026-11-18, 10:50 to 11:06 IST, on his typed `go staging proof`)

- **Plan first:** `TF_VAR_node_instance_types='["c6g.large","t4g.medium"]'`, 94 to add, 0 to change, 0 to destroy, nothing of production. The group in the plan: `prioritized`, 0 base, 100 percent On-Demand, overrides `c6g.large` then `t4g.medium`; the launch template's size `c6g.large`.
- **Built** (94 added, 10:55). `describe-instances`: the app server is a **`c6g.large`** (zone `ap-south-1b`), the database server a `t4g.small`.
- **Smoke test (`staging_smoke.py --no-record`)**: 7 of 7 on `sha-f280013` (9 pods ready, 0 restarts, digests equal the registry's, database at `d4f7a2c9e815`, both API calls 200). The eighth line, "build is a tagged release", fails by design here because production already runs v0.4.4; the pass was recorded on the first staging run and this run records nothing.
- **Headroom on the fallback size:** 2 CPUs, 3,805 MB, 2,066 MB used and 1,547 MB available with the model loaded, node CPU 7 percent. Production's `t4g.medium` showed 2,251 MB used on the same system the same morning, so the fallback has at least the same room.
- **Torn down 11:06:** 93 destroyed, no staging backup copies existed; the sweep clean; production's 27 backup copies unchanged. About 10 cents.
- **What this does not show:** the fallback itself (the group choosing the second size when the first is unavailable) can only be seen in a real shortage. What it shows is that the second size, which is what the fallback would give us, runs the whole system, and that AWS accepts the group's policy.

### Production (not yet)

Production's plan (only the group's policy changes) and its apply are separate gos; this section is filled in then.

