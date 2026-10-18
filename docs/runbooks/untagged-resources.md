# The tag scan found Kaval resources without their labels

`make tag-scan` (see [ADR-0061](../adr/0061-every-resource-carries-three-tags-and-the-bill-is-split-by-them.md))
lists Kaval resources in the live account that are missing `Project`, `Env` or `ManagedBy`, or carry a value outside
the allowed lists. This is **for a person**: Kaval proposes no fix, because labelling changes what the hard stop and
the cost reports can see, and a wrong label is worse than a missing one. Nothing is broken today; the cost is that
the resource's spend cannot be attributed and, for a missing `Project`, the budget's hard stop would not stop it.

## Signals

- `make tag-scan` exits with status 1 and lists lines such as
  `ec2/snapshot  snap-...  kaval-prod-database-pre-stop  missing Env; missing ManagedBy`
- `make cost-showback` shows a large "no Env label" share (some of it is never labellable: support, tax, data transfer,
  the Cost Explorer requests themselves)
- For servers, disks, snapshots and fixed addresses a daily scan now files an incident named
  `resource_unlabelled:aws:…` (`KAV-94`, [waste-found.md](waste-found.md)); `make tag-scan` is still the full,
  on-demand check across every resource type

## Likely causes

1. **Something a service launches by itself**, which Terraform's default tags do not reach (a server started by the
   Auto Scaling group, a snapshot made by one of Kaval's jobs). The fix is in the code that creates it, not a
   label on the one resource: `tag_policy.py` should have refused a pull request that created it, so its rule is
   missing and needs adding
2. **Made by hand** in the console or CLI
3. **A label removed or changed by hand**, or a value outside the lists (for example `Env=Prod`)
4. **A resource from before the policy** (the 10 pre-stop snapshots of 2026-10-18)

## Diagnosis

```bash
make tag-scan                    # the list; read-only
aws ec2 describe-tags --filters Name=resource-id,Values=<id> --query 'Tags[].[Key,Value]' --output text
aws cloudtrail lookup-events --lookup-attributes AttributeKey=ResourceName,AttributeValue=<id> --max-results 5
```

CloudTrail shows who created it and with which tool, which separates a hand-made resource from a job that forgot
a key.

## Remediation

Labelling only adds or corrects labels; it changes nothing else about the resource and is reversible.

- Pick the true values: `Env` from where it lives (its name, its VPC, the server it was attached to), `ManagedBy`
  from how it was made (`terraform`, `kaval-lambda`, `manual`). If you cannot tell, do not guess: leave it
  unlabelled, write it down in the journal, and ask.
- `aws ec2 create-tags --resources <id> --tags Key=Env,Value=<env> Key=ManagedBy,Value=<how>`
- If the cause is a job or module that forgot a key, fix that and add its rule to `scripts/ops/tag_policy.py`.
- A resource with no `Project` that is not Kaval's at all (the other project's): leave it. Its label is that
  project's to set.

## Do not

- Do not label something as Kaval's to make the scan pass. The scan's "left alone" count exists so another
  project's resources are not claimed.
- Do not delete an unlabelled resource because it is unlabelled. Unlabelled means "unknown", and a pre-stop snapshot
  may be the only copy of a database disk. Removal goes through the safe-removal steps (`KAV-95`).
- Do not remove `Project` from a resource to exempt it from the budget's hard stop.

## Escalation and ownership

- Owner: Roshan. There is no alarm to escalate from yet; the scan is run by hand and before each production bring-up.

## When it is fixed

`make tag-scan` prints `Missing or wrong labels: 0` and exits 0. Record the day in the journal.
