#!/usr/bin/env bash
# Month-to-date AWS spend against the Kaval ceiling.
#
#   ./cost-report.sh            print the report
#   ./cost-report.sh --assert   exit 1 if MTD exceeds the ceiling (for CI)
#
# Cost Explorer has roughly 24 hours of lag and bills $0.01 per API call.
# Running this a few times a day is fine; running it in a loop is not.

set -euo pipefail

PROFILE="${AWS_PROFILE:-kaval}"
CEILING="${BUDGET_MONTHLY_USD:-40}"
ASSERT=false
[[ "${1:-}" == "--assert" ]] && ASSERT=true

command -v jq >/dev/null 2>&1 || { echo "jq not installed — see docs/labs/lab-00-toolchain.md"; exit 1; }

START=$(date -u +%Y-%m-01)
END=$(date -u -d "$(date -u +%Y-%m-01) +1 month" +%Y-%m-%d 2>/dev/null \
      || date -u -v+1m -j -f %Y-%m-%d "$START" +%Y-%m-%d)

RESULT=$(aws ce get-cost-and-usage \
  --profile "$PROFILE" \
  --time-period "Start=$START,End=$END" \
  --granularity MONTHLY \
  --metrics UnblendedCost \
  --group-by Type=DIMENSION,Key=SERVICE \
  --output json)

TOTAL=$(echo "$RESULT" | jq -r '
  [.ResultsByTime[0].Groups[].Metrics.UnblendedCost.Amount | tonumber] | add // 0')

printf '\n  Kaval — cost report\n'
printf '  %s to today\n\n' "$START"

echo "$RESULT" | jq -r '
  .ResultsByTime[0].Groups[]
  | select((.Metrics.UnblendedCost.Amount | tonumber) > 0.005)
  | "  \(.Keys[0])|\(.Metrics.UnblendedCost.Amount | tonumber | .*100 | round / 100)"' \
  | sort -t'|' -k2 -rn \
  | awk -F'|' '{ printf "  %-42s $%6.2f\n", $1, $2 }'

PCT=$(awk -v t="$TOTAL" -v c="$CEILING" 'BEGIN { printf "%.0f", (t/c)*100 }')
BARS=$(awk -v p="$PCT" 'BEGIN { n=int(p/5); print (n>20?20:n) }')

printf '\n  %-42s $%6.2f\n' "TOTAL" "$TOTAL"
printf '  ceiling %-34s $%6.2f\n' "" "$CEILING"
printf '  ['
for ((i=0; i<20; i++)); do [[ $i -lt $BARS ]] && printf '#' || printf '.'; done
printf ']  %s%%\n\n' "$PCT"

OVER=$(awk -v t="$TOTAL" -v c="$CEILING" 'BEGIN { print (t>c) ? 1 : 0 }')

if [[ "$OVER" == "1" ]]; then
  echo "  OVER CEILING. Check for an orphaned NAT gateway, load balancer, or EKS cluster."
  echo "  See docs/cost/budget-plan.md."
  $ASSERT && exit 1
elif [[ "$PCT" -gt 70 ]]; then
  echo "  Above 70% of ceiling with time left in the month. Worth a look."
fi

echo "  On the 1st, record the month just finished: docs/cost/actuals/$(date -u -d "$(date -u +%Y-%m-01) -1 day" +%Y-%m).md"
echo "  (the dashboard lists it as a blocker until the file exists)"
echo ""
