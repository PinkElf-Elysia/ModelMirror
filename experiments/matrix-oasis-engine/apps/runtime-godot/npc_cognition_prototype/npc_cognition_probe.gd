extends SceneTree

const MARKER := "R22_NPC_COGNITION_GODOT_PROBE_OK"
const SCENE := preload("res://npc_cognition_prototype/npc_cognition_lab.tscn")
const BASE_SCRIPT_PATH := "res://npc_authority_prototype/npc_authority_lab.gd"
const NEUTRAL_INTENT_JSON := "{\"actionId\":\"action-one\",\"actorEntityId\":\"actor-one\",\"canonicalization\":\"matrix-oasis.canonical-json/1\",\"format\":\"matrix-oasis.npc-intent\",\"formatVersion\":\"0.1.0\",\"id\":\"intent-one\",\"nodeId\":\"node-one\",\"observed\":{\"headSha256\":null,\"revision\":0,\"runtimeSnapshotSha256\":\"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"},\"timelineId\":\"timeline-one\"}"
const NEUTRAL_NODE_COMMAND_SHA256 := "sha256:a311600aadbfc918215466e3a18c31edcf2b2ced4411cbcd71d5a05d2f0cd5df"
const NEUTRAL_NODE_EVIDENCE_SHA256 := "sha256:6471d6b9d1482c47574891e4126edef73e8d363e64adf0d0ede476cbc6a7eacc"
const SERIALIZER_MARKER := "R22_GODOT_SERIALIZER_NORMALIZATION_OK"


func _init() -> void:
	_run.call_deferred()


func _run() -> void:
	var lab := SCENE.instantiate()
	if not (lab is Node3D):
		_fail("R22_GODOT_PROBE_ROOT_INVALID")
		return
	var script := lab.get_script() as Script
	var base_script := script.get_base_script() if script != null else null
	if base_script == null or base_script.resource_path != BASE_SCRIPT_PATH:
		_fail("R22_GODOT_PROBE_INHERITANCE_INVALID")
		return
	if not lab.has_method("_accept_adjudication_response") or not lab.has_method("_accept_mirror_response"):
		_fail("R22_GODOT_PROBE_R20_COMPOSITION_INVALID")
		return
	if not _check_ui(lab) or not _check_closed_helpers(lab):
		return
	if not _check_authority_permit(lab):
		return
	if not _check_resume_permit():
		return
	if not _check_live_physical_evidence(lab):
		return
	if not _check_neutral_command_serializer(lab):
		return
	lab.free()
	print(SERIALIZER_MARKER)
	print(MARKER)
	quit(0)


func _check_ui(lab: Node) -> bool:
	var backdrop := lab.get_node_or_null("CognitionHud/Backdrop") as Control
	var panel := lab.get_node_or_null("CognitionHud/Backdrop/Margin/Panel") as PanelContainer
	var player_text := lab.get_node_or_null("CognitionHud/Backdrop/Margin/Panel/VBox/InputSection/PlayerText") as TextEdit
	var disclosure_scroll := lab.get_node_or_null("CognitionHud/Backdrop/Margin/Panel/VBox/ApprovalSection/DisclosureScroll") as ScrollContainer
	var disclosure := lab.get_node_or_null("CognitionHud/Backdrop/Margin/Panel/VBox/ApprovalSection/DisclosureScroll/Disclosure") as Label
	var dialogue_scroll := lab.get_node_or_null("CognitionHud/Backdrop/Margin/Panel/VBox/DialogueSection/DialogueScroll") as ScrollContainer
	var dialogue := lab.get_node_or_null("CognitionHud/Backdrop/Margin/Panel/VBox/DialogueSection/DialogueScroll/Dialogue") as Label
	if backdrop == null or panel == null or player_text == null or disclosure_scroll == null or disclosure == null or dialogue_scroll == null or dialogue == null:
		_fail("R22_GODOT_PROBE_UI_MISSING")
		return false
	if backdrop.anchor_right != 1.0 or backdrop.anchor_bottom != 1.0 or panel.custom_minimum_size.x > 608.0 or panel.custom_minimum_size.y > 516.0:
		_fail("R22_GODOT_PROBE_RESPONSIVE_LAYOUT_INVALID")
		return false
	if disclosure.autowrap_mode == TextServer.AUTOWRAP_OFF or dialogue.autowrap_mode == TextServer.AUTOWRAP_OFF:
		_fail("R22_GODOT_PROBE_PLAIN_TEXT_WRAP_INVALID")
		return false
	if disclosure_scroll.horizontal_scroll_mode != ScrollContainer.SCROLL_MODE_DISABLED or dialogue_scroll.horizontal_scroll_mode != ScrollContainer.SCROLL_MODE_DISABLED:
		_fail("R22_GODOT_PROBE_SCROLL_INVALID")
		return false
	return true


