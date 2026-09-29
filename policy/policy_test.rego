# Unit tests for policy.rego (KAV-42). `opa test policy/ -v`.

package policy_test

import data.policy
import rego.v1

# ── default: ask ─────────────────────────────────────────────────────────────────────────

test_ordinary_action_is_ask if {
	policy.decision == "ask" with input as {
		"action": {"type": "scale_deployment", "target": "x", "blast_radius": "deployment", "reversible": true},
		"confidence": 0.7,
	}
}

# ── never: action type keywords, case-insensitive ───────────────────────────────────────

test_never_delete_pvc if {
	policy.decision == "never" with input as {
		"action": {"type": "delete_pvc", "target": "x", "blast_radius": "pod", "reversible": false},
		"confidence": 0.5,
	}
}

test_never_matches_case_insensitively if {
	policy.decision == "never" with input as {
		"action": {"type": "Remove_PersistentVolumeClaim", "target": "x", "blast_radius": "pod", "reversible": true},
		"confidence": 0.99,
	}
}

test_never_mutate_iam if {
	policy.decision == "never" with input as {
		"action": {"type": "attach_iam_policy", "target": "x", "blast_radius": "account", "reversible": true},
		"confidence": 0.99,
	}
}

test_never_terminate_ec2 if {
	policy.decision == "never" with input as {
		"action": {"type": "terminate_ec2_instance", "target": "x", "blast_radius": "node", "reversible": false},
		"confidence": 0.5,
	}
}

test_never_touch_billing if {
	policy.decision == "never" with input as {
		"action": {"type": "modify_billing_alert", "target": "x", "blast_radius": "account", "reversible": true},
		"confidence": 0.5,
	}
}

test_never_delete_namespace if {
	policy.decision == "never" with input as {
		"action": {"type": "delete_namespace", "target": "x", "blast_radius": "namespace", "reversible": false},
		"confidence": 0.5,
	}
}

# ── never: blast_radius=account, defence in depth even for an innocuous-looking type ────────

test_never_account_blast_radius_regardless_of_type if {
	policy.decision == "never" with input as {
		"action": {"type": "restart_pod", "target": "x", "blast_radius": "account", "reversible": true},
		"confidence": 0.99,
	}
}

# ── auto: nothing is born here ──────────────────────────────────────────────────────────

test_auto_does_not_fire_with_empty_promotions if {
	# The best possible case for auto — pod, reversible, near-certain — still lands on ask
	# while policy/promotions.json is empty. This is the test that proves "nothing is born
	# auto": no confidence, however high, can produce "auto" without a promotion entry.
	policy.decision == "ask" with input as {
		"action": {"type": "restart_pod", "target": "x", "blast_radius": "pod", "reversible": true},
		"confidence": 0.999,
	} with data.auto_promotions as []
}

test_auto_fires_once_promoted if {
	promotions := [{
		"action_type": "restart_pod", "blast_radius": "pod",
		"requires_reversible": true, "min_confidence": 0.9,
		"adr": "docs/adr/0000-example-promotion.md",
	}]
	policy.decision == "auto" with input as {
		"action": {"type": "restart_pod", "target": "x", "blast_radius": "pod", "reversible": true},
		"confidence": 0.95,
	} with data.auto_promotions as promotions
}

test_promoted_type_below_min_confidence_stays_ask if {
	promotions := [{
		"action_type": "restart_pod", "blast_radius": "pod",
		"requires_reversible": true, "min_confidence": 0.9,
		"adr": "docs/adr/0000-example-promotion.md",
	}]
	policy.decision == "ask" with input as {
		"action": {"type": "restart_pod", "target": "x", "blast_radius": "pod", "reversible": true},
		"confidence": 0.85,
	} with data.auto_promotions as promotions
}

test_promoted_type_wrong_blast_radius_stays_ask if {
	promotions := [{
		"action_type": "restart_pod", "blast_radius": "pod",
		"requires_reversible": true, "min_confidence": 0.9,
		"adr": "docs/adr/0000-example-promotion.md",
	}]
	policy.decision == "ask" with input as {
		"action": {"type": "restart_pod", "target": "x", "blast_radius": "deployment", "reversible": true},
		"confidence": 0.95,
	} with data.auto_promotions as promotions
}

test_promoted_type_irreversible_stays_ask if {
	promotions := [{
		"action_type": "restart_pod", "blast_radius": "pod",
		"requires_reversible": true, "min_confidence": 0.9,
		"adr": "docs/adr/0000-example-promotion.md",
	}]
	policy.decision == "ask" with input as {
		"action": {"type": "restart_pod", "target": "x", "blast_radius": "pod", "reversible": false},
		"confidence": 0.95,
	} with data.auto_promotions as promotions
}

test_never_overrides_a_matching_promotion if {
	# A promotion can never resurrect a never-class type. Jev/the model/a promotion entry can
	# only make a class stricter, never looser (ADR-0006 rule 2).
	promotions := [{
		"action_type": "delete_pvc", "blast_radius": "pod",
		"requires_reversible": true, "min_confidence": 0.9,
		"adr": "docs/adr/0000-example-promotion.md",
	}]
	policy.decision == "never" with input as {
		"action": {"type": "delete_pvc", "target": "x", "blast_radius": "pod", "reversible": true},
		"confidence": 0.99,
	} with data.auto_promotions as promotions
}
