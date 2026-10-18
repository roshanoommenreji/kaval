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

# KAV-79: the memory-limit fix starts at ask, at any confidence. Nothing is born auto, and the
# type must not trip a never keyword by accident ("increase_memory_limit" contains none).
test_increase_memory_limit_is_ask if {
	policy.decision == "ask" with input as {
		"action": {"type": "increase_memory_limit", "target": "x/y", "blast_radius": "deployment", "reversible": true},
		"confidence": 0.99,
	}
}

# KAV-80: the rollback starts at ask, at any confidence. "rollback_deployment" contains no never
# keyword either; a rollback changes a Deployment's template, it deletes nothing.
test_rollback_deployment_is_ask if {
	policy.decision == "ask" with input as {
		"action": {"type": "rollback_deployment", "target": "x/y", "blast_radius": "deployment", "reversible": true},
		"confidence": 0.99,
	}
}

test_rollback_deployment_is_ask_even_when_unsure if {
	policy.decision == "ask" with input as {
		"action": {"type": "rollback_deployment", "target": "x/y", "blast_radius": "deployment", "reversible": true},
		"confidence": 0.1,
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

# ── KAV-81: incidents that are for a person ──────────────────────────────────────────────────
# An action attached to a disk-full or slowness incident (or to Kaval's own health) is never,
# whatever the action is and however sure the model sounds. Outside those causes nothing changes.

test_restart_pod_for_a_full_disk_is_never if {
	policy.decision == "never" with input as {
		"action": {"type": "restart_pod", "target": "x/y", "blast_radius": "pod", "reversible": true},
		"confidence": 0.99, "cause": "disk_pressure",
	}
}

test_any_action_for_a_slow_service_is_never if {
	policy.decision == "never" with input as {
		"action": {"type": "increase_memory_limit", "target": "x/y", "blast_radius": "deployment", "reversible": true},
		"confidence": 0.99, "cause": "latency_high",
	}
}

test_rollback_for_kaval_own_health_is_never if {
	policy.decision == "never" with input as {
		"action": {"type": "rollback_deployment", "target": "x/y", "blast_radius": "deployment", "reversible": true},
		"confidence": 0.99, "cause": "kaval_loop_stalled",
	}
}

test_a_promotion_cannot_resurrect_a_people_only_cause if {
	promotions := [{
		"action_type": "restart_pod", "blast_radius": "pod",
		"requires_reversible": true, "min_confidence": 0.9,
		"adr": "docs/adr/0000-example-promotion.md",
	}]
	policy.decision == "never" with input as {
		"action": {"type": "restart_pod", "target": "x/y", "blast_radius": "pod", "reversible": true},
		"confidence": 0.99, "cause": "disk_pressure",
	} with data.auto_promotions as promotions
}

# KAV-94 / KAV-95: the waste scan's findings are for a person. Two kinds (a loose disk, an old backup
# copy) can be removed, but only as `ask`: a person approves every step, and nothing is `auto`.

test_removing_an_old_backup_copy_is_asked_never_automatic if {
	policy.decision == "ask" with input as {
		"action": {"type": "delete_old_snapshot", "target": "snap-0abc", "blast_radius": "resource", "reversible": false},
		"confidence": 1.0, "cause": "snapshot_past_retention",
	}
}

test_removing_a_loose_disk_is_asked_in_both_steps if {
	policy.decision == "ask" with input as {
		"action": {"type": "backup_and_mark_volume", "target": "vol-0abc", "blast_radius": "resource", "reversible": true},
		"confidence": 1.0, "cause": "idle_volume",
	}
	policy.decision == "ask" with input as {
		"action": {"type": "delete_idle_volume", "target": "vol-0abc", "blast_radius": "resource", "reversible": false},
		"confidence": 1.0, "cause": "idle_volume",
	}
}

test_removal_is_never_automatic_even_with_a_promotion_for_its_type if {
	promotions := [{
		"action_type": "delete_idle_volume", "blast_radius": "resource",
		"requires_reversible": false, "min_confidence": 0.5,
		"adr": "docs/adr/0000-example-promotion.md",
	}]
	policy.decision != "auto" with input as {
		"action": {"type": "restart_pod", "target": "x/y", "blast_radius": "pod", "reversible": true},
		"confidence": 1.0, "cause": "server_overstay",
	} with data.auto_promotions as promotions
}

test_a_failed_removal_is_for_a_person_never_a_fix if {
	policy.decision == "never" with input as {
		"action": {"type": "delete_idle_volume", "target": "vol-0abc", "blast_radius": "resource", "reversible": false},
		"confidence": 1.0, "cause": "removal_failed",
	}
}

test_stopping_an_overstaying_server_is_never_for_now if {
	policy.decision == "never" with input as {
		"action": {"type": "stop_server", "target": "i-0abc", "blast_radius": "pod", "reversible": true},
		"confidence": 0.99, "cause": "server_overstay",
	}
}

test_releasing_an_idle_address_and_labelling_are_never_for_now if {
	policy.decision == "never" with input as {
		"action": {"type": "restart_pod", "target": "x/y", "blast_radius": "pod", "reversible": true},
		"confidence": 0.99, "cause": "idle_address",
	}
	policy.decision == "never" with input as {
		"action": {"type": "restart_pod", "target": "x/y", "blast_radius": "pod", "reversible": true},
		"confidence": 0.99, "cause": "resource_unlabelled",
	}
}

test_other_causes_are_unchanged if {
	policy.decision == "ask" with input as {
		"action": {"type": "restart_pod", "target": "x/y", "blast_radius": "pod", "reversible": true},
		"confidence": 0.9, "cause": "oom_killed",
	}
}

test_no_cause_is_unchanged if {
	policy.decision == "ask" with input as {
		"action": {"type": "restart_pod", "target": "x/y", "blast_radius": "pod", "reversible": true},
		"confidence": 0.9,
	}
}
