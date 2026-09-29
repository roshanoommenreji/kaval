# Kaval's action policy (KAV-42, ADR-0017). Evaluated by `opa eval` against one action at a
# time; see policy/README.md for the design and services/agent/kaval_agent/policy.py for the
# caller. Tested by policy_test.rego (`opa test policy/ -v`, also `make test`).
#
# input shape:
#   {
#     "action":     {"type": "restart_pod", "target": "...", "blast_radius": "pod", "reversible": true},
#     "confidence": 0.92
#   }
# output: data.policy.decision — one of "auto", "ask", "never".

package policy

import rego.v1

default decision := "ask"

# ── never — action type or blast radius, regardless of confidence (ADR-0006 rule 1) ────────
#
# Keyword match, not exact match: the model writes `action.type` as free text (schema.py has
# no enum for it), so "delete_pvc", "remove_persistentvolumeclaim" and "delete_pv_claim" must
# all be caught. A false positive here costs an extra human question; a false negative costs
# an unrefused destructive action, so matching is deliberately loose in the safer direction.

never_type_keywords := {"pvc", "persistentvolumeclaim", "namespace", "iam", "ec2", "billing"}

is_never if {
	some keyword in never_type_keywords
	regex.match(sprintf("(?i)%s", [keyword]), input.action.type)
}

# Defence in depth: blast_radius=account is the widest radius the schema allows (schema.py's
# BlastRadiusValue) and covers billing/account-wide actions the keyword list might miss,
# whatever the action's type string says.
is_never if {
	input.action.blast_radius == "account"
}

decision := "never" if is_never

# ── auto — nothing is born here (policy/README.md: "Everything starts in ask. Nothing is
# born auto.") `data.auto_promotions` (policy/promotions.json's own top-level key — a JSON
# data file loaded from the bundle root merges its keys directly under `data`, with no
# "promotions" segment from the filename) starts empty and stays empty until an ADR cites
# `outcome` rows and adds an entry (ADR-0006 rule 2: Jev/the model can only make a class
# stricter, never promote ask to auto — a promotion is a deliberate, separate act, not
# something confidence alone can trigger). Until an entry exists, this rule cannot fire for
# any input, which is the property policy_test.rego proves.

decision := "auto" if {
	not is_never
	some promotion in data.auto_promotions
	input.action.type == promotion.action_type
	input.action.blast_radius == promotion.blast_radius
	input.action.reversible == promotion.requires_reversible
	input.confidence > promotion.min_confidence
}
