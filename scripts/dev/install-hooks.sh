#!/usr/bin/env bash
# Install the pre-commit hook. Run once after cloning.
#
#   ./scripts/dev/install-hooks.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOOK="$ROOT/.git/hooks/pre-commit"

[[ -d "$ROOT/.git" ]] || { echo "not a git repository — run 'git init' first"; exit 1; }

cat > "$HOOK" <<'EOF'
#!/usr/bin/env bash
# Kaval pre-commit — block secrets before they enter history.
#
# This repository goes public at v1. A secret committed today and removed
# tomorrow is still in the history, and history rewrites are painful. The
# only cheap moment to catch it is here.

set -euo pipefail

if command -v gitleaks >/dev/null 2>&1; then
  # gitleaks v8.20+ moved the working scan logic to `git`/`dir`/`stdin`.
  # `protect`/`detect` still parse flags and exit 0 without erroring, but
  # they no longer scan anything — a silent no-op, not a missing-tool
  # warning. `git --staged` is the real equivalent of the old `protect --staged`.
  if ! gitleaks git --staged --redact --config .gitleaks.toml --no-banner; then
    echo ""
    echo "  Commit blocked: a possible secret is staged."
    echo ""
    echo "  If it is genuinely a false positive, add it to the allowlist in"
    echo "  .gitleaks.toml with a comment explaining why. Do not use --no-verify."
    echo ""
    exit 1
  fi
else
  echo "  WARNING: gitleaks not installed — commit NOT scanned."
  echo "  Install it: scoop install gitleaks   (see docs/labs/lab-00-toolchain.md)"
fi

# Belt and braces: catch a real 12-digit account ID that slipped past the rules.
# The boundaries exclude hex letters too, so a digit run inside a sha256 (uv.lock, image
# digests) doesn't count; an account ID sits between ':' '"' or spaces (KAV-24).
if git diff --cached -U0 | grep -nE '^\+(.*[^0-9a-fA-F])?[0-9]{12}([^0-9a-fA-F]|$)' \
     | grep -vE '000000000000' >/dev/null 2>&1; then
  echo ""
  echo "  A 12-digit number is being committed. If that is an AWS account ID,"
  echo "  replace it with a variable or the 000000000000 placeholder."
  echo ""
  read -p "  Continue anyway? [y/N] " ok < /dev/tty
  [[ "$ok" == "y" ]] || exit 1
fi
EOF

chmod +x "$HOOK"
echo "installed: .git/hooks/pre-commit"

command -v gitleaks >/dev/null 2>&1 \
  || echo "NOTE: gitleaks is not installed yet — the hook will warn but not scan."
