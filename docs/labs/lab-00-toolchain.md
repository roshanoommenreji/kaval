# Lab 00 — Toolchain

**Phase:** 0 · **Time:** ~30 min · **Cost:** $0

Get the machine ready. Nothing here touches AWS.

---

## Already present

Checked on 2026-08-22:

| Tool | Version |
|---|---|
| Docker | 28.0.4 *(daemon not running — start Docker Desktop)* |
| Terraform | 1.11.4 |
| kubectl | installed |
| helm | installed |
| Node | 24.15.0 |
| npm | 11.12.1 |
| Python | 3.11.9 |
| git | 2.49.0 |
| gh | 2.90.0 |
| java | 17.0.19 |
| **ollama** | installed — Phase 1 is closer than expected |
| aws | 2.26.2 *(credentials expired — see Lab 01)* |

## Missing

| Tool | Needed from | What for |
|---|---|---|
| `make` | now | Every command in this repo |
| `jq` | now | JSON in shell scripts, cost reporting |
| `gitleaks` | now | Pre-commit secret scanning |
| `opa` | Phase 2 | Policy engine tests |
| `k3d` | Phase 3 | Local Kubernetes |
| `flux` | Phase 4 | GitOps reconciliation |

---

## Install

Scoop carries all six cleanly on Windows and does not need admin rights. If you already use
winget or Chocolatey, they work too — but **search before installing**, because package IDs
change and a guessed ID installs the wrong thing:

```powershell
winget search make
choco search gitleaks
```

### Scoop path (recommended)

```powershell
# one-time, if you don't have scoop
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
Invoke-RestMethod -Uri https://get.scoop.sh | Invoke-Expression

# needed now
scoop install make jq gitleaks

# needed later — install when you reach the phase, not before
scoop bucket add extras
scoop install opa k3d flux
```

### Verify

```bash
for t in make jq gitleaks docker terraform kubectl helm node python ollama; do
  printf "%-12s " "$t"; command -v $t >/dev/null 2>&1 && echo ok || echo MISSING
done
```

All should read `ok`. Then:

```bash
cd "c:/Users/rosha/Desktop/Claude/AWS AI"
make help
```

If `make help` prints the target list, the repo is wired up correctly.

---

## Start Docker

Docker is installed but the daemon is not running. Launch Docker Desktop and confirm:

```bash
docker info --format '{{.ServerVersion}} / {{.OSType}}'
```

Expect a version and `linux`. Nothing in Phase 1 works without this.

---

## About `make` on Windows

`make` is not a Windows-native tool and Git Bash does not bundle it. Once installed via scoop it
works fine from Git Bash, which is what this repo assumes (`SHELL := /bin/bash` at the top of the
Makefile). Run make targets from **Git Bash**, not PowerShell — PowerShell will choke on the
shell syntax inside the recipes.

---

## Notes

Record anything that surprised you here, then append a line to today's journal.

- [ ] All six tools installed and verified
- [ ] Docker daemon running
- [ ] `make help` prints targets
- [ ] Journal entry appended
