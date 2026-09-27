# Lab 03 — The AWS dev server

**Phase:** 1 · **Time:** ~1.5 hrs · **Cost:** $0.0224/hr while running, ~$5/month at 20 hrs/week
(paid from AWS credit; see [ADR-0007](../adr/0007-develop-on-an-aws-dev-server.md))

One small ARM server in Mumbai that runs the Phase 1–3 stack instead of the laptop. It has **no
open ports** and **turns itself off** after an idle hour. The laptop stays the editor: VS Code,
Git and Claude Code don't move. Only the heavy work (Docker, the AI model, the database) runs
on the server.

---

## Why this order

The project was planned to use the laptop until Phase 4. The switch happened because the model
choice (Phase 2) depends on memory and speed measured on the machine prod will actually use: a
4 GB Graviton `t4g.medium`. It also means every image is built natively as arm64 from day one.
The full reasoning, the real prices, and the rejected options are in ADR-0007.

Two rules shaped the build:

- **Nothing on the internet can connect to it.** The firewall has zero inbound rules. You reach
  it through **AWS Systems Manager Session Manager**. The server dials *out* to AWS, and your
  connection rides that link. SSH works *inside* that tunnel, so there's no port 22 to attack.
- **It stops itself.** A forgotten server is the classic cloud bill. A timer on the server
  checks every 5 minutes for anyone connected, and shuts it down after 60 minutes of nobody.
  Shutdown is set to **stop** (the disk is kept), never **terminate**.

---

## Prerequisites

- [Lab 01](lab-01-aws-guardrails.md): the `kaval` AWS profile and the budget guardrails
- Terraform, the AWS CLI and Git, from [Lab 00](lab-00-toolchain.md)
- **The AWS account on the paid plan** (Step 1)

---

## Step 1 — Check the account can run this server size

AWS's Free Plan (for accounts created after July 2025) only allows free-tier sizes. The first
real apply was refused:

```
api error InvalidParameterCombination: The specified instance type is not eligible for Free Tier.
```

Two things worth knowing:

- **A `--dry-run` launch passes anyway.** It checks permissions, not Free Plan eligibility.
  Only the real launch tells you.
- **Nothing was lost by trying.** The four free pieces (firewall, role, permission, profile) were
  created, and the refused server simply didn't exist.

Before upgrading, check **Billing → Credits**. This account had **$140** ($100 Free Tier, plus
$20 each for the Lambda and Budgets work in Phase 0), **$0 used**, expiring **2027-09-11**. Then
go to **Billing → Free Tier → Upgrade plan**.

- There's no fee, and the credits carry over.
- **It can't be undone.** Afterwards, usage beyond the credit bills the card instead of stopping
  the account. From then on, Kaval's own guardrails are the brake.

---

## Step 2 — A dedicated SSH key, outside the repo

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/kaval-devbox -C kaval-devbox
cp infra/envs/dev/terraform.tfvars.example infra/envs/dev/terraform.tfvars
# paste the contents of ~/.ssh/kaval-devbox.pub into ssh_public_key
```

`terraform.tfvars` is gitignored. The private key never leaves `~/.ssh/`. The public half is
what the server trusts.

---

## Step 3 — Plan, review, apply

```bash
make devbox-create          # terraform init + apply; it shows the plan and asks first
```

Five resources, and only the last one costs anything:

| Resource | What it's for |
|---|---|
| Security group `kaval-devbox` | The firewall. **No inbound rules**; all outbound allowed (packages, model downloads, SSM) |
| IAM role `kaval-devbox` | The server's identity |
| Policy attachment | Its one permission, `AmazonSSMManagedInstanceCore`, which lets Session Manager reach it |
| Instance profile | Hands that role to the server |
| EC2 `kaval-devbox` | `t4g.medium`, Amazon Linux 2023 arm64, 30 GB encrypted gp3, IMDSv2 required, default VPC |

On first boot, cloud-init (`infra/modules/devbox/user_data.sh.tftpl`) installs Docker, the
Compose and Buildx plugins (Amazon Linux's Docker package ships without them), Git, Make and
Python 3.11. It also installs your SSH key and the idle-stop timer. Allow about 3 minutes.

**Where to see it in the console:** EC2 → Instances → `kaval-devbox`. Systems Manager → Fleet
Manager shows it as a *managed node* once it's online.

---

## Step 4 — Connect through Session Manager

Install the Session Manager plugin, which the AWS CLI needs for `start-session`:

```powershell
winget install --id Amazon.SessionManagerPlugin -e
```

Open a **new** terminal afterwards, since the installer adds itself to `PATH`. Then add this to
`~/.ssh/config`:

```
Host kaval-devbox
  HostName i-xxxxxxxxxxxxxxxxx          # terraform -chdir=infra/envs/dev output -raw instance_id
  User ec2-user
  IdentityFile ~/.ssh/kaval-devbox
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
  ServerAliveInterval 60
  ProxyCommand aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters portNumber=%p --profile kaval --region ap-south-1