func _check_authority_permit(lab: Node) -> bool:
	if lab.get("_permit_next_authority_command") != false:
		_fail("R22_GODOT_PROBE_INITIAL_COMMAND_PERMITTED")
		return false
	lab._arm_one_authority_command()
	if not lab._consume_authority_command_permit():
		_fail("R22_GODOT_PROBE_COMMAND_NOT_PERMITTED")
		return false
	if lab._consume_authority_command_permit():
		_fail("R22_GODOT_PROBE_MULTIPLE_COMMANDS_PERMITTED")
		return false
	lab._arm_one_authority_command()
	lab._return_to_initial_waiting_state()
	if lab.get("_permit_next_authority_command") != false or lab.get("_authority_state") != "idle":
		_fail("R22_GODOT_PROBE_RESET_WAITING_STATE_INVALID")
		return false
	return true


func _check_resume_permit() -> bool:
	var fresh := SCENE.instantiate()
	if not fresh._configure_resume_queued_action(""):
		_fail("R22_GODOT_PROBE_FRESH_RESUME_CONFIG_INVALID")
		return false
	fresh._consume_resume_queued_action_at_ready_boundary()
	if fresh._consume_authority_command_permit():
		_fail("R22_GODOT_PROBE_FRESH_COMMAND_PERMITTED")
		return false
	fresh.free()
	var resumed := SCENE.instantiate()
	if not resumed._configure_resume_queued_action("1"):
		_fail("R22_GODOT_PROBE_RESUME_CONFIG_INVALID")
		return false
	resumed._consume_resume_queued_action_at_ready_boundary()
	if not resumed._consume_authority_command_permit():
		_fail("R22_GODOT_PROBE_RESUME_COMMAND_NOT_PERMITTED")
		return false
	resumed._consume_resume_queued_action_at_ready_boundary()
	resumed._return_to_initial_waiting_state()
	resumed._consume_resume_queued_action_at_ready_boundary()
	if resumed._consume_authority_command_permit():
		_fail("R22_GODOT_PROBE_RESUME_COMMAND_REARMED")
		return false
	resumed.free()
	var invalid := SCENE.instantiate()
	if invalid._configure_resume_queued_action("true"):
		_fail("R22_GODOT_PROBE_INVALID_RESUME_FLAG_ACCEPTED")
		return false
	invalid.free()
	return true


