# The token that lets a person (or the game-day script) record an approval through the gateway's
# API (KAV-115, ADR-0073). Generated once and never typed: the node's bootstrap script reads it
# from Parameter Store on every boot and writes the Secret `kaval-approver` in the cluster, the
# same way it does for the database passwords and the Slack tokens (ADR-0027).
#
# Read it yourself with `aws ssm get-parameter --with-decryption`, never through an assistant.
# Rotating it is `terraform apply -replace=module.approver_token.random_password.token`, then
# letting the node's next bootstrap run (or its 6-hour timer) rewrite the Secret and restarting the
# gateway: docs/runbooks/approver-token.md.
#
# The value is also in Terraform's state. That is the same position as the database passwords, and
# the state lives in a private, versioned, encrypted bucket (ADR-0058).

resource "random_password" "token" {
  length  = 43
  special = false # goes into an HTTP header and a shell variable; letters and digits are enough
}

resource "aws_ssm_parameter" "token" {
  name  = "/kaval/${var.name_prefix}/approver/token"
  type  = "SecureString"
  value = random_password.token.result

  tags = { Name = "${var.name_prefix}-approver-token" }
}