```

```bash
make devbox-ssh             # or: ssh kaval-devbox
```

The `HostName` is the **instance ID**, not an address. The `ProxyCommand` asks Session Manager
to open a tunnel to that instance's own port 22, and SSH runs through it. The instance ID never
changes across stop and start, so this file never needs editing.

Checked on the first connection: `aarch64`, 2 CPUs, 3,830 MB RAM, Docker 25.0.14, Compose
v5.5.1, Buildx v0.37.1, Python 3.11.16, idle timer active.

---

## Step 5 — Drive the server's Docker from the laptop

```bash
docker context create kaval-devbox --docker "host=ssh://kaval-devbox"
docker --context kaval-devbox ps
```

Every `docker` command with `--context kaval-devbox` (or after
`docker context use kaval-devbox`) runs on the server. Docker Desktop doesn't need to be running
on the laptop. Only its command-line tool is used.

---

## Step 6 — Daily use

```bash
make devbox-up        # start it and wait until Session Manager sees it (~1 min)
make devbox-status    # running or stopped, and since when
make devbox-down      # stop it now
```

If you forget `devbox-down`, it stops itself 60 minutes after the last connection closes.
Stopped, the only charge is the disk: 30 GB × $0.0912 ≈ $2.74/month.

---

## What broke, and why it's worth knowing

### The idle stop could never have fired: systemd's `%s`

The boot-time reset was a systemd unit running `date +%s > /var/lib/kaval/last-active`. The
first check on the live server found the file contained the text **`/bin/bash`**.

systemd treats `%` sequences in unit files as *specifiers*, and `%s` means "the user's shell". So
`date +%s` became `date +/bin/bash`, which prints `/bin/bash`. The idle script then did arithmetic
on that, failed on every run, and never stopped anything. **The server would have billed all
month, while looking protected.**

The fix is to write `%%s` in the unit file (systemd's escape for a literal `%`). Terraform's
template engine leaves `%%s` alone; I rendered the template and checked the output rather than
assume. The script also got a guard: anything that isn't a plain number restarts the clock
instead of crashing the check.

### A Terraform warning inside a variable

With no server yet, `terraform output -raw instance_id` prints its **"No outputs found"** warning
on *standard output*, not standard error. The Makefile's `2>/dev/null` didn't hide it, so the
server-ID variable filled up with warning text. The Makefile now keeps only text that matches
`i-` plus hex characters.

### Dead SSH connections kept the server "busy" (found 2026-09-27, `KAV-23`)

Through Session Manager, the SSH daemon's TCP peer is the local SSM worker, not the laptop, and
that worker stays up after the laptop's `ssh` exits. So each `docker --context` connection lingered
for SSM's 20-minute idle timeout: 42 open within 20 minutes of `compose` commands, new handshakes
timing out, **and the idle stop counting them as someone working**. The first-boot script now
writes `ClientAliveInterval 30` / `ClientAliveCountMax 3` to
`/etc/ssh/sshd_config.d/10-kaval-keepalive.conf`: the daemon probes each client and drops one that
doesn't answer in ~90 seconds. On a server built before this, apply the same file with
`aws ssm send-command` and `systemctl reload sshd`, as
[Lab 05](lab-05-gateway-api-and-synthetic-signals.md#troubleshooting) describes.

### "Online" didn't mean "reachable" (found 2026-09-27, `KAV-23`)

`make devbox-up` used to report ready as soon as Session Manager said the server was `Online`.
Right after a start, that status can still be the one from before the stop. The first
`make dev` then connected too early: the server's SSH log shows no connection at all, `ssh` gave
up after 30 seconds, and the SSM plugin it left behind kept Docker's pipe open, so `compose`
hung with no error. `devbox-up` now finishes with a real SSH login, retried every 5 seconds for
up to 2 minutes. A cold `make dev` from a stopped server took 102 s afterwards.

---

## Step 7 — Prove the idle stop, don't assume it

Set the limit to 2 minutes temporarily, disconnect, and watch from outside:

```bash
ssh kaval-devbox 'sudo sed -i "s/limit=\$((60 \* 60))/limit=\$((2 * 60))/" /usr/local/sbin/kaval-idle-stop'
# disconnect, then from the laptop:
aws ec2 describe-instances --instance-ids <id> --query 'Reservations[0].Instances[0].State.Name'
```

What happened on 2026-09-26, polling every ~30 seconds from the laptop:

| Time (UTC) | Event |
|---|---|
| 14:03:48 | Last SSH session closed, limit set to 2 minutes |
| 14:03:57 → 14:10:38 | `running` |
| 14:11:11 | **`stopped`**, about 7 minutes after disconnecting |

The wait is the 2-minute limit plus up to 5 minutes until the timer's next check, plus the
shutdown itself. The server's own log from that boot (`journalctl -b -1 -t kaval-idle`) reads:

```
idle for 417 s, stopping the instance
```

Then the other half:

```bash
make devbox-up        # 30 s from stopped to Session Manager "Online"
```

After boot, `/var/lib/kaval/last-active` held a timestamp **3 seconds after boot**, so the
reset works and the server doesn't stop itself the moment it starts. Then the limit went back
to 60 minutes, and `docker --context kaval-devbox run --rm hello-world` from the laptop printed
`Hello from Docker!`, pulling the `arm64v8` image.

---

## Done when

- `terraform apply` created 5 resources, with a plan reviewed first
- The security group has **no inbound rules**, and SSH works only through Session Manager
- The server stopped itself after being left idle, observed from outside
- It started again with `make devbox-up`, and **didn't** stop immediately (the boot reset works)
- `docker --context kaval-devbox` runs commands on the server from the laptop
- The idle limit is back to 60 minutes

## Related

- [ADR-0007](../adr/0007-develop-on-an-aws-dev-server.md): why, the prices, the rejected options
- [docs/cost/budget-plan.md](../cost/budget-plan.md): dev server costs and the $140 credit
- `infra/modules/devbox/`: the Terraform and first-boot script
