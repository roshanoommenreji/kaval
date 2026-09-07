#!/usr/bin/env bash
# Scaffold a lab document and today's journal entry.
#
#   ./new-lab.sh setup-flux
#
# Documentation is a merge gate here, not an afterthought. This exists to make
# the gate cheap enough that it never becomes the reason a session ends undone.

set -euo pipefail

SLUG="${1:?usage: new-lab.sh <slug>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TODAY=$(date -u +%Y-%m-%d)

NEXT=$(find "$ROOT/docs/labs" -name 'lab-*.md' 2>/dev/null \
  | sed -E 's/.*lab-([0-9]+)-.*/\1/' | sort -n | tail -1)
NEXT=$(printf '%02d' $(( 10#${NEXT:-0} + 1 )))

LAB="$ROOT/docs/labs/lab-$NEXT-$SLUG.md"
JOURNAL="$ROOT/docs/journal/$TODAY.md"
TITLE=$(echo "$SLUG" | tr '-' ' ' | awk '{ $1=toupper(substr($1,1,1)) substr($1,2); print }')

if [[ -e "$LAB" ]]; then
  echo "already exists: $LAB"
  exit 1
fi

cat > "$LAB" <<EOF
# Lab $NEXT — $TITLE

**Phase:** ? · **Time:** ? · **Cost:** \$?

One paragraph: what this lab achieves and why it comes at this point.

---

## Prerequisites

- Lab ?? complete

---

## Step 1 —

\`\`\`bash

\`\`\`

---

## Done when

- [ ]
- [ ] Journal entry appended
- [ ] Jira story moved to Done

---

## What to write down

The parts worth keeping for the course — the arguments and the surprises, not the keystrokes.

-
EOF

if [[ ! -e "$JOURNAL" ]]; then
  cat > "$JOURNAL" <<EOF
# $TODAY

**Phase:** ?

## Done

-

## Learned

-

## Open threads

-

## Next

-

## Cost

No change.
EOF
fi

echo "  lab:     docs/labs/lab-$NEXT-$SLUG.md"
echo "  journal: docs/journal/$TODAY.md"
echo ""
echo "Create the matching Jira story in project KAV before starting."
