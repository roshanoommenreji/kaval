# Monitoring and patching for the database server (KAV-82, ADR-0050, the Phase 5 line that
# ADR-0008 deferred: "postgres_exporter + node_exporter -> Prometheus, disk-space alert; SSM Patch
# Manager schedule; connection and failed-auth logging").
#
# Everything here reaches the server through SSM State Manager, not through user_data. The
# instance has `ignore_changes = [user_data]` (a boot script must never silently replace a running
# database), so editing user_data would reach only a server that gets rebuilt, and production's
# will not be. An association reaches every server tagged for it, old or new:
#   - when it is created or changed,
#   - on its schedule,
#   - and whenever a stopped server comes back online after missing a run (State Manager's
#     documented behaviour), which is exactly the parked-most-of-the-time pattern here.
#
# Cost: Parameter Store (standard), SSM documents and associations, Patch Manager and the default
# patch baseline are all free. The two exporters use about 30 MiB of the server's 2 GiB.

locals {
  # Pinned by digest, from quay.io (no anonymous pull limit). Arm64 is in both manifests.
  node_exporter_image     = "quay.io/prometheus/node-exporter:v1.9.1@sha256:d00a542e409ee618a4edc67da14dd48c5da66726bbd5537ab2af9c1dfc442c8a"
  postgres_exporter_image = "quay.io/prometheuscommunity/postgres-exporter:v0.17.1@sha256:38606faa38c54787525fb0ff2fd6b41b4cfb75d455c1df294927c5f611699b17"
}

# The monitoring role's password. Deliberately NOT in `local.roles`: that list is also what the app
# node's instance role may read (outputs.tf), and the app node has no business with this one.
resource "random_password" "monitor" {
  length  = 32
  special = false
}

resource "aws_ssm_parameter" "monitor_password" {
  name  = "/kaval/${var.name_prefix}/db/monitor-password"
  type  = "SecureString"
  value = random_password.monitor.result

  tags = { Name = "${var.name_prefix}-db-monitor-password" }
}

# Let this server's own role read it (and only it, beyond what it could already read).
data "aws_iam_policy_document" "read_monitor_param" {
  statement {
    effect    = "Allow"
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.monitor_password.arn]
  }
}

resource "aws_iam_role_policy" "read_monitor_param" {
  name   = "read-monitor-param"
  role   = aws_iam_role.database.id
  policy = data.aws_iam_policy_document.read_monitor_param.json
}

# ── the document: the script, with its three settings on the first lines ──────────────────────
resource "aws_ssm_document" "monitoring" {
  name            = "${var.name_prefix}-database-monitoring"
  document_type   = "Command"
  document_format = "JSON"

  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Keeps the database server's exporters, monitoring role and logging in place (KAV-82)."
    mainSteps = [{
      action = "aws:runShellScript"
      name   = "monitoring"
      inputs = {
        timeoutSeconds = "900"
        runCommand = split("\n", join("\n", [
          "#!/bin/bash",
          "export NAME_PREFIX='${var.name_prefix}'",
          "export NODE_EXPORTER_IMAGE='${local.node_exporter_image}'",
          "export POSTGRES_EXPORTER_IMAGE='${local.postgres_exporter_image}'",
          file("${path.module}/db-monitoring.sh"),
        ]))
      }
    }]
  })

  tags = { Name = "${var.name_prefix}-database-monitoring" }
}

resource "aws_ssm_association" "monitoring" {
  name                = aws_ssm_document.monitoring.name
  association_name    = "${var.name_prefix}-database-monitoring"
  schedule_expression = "cron(0 18 * * ? *)" # daily, as well as on creation and whenever a stopped server returns

  targets {
    key    = "tag:Name"
    values = ["${var.name_prefix}-database"]
  }
}

# ── patching ──────────────────────────────────────────────────────────────────────────────────
# AWS's own Run Command document and the default patch baseline for Amazon Linux 2023. Install,
# NO reboot: the server is stopped and started every night in the normal posture, and a start is
# a boot, so a kernel update takes effect at the next start without anything restarting Postgres
# in the middle of a working session. Weekly; a server that was stopped at the time is patched when
# it next comes online.
resource "aws_ssm_association" "patching" {
  name                = "AWS-RunPatchBaseline"
  association_name    = "${var.name_prefix}-database-patching"
  schedule_expression = "cron(0 19 ? * SUN *)"

  parameters = {
    Operation    = "Install"
    RebootOption = "NoReboot"
  }

  targets {
    key    = "tag:Name"
    values = ["${var.name_prefix}-database"]
  }
}
