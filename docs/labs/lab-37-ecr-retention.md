# Lab 37 — ECR keeps the newest fifteen images, and `make up` checks prod's images exist

**Phase:** 4 · **Story:** `KAV-68` · **Cost:** it *removes* an open-ended cost. Storage is capped at about
$0.71 a month (it is about $0.09 today). Decisions: [ADR-0036](../adr/0036-ecr-keeps-the-newest-fifteen-tagged-images.md).

[Lab 19](lab-19-aws-landing.md) created the repositories with a rule that kept every
tagged image forever. This lab limits that, and adds a check so that limiting it cannot leave a parked prod
pointing at an image that no longer exists.

**Result:** applied to AWS and read back (section 5); `infra/modules/ecr` has a second lifecycle rule (keep the newest 15 `sha-*` images per
repository); an ECR *preview* of the rule on the real gateway repository named exactly the older image;
`make up` runs `promote.py preflight` first and, against the real registry, reports that prod's four images
exist; the Terraform plan is five policy replacements and nothing else; 147 release tests pass.

---

## 1. The rule

In `infra/modules/ecr/main.tf`, next to the existing "expire untagged after 7 days":

```hcl
{
  rulePriority = 2
  description  = "keep the newest ${var.keep_tagged_images} tagged images"
  selection = {
    tagStatus     = "tagged"
    tagPrefixList = ["sha-"]
    countType     = "imageCountMoreThan"
    countNumber   = var.keep_tagged_images      # default 15, must be at least 5
  }
  action = { type = "expire" }
}
```

"Newest" means by push time. ECR runs lifecycle rules about once a day.

## 2. Try the rule without deleting anything

ECR can show what a rule *would* do. Here it is with a limit of one, so the existing gateway images show it:

```bash
export AWS_PROFILE=kaval
cat > keep1.json <<'EOF'
{"rules":[{"rulePriority":2,"description":"keep newest 1","selection":{"tagStatus":"tagged","tagPrefixList":["sha-"],"countType":"imageCountMoreThan","countNumber":1},"action":{"type":"expire"}}]}
EOF
aws ecr start-lifecycle-policy-preview --repository-name kaval/gateway --lifecycle-policy-text file://keep1.json
sleep 8
aws ecr get-lifecycle-policy-preview --repository-name kaval/gateway \
  --query '{status:status,wouldExpire:previewResults[].[imageTags[0],action.type]}'
```

Result on 2026-10-10: status `COMPLETE`, would expire `sha-ffb436b` (pushed 2026-10-02), and not
`sha-2459417` (the newest, and the one prod pins). Nothing was deleted; a preview never deletes.

## 3. The check in `make up`

```bash
python scripts/release/promote.py preflight
#   PASS  prod's pinned images are still in ECR: all four images tagged sha-2459417 exist
```

It reads the four tags in prod's two files and asks ECR for each. If one is gone it prints `FAIL`, the
reason, and "promote a newer tag that passed staging (promote.yml) before starting prod", and exits 1.
`make up` runs it as its first line, before it says "this starts billing", so it fails before anything costs
money. Two tests in `test_promote.py` cover the pass and the fail with a stand-in for ECR.

## 4. The plan

```bash
cd infra/envs/prod && terraform plan
# Plan: 5 to add, 0 to change, 5 to destroy.
#   module.ecr.aws_ecr_lifecycle_policy.this["agent"|"backup"|"collector"|"executor"|"gateway"] must be replaced
```

Replacing a lifecycle policy means deleting and recreating the *policy*, which holds only the rules; the
repositories and the images are not in the plan. Only those five resources appear, the parked-prod node
settings are untouched, and `terraform validate` and `terraform fmt -check` pass.

## 5. The apply (2026-10-10, approved by Roshan)

```bash
cd infra/envs/prod && terraform apply     # Apply complete! Resources: 5 added, 0 changed, 5 destroyed.
aws ecr get-lifecycle-policy --repository-name kaval/gateway   # rule 1 untagged/7, rule 2 tagged/15
```

All five repositories read back with both rules; a second `terraform plan` said "No changes"; `preflight` still
passed afterwards, so no image was touched.

## 6. What went wrong

Nothing broke, but one thing nearly went into the design: I first thought of protecting prod's image by giving
it a second tag such as `keep-sha-2459417`. ECR has no "keep" action and no exclusion, so an image that also
has a `sha-` tag still matches the `sha-` rule. The answer is a large enough number plus the pre-flight.

## What this does not do

- No image has been expired yet: every repository holds two or three against a limit of 15. The first real
  expiry is when a sixteenth is pushed, so the live expiry is unproven; the preview is the evidence.
- The pre-flight checks the four deployed images only (not `kaval/backup`).
- A rollback can reach only versions still in ECR.

## Reproduce

1. Run section 2 against any repository with two or more `sha-*` images.
2. `python scripts/release/promote.py preflight` (AWS profile `kaval`).
3. `terraform plan` in `infra/envs/prod` and read that it replaces five policies and touches nothing else.
4. `pytest scripts/release -q` (147 tests).