func _check_live_physical_evidence(lab: Node) -> bool:
	var digest := "sha256:" + "a".repeat(64)
	var intent := {"actionId": "action-one", "actorEntityId": "actor-one", "canonicalization": "matrix-oasis.canonical-json/1", "format": "matrix-oasis.npc-intent", "formatVersion": "0.1.0", "id": "intent-one", "nodeId": "node-one", "observed": {"headSha256": null, "revision": 0, "runtimeSnapshotSha256": digest}, "timelineId": "timeline-one"}
	var command := {"actionId": "action-one", "actorEntityId": "actor-one", "intentId": "intent-one", "nodeId": "node-one", "npcIntentJson": JSON.stringify(intent, "", true), "ruleIndex": 0, "sequence": 1}
	var arrival := {"capsuleVerified": true, "domainVerified": true, "floorVerified": true, "movementTicks": 20, "pathComplete": true, "pathLengthMm": 1000}
	var trace: Array = [{"actionId": "action-one", "actorEntityId": "actor-one", "arrivalEvidence": arrival, "sequence": 1, "state": "arrived"}, {"actionId": "action-one", "actorEntityId": "actor-one", "afterSnapshotSha256": digest, "beforeSnapshotSha256": digest, "decision": "accepted", "sequence": 1, "state": "mirrored"}]
	var frames_299: Array[int] = []
	frames_299.resize(299)
	frames_299.fill(16667)
	var returned := {"kind": "walked-home", "physicsProcessing": false, "positionErrorMm": 0, "returnedHome": true}
	if not lab._exact(command, ["actionId", "actorEntityId", "intentId", "nodeId", "npcIntentJson", "ruleIndex", "sequence"]):
		_fail("R22_GODOT_PROBE_PHYSICAL_COMMAND_INVALID")
		return false
	if not lab._is_sha256(digest) or not lab._valid_turn_id(intent["timelineId"]):
		_fail("R22_GODOT_PROBE_PHYSICAL_IDENTITY_INVALID")
		return false
	if not lab._exact(returned, ["kind", "physicsProcessing", "positionErrorMm", "returnedHome"]):
		_fail("R22_GODOT_PROBE_PHYSICAL_RETURN_INVALID")
		return false
	if not lab._build_live_physical_evidence(command, trace, frames_299, digest, returned).is_empty():
		_fail("R22_GODOT_PROBE_PHYSICAL_EVIDENCE_EARLY")
		return false
	var frames_300: Array[int] = []
	frames_300.resize(300)
	frames_300.fill(16667)
	var evidence: Dictionary = lab._build_live_physical_evidence(command, trace, frames_300, digest, returned)
	if evidence.is_empty():
		_fail("R22_GODOT_PROBE_PHYSICAL_EVIDENCE_EMPTY")
		return false
	if evidence.get("timelineId") != "timeline-one" or evidence.get("performance", {}).get("sampleCount") != 300 or evidence.get("performance", {}).get("frameMicros", []).size() != 300 or evidence.get("movement", {}).get("return", {}).get("kind") != "walked-home" or typeof(JSON.parse_string(evidence.get("canonicalR20Trace", ""))) != TYPE_ARRAY:
		_fail("R22_GODOT_PROBE_PHYSICAL_EVIDENCE_INVALID")
		return false
	var encoded := JSON.stringify(evidence, "", true)
	for forbidden in ["playerText", "dialogueText", "providerRequestJson", "responseId"]:
		if encoded.contains(forbidden):
			_fail("R22_GODOT_PROBE_PHYSICAL_EVIDENCE_PRIVATE")
			return false
	lab._begin_live_physical_evidence(command)
	if not lab._live_physical_evidence_pending():
		_fail("R22_GODOT_PROBE_PHYSICAL_PENDING_GATE_INVALID")
		return false
	lab._live_physical_frames = frames_300
	lab._live_physical_cycle_complete = true
	lab._live_physical_return_evidence = returned
	lab._reset_live_physical_evidence()
	if lab._live_physical_evidence_pending() or lab.get("_live_physical_sampling") != false or not lab.get("_live_physical_frames").is_empty() or lab.get("_live_physical_evidence_emitted") != false:
		_fail("R22_GODOT_PROBE_PHYSICAL_RESET_INVALID")
		return false
	return true


