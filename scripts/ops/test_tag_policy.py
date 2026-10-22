"""The tag policy's offline rules (KAV-93, ADR-0061). Each rule is also run on a broken input, so a
rule that quietly stopped working would fail here instead of passing."""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import tag_policy as tp  # noqa: E402

GOOD_ENV = """
provider "aws" {
  default_tags {
    tags = {
      Project   = "kaval"
      ManagedBy = "terraform"
      Env       = "staging"
    }
  }
}
"""

GOOD_NODE = """
locals {
  required_tags = {
    Project   = "kaval"
    Env       = trimprefix(var.name_prefix, "kaval-")
    ManagedBy = "terraform"
  }
}
resource "aws_launch_template" "node" {
  tag_specifications {
    resource_type = "instance"
    tags          = merge(local.required_tags, { Name = var.name_prefix })
  }

  tag_specifications {
    resource_type = "volume"
    tags          = merge(local.required_tags, { Name = "x" })
  }
}
resource "aws_autoscaling_group" "node" {
  dynamic "tag" {
    for_each = local.required_tags
    content {
      key = tag.key
    }
  }
}
"""

GOOD_JOB = """
    snapshot = ec2.create_snapshot(
        TagSpecifications=[{
            "ResourceType": "snapshot",
            "Tags": [
                {"Key": "Project", "Value": "kaval"},
                {"Key": "Env", "Value": "x"},
                {"Key": "ManagedBy", "Value": "kaval-lambda"},
            ],
        }],
    )
"""


def test_the_real_repository_follows_the_policy() -> None:
    assert tp.repo_problems() == []


def test_a_good_environment_passes() -> None:
    assert tp.default_tags_problems("staging", GOOD_ENV) == []


def test_an_environment_with_a_missing_key_fails() -> None:
    broken = GOOD_ENV.replace('      Env       = "staging"\n', "")
    assert tp.default_tags_problems("staging", broken) == ["staging: default_tags has no Env"]


def test_an_environment_labelled_as_another_fails() -> None:
    problems = tp.default_tags_problems("prod", GOOD_ENV)  # a copy-pasted staging block
    assert any("Env is 'staging', must be 'prod'" in p for p in problems)


def test_an_environment_with_no_default_tags_fails() -> None:
    assert tp.default_tags_problems("dev", 'provider "aws" {}') != []


def test_a_commented_out_tag_block_does_not_count() -> None:
    commented = "\n".join("# " + line for line in GOOD_ENV.splitlines())
    assert tp.default_tags_problems("staging", commented) != []


def test_a_good_node_module_passes() -> None:
    assert tp.node_problems(GOOD_NODE) == []


def test_a_node_module_that_leaves_the_disk_unlabelled_fails() -> None:
    broken = GOOD_NODE.replace('resource_type = "volume"', 'resource_type = "network-interface"')
    want = ["node: the launch template does not tag the volume with required_tags"]
    assert tp.node_problems(broken) == want


def test_a_node_module_whose_group_has_only_a_name_fails() -> None:
    broken = GOOD_NODE.replace("local.required_tags\n    content", "{ Name = 1 }\n    content")
    assert any("auto-scaling group" in p for p in tp.node_problems(broken))


def test_a_node_module_missing_a_required_key_fails() -> None:
    broken = GOOD_NODE.replace('    ManagedBy = "terraform"\n', "")
    assert tp.node_problems(broken) == ["node: local required_tags has no ManagedBy"]


def test_a_good_job_passes() -> None:
    assert tp.job_problems("job", GOOD_JOB) == []


def test_a_job_that_leaves_out_a_key_fails() -> None:
    line = '                {"Key": "ManagedBy", "Value": "kaval-lambda"},\n'
    broken = GOOD_JOB.replace(line, "")
    assert tp.job_problems("job", broken) == ["job: its snapshot is not given ManagedBy"]


def test_a_job_that_tags_nothing_fails() -> None:
    assert tp.job_problems("job", "ec2.create_snapshot(VolumeId=v)") != []


def test_values_outside_the_allowed_lists_are_problems() -> None:
    ok = {"Project": "kaval", "Env": "prod", "ManagedBy": "terraform"}
    assert tp.tag_problems(ok) == []
    wrong = tp.tag_problems({**ok, "Env": "Prod"})
    assert wrong == ["Env=Prod is not one of prod, staging, dev, state"]
    assert tp.tag_problems({"Project": "kaval"}) == ["missing Env", "missing ManagedBy"]


def test_another_projects_resources_are_not_kavals_but_an_unlabelled_one_is() -> None:
    assert not tp.is_kaval({"Project": "stock-trader"})
    assert tp.is_kaval({"Name": "forgotten"})


_TAGS = "{Key=Project,Value=kaval},{Key=Env,Value='\"${E}\"'},{Key=ManagedBy,Value=manual}"
GOOD_SCRIPT = f"""aws ec2 create-snapshot --volume-id v \\
  --tag-specifications 'ResourceType=snapshot,Tags=[{{Key=Name,Value=x}},{_TAGS}]' \\
  --query SnapshotId
"""


def test_a_good_script_passes() -> None:
    assert tp.script_problems("s.sh", GOOD_SCRIPT) == []


def test_a_script_that_leaves_out_a_key_fails() -> None:
    for key in ("Env", "ManagedBy", "Project"):
        broken = re.sub(rf"\{{Key={key},Value=[^}}]*\}},?", "", GOOD_SCRIPT)
        assert tp.script_problems("s.sh", broken) == [f"s.sh: its snapshot is not given {key}"], key


def test_a_script_that_tags_nothing_fails() -> None:
    assert tp.script_problems("s.sh", "aws ec2 create-snapshot --volume-id v") != []


def test_a_script_that_no_longer_snapshots_is_reported_not_passed() -> None:
    assert tp.script_problems("s.sh", "echo hello") != []