func _check_neutral_command_serializer(lab: Node) -> bool:
	var command_json := "{\"actionId\":\"action-one\",\"actorEntityId\":\"actor-one\",\"intentId\":\"intent-one\",\"nodeId\":\"node-one\",\"npcIntentJson\":" + JSON.stringify(NEUTRAL_INTENT_JSON) + ",\"ruleIndex\":0,\"sequence\":1}"
	var parsed: Variant = JSON.parse_string(command_json)
	if typeof(parsed) != TYPE_DICTIONARY or typeof(parsed.get("sequence")) != TYPE_FLOAT or typeof(parsed.get("ruleIndex")) != TYPE_FLOAT:
		_fail("R22_GODOT_PROBE_NEUTRAL_NUMBER_TYPE_UNEXPECTED")
		return false
	var original: Dictionary = parsed.duplicate(true)
	var digest := "sha256:" + "a".repeat(64)
	var arrival := {"capsuleVerified": true, "domainVerified": true, "floorVerified": true, "movementTicks": 20, "pathComplete": true, "pathLengthMm": 1000}
	var trace: Array = [{"actionId": "action-one", "actorEntityId": "actor-one", "arrivalEvidence": arrival, "sequence": 1, "state": "arrived"}, {"actionId": "action-one", "actorEntityId": "actor-one", "afterSnapshotSha256": digest, "beforeSnapshotSha256": digest, "decision": "accepted", "sequence": 1, "state": "mirrored"}]
	var frames: Array[int] = []
	frames.resize(300)
	frames.fill(16667)
	var returned := {"kind": "walked-home", "physicsProcessing": false, "positionErrorMm": 0, "returnedHome": true}
	var evidence: Dictionary = lab._build_live_physical_evidence(parsed, trace, frames, digest, returned)
	if evidence.is_empty() or evidence.get("command", {}).get("commandSha256") != NEUTRAL_NODE_COMMAND_SHA256 or typeof(evidence.get("command", {}).get("sequence")) != TYPE_INT or typeof(evidence.get("command", {}).get("ruleIndex")) != TYPE_INT:
		_fail("R22_GODOT_PROBE_NEUTRAL_PRODUCTION_CANONICALIZATION_INVALID")
		return false
	var canonical_evidence := JSON.stringify(evidence, "", true)
	if "sha256:" + canonical_evidence.sha256_text() != NEUTRAL_NODE_EVIDENCE_SHA256:
		_fail("R22_GODOT_PROBE_NEUTRAL_MARKER_BYTES_INVALID")
		return false
	if parsed != original or typeof(parsed["sequence"]) != TYPE_FLOAT or typeof(parsed["ruleIndex"]) != TYPE_FLOAT:
		_fail("R22_GODOT_PROBE_NEUTRAL_INPUT_MUTATED")
		return false
	for invalid in [{"sequence": 1.5}, {"sequence": NAN}, {"sequence": 10001.0}, {"ruleIndex": 256.0}, {"ruleIndex": -1.0}]:
		var rejected: Dictionary = parsed.duplicate(true)
		for key in invalid:
			rejected[key] = invalid[key]
		if not lab._build_live_physical_evidence(rejected, trace, frames, digest, returned).is_empty():
			_fail("R22_GODOT_PROBE_NEUTRAL_INTEGER_BOUNDARY_ACCEPTED")
			return false
	return true


func _check_closed_helpers(lab: Node) -> bool:
	var digest := "sha256:" + "a".repeat(64)
	if not lab._valid_turn_id("turn-abc123") or lab._valid_turn_id("-turn") or lab._valid_turn_id("1turn") or lab._valid_turn_id("turn-") or lab._valid_turn_id("turn--id") or lab._valid_turn_id("Turn") or lab._valid_turn_id("turn%2fescape") or lab._valid_turn_id("turn/path") or lab._valid_turn_id("turn?query"):
		_fail("R22_GODOT_PROBE_STATUS_PATH_INVALID")
		return false
	if not lab._valid_cognition_route("cognition/status/turn-abc123", HTTPClient.METHOD_GET) or lab._valid_cognition_route("cognition/status/turn%2fescape", HTTPClient.METHOD_GET):
		_fail("R22_GODOT_PROBE_STATUS_ROUTE_INVALID")
		return false
	if not lab._valid_cognition_route("cognition/displayed", HTTPClient.METHOD_POST) or lab._valid_cognition_route("cognition/displayed", HTTPClient.METHOD_GET):
		_fail("R22_GODOT_PROBE_DISPLAY_ROUTE_INVALID")
		return false
	var approval: Dictionary = lab._approval_body("turn-abc123", digest)
	if not lab._exact(approval, ["approvalHash", "turnId"]) or approval["turnId"] != "turn-abc123" or approval["approvalHash"] != digest:
		_fail("R22_GODOT_PROBE_APPROVAL_BODY_INVALID")
		return false
	var displayed: Dictionary = lab._display_ack_body("turn-abc123", digest)
	if not lab._exact(displayed, ["displayAckHash", "turnId"]) or displayed["turnId"] != "turn-abc123" or displayed["displayAckHash"] != digest:
		_fail("R22_GODOT_PROBE_DISPLAY_BODY_INVALID")
		return false
	if not lab._valid_dialogue_text("[url=file:///tmp]literal[/url]\n<script>alert(1)</script>"):
		_fail("R22_GODOT_PROBE_PLAIN_TEXT_REJECTED")
		return false
	if lab._valid_dialogue_text("unsafe" + String.chr(0x2028)) or lab._valid_dialogue_text("unsafe" + String.chr(0x2029)) or lab._valid_dialogue_text("1\n2\n3\n4\n5\n6\n7\n8\n9"):
		_fail("R22_GODOT_PROBE_DIALOGUE_LIMIT_INVALID")
		return false
	if lab._valid_player_text("tab\tcontrol"):
		_fail("R22_GODOT_PROBE_PLAYER_CONTROL_ACCEPTED")
		return false
	var disclosure := {
		"endpoint": "https://api.openai.com/v1/responses",
		"maxCostMicrousd": 10000,
		"maxOutputTokens": 512,
		"model": "gpt-5.6-luna",
		"priceLock": {
			"cacheWriteInputMicrousdPerMillionTokens": 250000,
			"cachedInputMicrousdPerMillionTokens": 20000,
			"inputMicrousdPerMillionTokens": 200000,
			"outputMicrousdPerMillionTokens": 1200000,
		},
		"providerRequestJson": "{\"input\":\"exact\"}",
		"requestLimit": 1,
		"retention": {
			"abuseMonitoringMaxDays": 30,
			"promptCachingPossible": true,
			"store": false,
			"zeroDataRetentionClaimed": false,
		},
		"retryLimit": 0,
	}
	if not lab._valid_disclosure(disclosure):
		_fail("R22_GODOT_PROBE_DISCLOSURE_INVALID")
		return false
	var rendered: String = lab._disclosure_text(disclosure)
	for expected in [
		disclosure["providerRequestJson"], disclosure["model"], disclosure["endpoint"],
		"Maximum cost: 10000 microusd", "Maximum output: 512 tokens", "Request limit: 1", "Retry limit: 0",
		"input 200000", "cached input 20000", "cache-write input 250000", "output 1200000",
		"abuseMonitoringMaxDays", "promptCachingPossible", "zeroDataRetentionClaimed",
	]:
		if not rendered.contains(expected):
			_fail("R22_GODOT_PROBE_DISCLOSURE_INCOMPLETE")
			return false
	var drifted: Dictionary = disclosure.duplicate(true)
	drifted["retryLimit"] = 1
	if lab._valid_disclosure(drifted):
		_fail("R22_GODOT_PROBE_DISCLOSURE_RETRY_DRIFT_ACCEPTED")
		return false
	drifted = disclosure.duplicate(true)
	drifted["priceLock"]["outputMicrousdPerMillionTokens"] = 1200001
	if lab._valid_disclosure(drifted):
		_fail("R22_GODOT_PROBE_DISCLOSURE_PRICE_DRIFT_ACCEPTED")
		return false
	drifted = disclosure.duplicate(true)
	drifted["unexpected"] = true
	if lab._valid_disclosure(drifted):
		_fail("R22_GODOT_PROBE_DISCLOSURE_UNKNOWN_FIELD_ACCEPTED")
		return false
	drifted = disclosure.duplicate(true)
	drifted["retention"]["zeroDataRetentionClaimed"] = true
	if lab._valid_disclosure(drifted):
		_fail("R22_GODOT_PROBE_DISCLOSURE_RETENTION_DRIFT_ACCEPTED")
		return false
	drifted = disclosure.duplicate(true)
	drifted.erase("requestLimit")
	if lab._valid_disclosure(drifted):
		_fail("R22_GODOT_PROBE_DISCLOSURE_INCOMPLETE")
		return false
	return true


func _fail(code: String) -> void:
	printerr(code)
	quit(2)
