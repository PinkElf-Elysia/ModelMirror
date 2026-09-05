extends "res://npc_authority_prototype/npc_authority_lab.gd"

const R22_READY_MARKER := "MATRIX_OASIS_R22_COGNITION_PREVIEW_READY"
const R22_TRACE_MARKER := "MATRIX_OASIS_R22_COGNITION_TRACE_JSON:"
const R22_LIVE_PHYSICAL_EVIDENCE_MARKER := "R22_LIVE_PHYSICAL_EVIDENCE_JSON:"
const R22_LOOPBACK_BASE := "http://127.0.0.1:43122/v1/"
const RESUME_QUEUED_ACTION_ENV := "MATRIX_OASIS_R22_RESUME_QUEUED_ACTION"
const MAX_LOCAL_BODY_BYTES := 65536
const MAX_PLAYER_TEXT_BYTES := 4096
const MAX_DIALOGUE_BYTES := 2048
const MAX_DIALOGUE_LINES := 8
const MAX_STATUS_POLLS := 320
const STATUS_POLL_SECONDS := 0.1
const STATUS_POLL_TIMEOUT_MSEC := 35000
const PREPARATION_TIMEOUT_MSEC := 65000
const COGNITION_LEASE_TIMEOUT_MSEC := 300000
const OPENAI_RESPONSES_ENDPOINT := "https://api.openai.com/v1/responses"
const OPENAI_COGNITION_MODEL := "gpt-5.6-luna"
const MAX_CALL_COST_MICROUSD := 10000
const MAX_OUTPUT_TOKENS := 512
const PROVIDER_REQUEST_LIMIT := 1
const PROVIDER_RETRY_LIMIT := 0
const INPUT_MICROUSD_PER_MILLION_TOKENS := 200000
const CACHED_INPUT_MICROUSD_PER_MILLION_TOKENS := 20000
const CACHE_WRITE_INPUT_MICROUSD_PER_MILLION_TOKENS := 250000
const OUTPUT_MICROUSD_PER_MILLION_TOKENS := 1200000
const ENVIRONMENT_AND_NPC_COLLISION_MASK := 3
const INTERACTION_DISTANCE_METERS := 3.0
const LIVE_PHYSICAL_SAMPLE_COUNT := 300
const MAX_LIVE_PHYSICAL_EVIDENCE_BYTES := 32768
const FIXED_FALLBACK_TEXT := "The NPC cannot answer right now. Deterministic behavior remains available."
const BUSY_PROMPT := "NPC is busy"
const IDLE_PROMPT := "E · Talk to NPC"
const SEARCH_PROMPT := "Aim at a visible idle NPC"

@onready var _cognition_request: HTTPRequest = $CognitionRequest
@onready var _modal: Control = $CognitionHud/Backdrop
@onready var _modal_title: Label = $CognitionHud/Backdrop/Margin/Panel/VBox/Title
@onready var _modal_status: Label = $CognitionHud/Backdrop/Margin/Panel/VBox/Status
@onready var _input_section: VBoxContainer = $CognitionHud/Backdrop/Margin/Panel/VBox/InputSection
@onready var _player_text: TextEdit = $CognitionHud/Backdrop/Margin/Panel/VBox/InputSection/PlayerText
@onready var _submit_button: Button = $CognitionHud/Backdrop/Margin/Panel/VBox/InputSection/InputActions/Submit
@onready var _cancel_button: Button = $CognitionHud/Backdrop/Margin/Panel/VBox/InputSection/InputActions/Cancel
@onready var _approval_section: VBoxContainer = $CognitionHud/Backdrop/Margin/Panel/VBox/ApprovalSection
@onready var _disclosure_label: Label = $CognitionHud/Backdrop/Margin/Panel/VBox/ApprovalSection/DisclosureScroll/Disclosure
@onready var _approve_button: Button = $CognitionHud/Backdrop/Margin/Panel/VBox/ApprovalSection/ApprovalActions/Approve
@onready var _decline_button: Button = $CognitionHud/Backdrop/Margin/Panel/VBox/ApprovalSection/ApprovalActions/Decline
@onready var _dialogue_section: VBoxContainer = $CognitionHud/Backdrop/Margin/Panel/VBox/DialogueSection
@onready var _dialogue_label: Label = $CognitionHud/Backdrop/Margin/Panel/VBox/DialogueSection/DialogueScroll/Dialogue
@onready var _close_button: Button = $CognitionHud/Backdrop/Margin/Panel/VBox/DialogueSection/Close

var _authority_state := "starting"
var _cognition_state := "closed"
var _cognition_pending_route := ""
var _interaction_actor_id := ""
var _look_actor_id := ""
var _turn_actor_id := ""
var _turn_id := ""
var _approval_hash := ""
var _display_ack_hash := ""
var _pending_dialogue_text := ""
var _pending_dialogue_action_queued := false
var _lease_held := false
var _lease_started_msec := 0
var _planning_started_msec := 0
var _resume_authority_on_close := false
var _status_poll_generation := 0
var _status_poll_count := 0
var _status_poll_started_msec := 0
var _r22_ready_printed := false
var _permit_next_authority_command := false
var _resume_queued_action_requested := false
var _resume_ready_boundary_consumed := false
var _live_physical_command: Dictionary = {}
var _live_physical_frames: Array[int] = []
var _live_physical_sampling := false
var _live_physical_cycle_complete := false
var _live_physical_return_evidence: Dictionary = {}
var _live_physical_evidence_emitted := false
var _live_physical_previous_frame_usec := 0
var _player_was_enabled := true
var _previous_mouse_mode := Input.MOUSE_MODE_CAPTURED


func _ready() -> void:
	if not _configure_resume_queued_action(OS.get_environment(RESUME_QUEUED_ACTION_ENV)):
		_fail("R22_GODOT_RESUME_FLAG_INVALID")
		return
	_cognition_request.request_completed.connect(_on_cognition_request_completed)
	_submit_button.pressed.connect(_submit_player_text)
	_cancel_button.pressed.connect(_cancel_input)
	_approve_button.pressed.connect(_approve_turn)
	_decline_button.pressed.connect(_decline_turn)
	_close_button.pressed.connect(_close_dialogue)
	_set_modal_state("closed")
	super._ready()


func _process(delta: float) -> void:
	super._process(delta)
	_sample_live_physical_evidence()
	if (
		_cognition_state == "planning"
		and _planning_started_msec > 0
		and Time.get_ticks_msec() - _planning_started_msec >= PREPARATION_TIMEOUT_MSEC
	):
		_cognition_request.cancel_request()
		_cognition_pending_route = ""
		_show_fallback("R22_GODOT_PREPARATION_TIMEOUT")
		return
	if (
		_lease_held
		and _modal.visible
		and _cognition_state in ["input", "approval"]
		and _lease_started_msec > 0
		and Time.get_ticks_msec() - _lease_started_msec >= COGNITION_LEASE_TIMEOUT_MSEC
	):
		if _cognition_state == "approval":
			_decline_turn()
		else:
			_close_modal(false)


func _physics_process(_delta: float) -> void:
	_refresh_interaction_target()


func _input(event: InputEvent) -> void:
	if not _modal.visible or not (event is InputEventKey) or not event.pressed or event.echo:
		return
	if _is_escape(event):
		match _cognition_state:
			"input": _cancel_input()
			"approval": _decline_turn()
			"dialogue", "fallback": _close_dialogue()
		get_viewport().set_input_as_handled()
		return
	if _is_enter(event):
		match _cognition_state:
			"input":
				if event.ctrl_pressed:
					_submit_player_text()
			"approval": _approve_turn()
			"dialogue", "fallback": _close_dialogue()
		get_viewport().set_input_as_handled()


func _unhandled_input(event: InputEvent) -> void:
	if _modal.visible:
		get_viewport().set_input_as_handled()
		return
	if _is_physical_key(event, KEY_E):
		get_viewport().set_input_as_handled()
		if not _interaction_actor_id.is_empty():
			_open_input(_interaction_actor_id)
		elif not _look_actor_id.is_empty() and _scene_lab != null:
			_scene_lab.prompt_label.text = BUSY_PROMPT
		return
	super._unhandled_input(event)


func _request_command() -> void:
	if not _r22_ready_printed and _scene_lab != null:
		_r22_ready_printed = true
		print(R22_READY_MARKER)
		_consume_resume_queued_action_at_ready_boundary()
	if _live_physical_sampling and not _live_physical_command.is_empty() and _active_command.is_empty():
		_live_physical_return_evidence = _current_live_physical_return_evidence()
		if _live_physical_return_evidence.is_empty():
			_fail("R22_LIVE_PHYSICAL_RETURN_EVIDENCE_INVALID")
			return
		_live_physical_cycle_complete = true
		_maybe_emit_live_physical_evidence()
	if _lease_held or not _consume_authority_command_permit():
		_authority_state = "idle"
		return
	_authority_state = "checking"
	super._request_command()


func _arm_one_authority_command() -> void:
	_permit_next_authority_command = true


func _configure_resume_queued_action(value: String) -> bool:
	if _resume_ready_boundary_consumed or value not in ["", "1"]:
		return false
	_resume_queued_action_requested = value == "1"
	return true


func _consume_resume_queued_action_at_ready_boundary() -> void:
	if _resume_ready_boundary_consumed:
		return
	_resume_ready_boundary_consumed = true
	if _resume_queued_action_requested:
		_arm_one_authority_command()
	_resume_queued_action_requested = false


func _consume_authority_command_permit() -> bool:
	if not _permit_next_authority_command:
		return false
	_permit_next_authority_command = false
	return true


func _return_to_initial_waiting_state() -> void:
	_permit_next_authority_command = false
	_authority_state = "idle"


func _start_request(route: String, method: HTTPClient.Method, body: String) -> void:
	if _failed or not _pending_route.is_empty() or route not in ["command", "arrived", "mirror", "reset", "verify"]:
		_fail("R20_GODOT_REQUEST_STATE_INVALID")
		return
	_pending_route = route
	var headers := PackedStringArray(["Authorization: Bearer " + _session_token])
	if method == HTTPClient.METHOD_POST:
		headers.append("Content-Type: application/json")
	var error := _request.request(R22_LOOPBACK_BASE + route, headers, method, body)
	if error != OK:
		_pending_route = ""
		_fail("R20_GODOT_REQUEST_FAILED")


func _accept_command_response(response: Dictionary) -> void:
	var status: Variant = response.get("status")
	if status in ["quiescent", "ended"]:
		_authority_state = status
	elif status == "command":
		_authority_state = "busy"
	super._accept_command_response(response)
	if status == "command" and not _failed and not _active_command.is_empty():
		_begin_live_physical_evidence(_active_command)


func _accept_reset_response(response: Dictionary) -> void:
	_clear_cognition_transient()
	_reset_live_physical_evidence()
	_return_to_initial_waiting_state()
	super._accept_reset_response(response)


func _refresh_interaction_target() -> void:
	_interaction_actor_id = ""
	_look_actor_id = ""
	if _modal.visible or _failed or _scene_lab == null or _scene_lab.player == null or _scene_lab.player.camera == null:
		return
	var player := _scene_lab.player as MatrixOasisFirstPersonController
	var camera := player.camera
	var origin := camera.global_position
	var target := origin - camera.global_basis.z.normalized() * INTERACTION_DISTANCE_METERS
	var query := PhysicsRayQueryParameters3D.create(origin, target)
	# Environment layer 1 must participate so an NPC capsule on layer 2 cannot
	# be selected through a wall or other final collider.
	query.collision_mask = ENVIRONMENT_AND_NPC_COLLISION_MASK
	query.collide_with_bodies = true
	query.collide_with_areas = false
	query.exclude = [player.get_rid()]
	var hit := get_world_3d().direct_space_state.intersect_ray(query)
	var collider: Variant = hit.get("collider")
	if not (collider is MatrixOasisNpcActorController):
		_update_world_prompt()
		return
	var actor := collider as MatrixOasisNpcActorController
	if not _actor_is_visible_here(actor):
		_update_world_prompt()
		return
	_look_actor_id = actor.actor_entity_id
	if _actor_is_interactable(actor):
		_interaction_actor_id = actor.actor_entity_id
	_update_world_prompt()


func _actor_is_visible_here(actor: MatrixOasisNpcActorController) -> bool:
	if actor == null or not actor.visible or not _actors.has(actor.actor_entity_id) or not _bindings.has(actor.actor_entity_id):
		return false
	var inspection: Variant = _scene_lab.get("_inspection")
	var location: Variant = inspection.get("location") if typeof(inspection) == TYPE_DICTIONARY else null
	var node_id: Variant = location.get("id") if typeof(location) == TYPE_DICTIONARY else null
	var binding: Variant = _bindings.get(actor.actor_entity_id)
	return typeof(node_id) == TYPE_STRING and typeof(binding) == TYPE_DICTIONARY and binding.get("visibleNodeIds", []).has(node_id)


func _actor_is_interactable(actor: MatrixOasisNpcActorController) -> bool:
	return (
		not _lease_held
		and _actor_is_turn_safe(actor)
	)


func _actor_is_turn_safe(actor: MatrixOasisNpcActorController) -> bool:
	return (
		actor != null
		and _authority_state in ["idle", "quiescent"]
		and not _live_physical_evidence_pending()
		and _active_command.is_empty()
		and _pending_route.is_empty()
		and _cognition_pending_route.is_empty()
		and not actor.is_physics_processing()
	)


func _update_world_prompt() -> void:
	if _scene_lab == null or _scene_lab.prompt_label == null:
		return
	if _authority_state == "ended":
		_scene_lab.prompt_label.text = "Timeline ended · R to reset"
	elif not _interaction_actor_id.is_empty():
		_scene_lab.prompt_label.text = IDLE_PROMPT
	elif not _look_actor_id.is_empty():
		_scene_lab.prompt_label.text = BUSY_PROMPT
	else:
		_scene_lab.prompt_label.text = SEARCH_PROMPT


func _open_input(actor_id: String) -> void:
	if _cognition_state != "closed" or not _actors.has(actor_id):
		return
	var actor := _actors[actor_id] as MatrixOasisNpcActorController
	if not _actor_is_interactable(actor):
		_scene_lab.prompt_label.text = BUSY_PROMPT
		return
	_turn_actor_id = actor_id
	_player_text.text = ""
	_disclosure_label.text = ""
	_dialogue_label.text = ""
	_resume_authority_on_close = false
	_lease_held = true
	_lease_started_msec = Time.get_ticks_msec()
	_status_poll_count = 0
	_status_poll_started_msec = 0
	_status_poll_generation += 1
	_freeze_player()
	_set_modal_state("input")
	_player_text.grab_focus.call_deferred()


func _submit_player_text() -> void:
	if _cognition_state != "input" or _turn_actor_id.is_empty():
		return
	var actor := _actors.get(_turn_actor_id) as MatrixOasisNpcActorController
	if actor == null or not _actor_is_visible_here(actor) or not _actor_is_turn_safe(actor):
		_modal_status.text = BUSY_PROMPT
		return
	var player_input := _player_text.text
	if not _valid_player_text(player_input):
		_modal_status.text = "Enter 1–4096 bytes of plain text without control characters."
		return
	_set_modal_state("planning")
	_planning_started_msec = Time.get_ticks_msec()
	_start_cognition_request("cognition/turn", HTTPClient.METHOD_POST, {
		"actorEntityId": _turn_actor_id,
		"playerText": player_input,
	})


func _cancel_input() -> void:
	if _cognition_state == "input":
		_close_modal(false)


func _approve_turn() -> void:
	if _cognition_state != "approval" or not _valid_turn_id(_turn_id) or not _is_sha256(_approval_hash):
		return
	_set_modal_state("dispatching")
	_start_cognition_request("cognition/approve", HTTPClient.METHOD_POST, _approval_body(_turn_id, _approval_hash))


func _decline_turn() -> void:
	if _cognition_state != "approval" or not _valid_turn_id(_turn_id) or not _is_sha256(_approval_hash):
		return
	_set_modal_state("declining")
	_start_cognition_request("cognition/decline", HTTPClient.METHOD_POST, _approval_body(_turn_id, _approval_hash))


func _close_dialogue() -> void:
	if _cognition_state not in ["dialogue", "fallback"]:
		return
	_close_modal(_resume_authority_on_close)


func _close_modal(resume_authority: bool) -> void:
	_status_poll_generation += 1
	_lease_held = false
	_set_modal_state("closed")
	_restore_player()
	_clear_cognition_transient()
	if resume_authority and not _failed and _active_command.is_empty() and _pending_route.is_empty():
		_arm_one_authority_command()
		_request_command()


func _clear_cognition_transient() -> void:
	_turn_actor_id = ""
	_turn_id = ""
	_approval_hash = ""
	_display_ack_hash = ""
	_pending_dialogue_text = ""
	_pending_dialogue_action_queued = false
	if _player_text != null:
		_player_text.text = ""
	if _disclosure_label != null:
		_disclosure_label.text = ""
	if _dialogue_label != null:
		_dialogue_label.text = ""
	_resume_authority_on_close = false
	_lease_held = false
	_lease_started_msec = 0
	_planning_started_msec = 0
	_status_poll_count = 0
	_status_poll_started_msec = 0
	_status_poll_generation += 1


func _freeze_player() -> void:
	if _scene_lab == null or _scene_lab.player == null:
		return
	_player_was_enabled = _scene_lab.player.input_enabled
	_previous_mouse_mode = Input.mouse_mode
	_scene_lab.player.input_enabled = false
	_scene_lab.player.velocity = Vector3.ZERO
	Input.mouse_mode = Input.MOUSE_MODE_VISIBLE


func _restore_player() -> void:
	if _scene_lab == null or _scene_lab.player == null:
		return
	_scene_lab.player.input_enabled = _player_was_enabled
	_scene_lab.player.velocity = Vector3.ZERO
	Input.mouse_mode = _previous_mouse_mode


func _set_modal_state(state: String) -> void:
	_cognition_state = state
	_modal.visible = state != "closed"
	_input_section.visible = state in ["input", "planning"]
	_approval_section.visible = state in ["approval", "dispatching", "declining"]
	_dialogue_section.visible = state in ["displaying", "dialogue", "fallback"]
	_player_text.editable = state == "input"
	_submit_button.disabled = state != "input"
	_cancel_button.disabled = state != "input"
	_approve_button.disabled = state != "approval"
	_decline_button.disabled = state != "approval"
	_close_button.disabled = state != "dialogue" and state != "fallback"
	match state:
		"input":
			_modal_title.text = "Talk to NPC"
			_modal_status.text = "Ctrl+Enter submits · Esc cancels"
		"planning":
			_modal_title.text = "Preparing bounded context"
			_modal_status.text = "No external request has been approved."
		"approval":
			_modal_title.text = "External call approval"
			_modal_status.text = "Review every byte below · Enter approves · Esc declines"
		"dispatching":
			_modal_title.text = "Approved call in progress"
			_modal_status.text = "One request maximum · no retry"
		"declining":
			_modal_title.text = "Declining call"
			_modal_status.text = "No provider request will be sent."
		"displaying":
			_modal_title.text = "NPC dialogue"
			_modal_status.text = "Confirming local display before any action"
		"dialogue":
			_modal_title.text = "NPC dialogue"
			_modal_status.text = "Plain text only · Enter or Esc closes"
		"fallback":
			_modal_title.text = "Deterministic fallback"
			_modal_status.text = "No hidden action was applied · Enter or Esc closes"
		_:
			_modal_title.text = ""
			_modal_status.text = ""


func _start_cognition_request(route: String, method: HTTPClient.Method, body: Dictionary = {}) -> void:
	if _failed or not _cognition_pending_route.is_empty() or not _valid_cognition_route(route, method):
		_show_fallback("R22_GODOT_REQUEST_STATE_INVALID")
		return
	var encoded := "" if method == HTTPClient.METHOD_GET else JSON.stringify(body, "", true)
	if encoded.to_utf8_buffer().size() > MAX_LOCAL_BODY_BYTES:
		_show_fallback("R22_GODOT_REQUEST_LIMIT_EXCEEDED")
		return
	_cognition_pending_route = route
	var headers := PackedStringArray(["Authorization: Bearer " + _session_token])
	if method == HTTPClient.METHOD_POST:
		headers.append("Content-Type: application/json")
	var error := _cognition_request.request(R22_LOOPBACK_BASE + route, headers, method, encoded)
	if error != OK:
		_cognition_pending_route = ""
		if route in ["cognition/approve", "cognition/displayed"] or route.begins_with("cognition/status/"):
			_schedule_status_poll()
		else:
			_show_fallback("R22_GODOT_LOOPBACK_UNAVAILABLE")


func _on_cognition_request_completed(result: int, response_code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	var route := _cognition_pending_route
	_cognition_pending_route = ""
	if result != HTTPRequest.RESULT_SUCCESS or response_code != 200 or body.size() > MAX_LOCAL_BODY_BYTES:
		if route in ["cognition/approve", "cognition/displayed"] or route.begins_with("cognition/status/"):
			_schedule_status_poll()
		else:
			_show_fallback("R22_GODOT_LOOPBACK_RESPONSE_INVALID")
		return
	var response: Variant = JSON.parse_string(body.get_string_from_utf8())
	if typeof(response) != TYPE_DICTIONARY:
		_show_fallback("R22_GODOT_LOOPBACK_RESPONSE_INVALID")
		return
	if route == "cognition/turn":
		_accept_turn_plan(response)
	elif route == "cognition/displayed":
		_accept_display_ack(response)
	elif route == "cognition/approve" or route == "cognition/decline" or route.begins_with("cognition/status/"):
		_accept_turn_outcome(response)
	else:
		_show_fallback("R22_GODOT_LOOPBACK_ROUTE_INVALID")


func _accept_turn_plan(response: Dictionary) -> void:
	if not _exact(response, ["approvalHash", "disclosure", "status", "turnId"]) or response.get("status") != "approval_required":
		_show_fallback("R22_GODOT_CALL_PLAN_INVALID")
		return
	var turn_id: Variant = response.get("turnId")
	var approval_hash: Variant = response.get("approvalHash")
	var disclosure: Variant = response.get("disclosure")
	if typeof(turn_id) != TYPE_STRING or not _valid_turn_id(turn_id) or typeof(approval_hash) != TYPE_STRING or not _is_sha256(approval_hash) or typeof(disclosure) != TYPE_DICTIONARY or not _valid_disclosure(disclosure):
		_show_fallback("R22_GODOT_CALL_PLAN_INVALID")
		return
	_turn_id = turn_id
	_approval_hash = approval_hash
	_disclosure_label.text = _disclosure_text(disclosure)
	_set_modal_state("approval")
	_approve_button.grab_focus.call_deferred()


func _accept_turn_outcome(response: Dictionary) -> void:
	var status: Variant = response.get("status")
	if status == "dispatching" and _exact(response, ["status"]):
		_set_modal_state("dispatching")
		_schedule_status_poll()
		return
	if status == "fallback" and _exact(response, ["diagnostic", "status"]) and _valid_static_diagnostic(response.get("diagnostic")):
		_show_fallback(response["diagnostic"])
		_emit_trace("fallback", false)
		return
	if status == "dialogue_only" and _exact(response, ["actionChoiceId", "dialogueText", "displayAckHash", "status"]) and response.get("actionChoiceId") == null and _valid_dialogue_text(response.get("dialogueText")) and _is_sha256(response.get("displayAckHash")):
		_begin_dialogue_display(response["dialogueText"], false, response["displayAckHash"])
		return
	if status == "queued_for_r20" and _exact(response, ["actionChoiceId", "dialogueText", "displayAckHash", "status"]) and _is_choice_id(response.get("actionChoiceId")) and _valid_dialogue_text(response.get("dialogueText")) and _is_sha256(response.get("displayAckHash")):
		_begin_dialogue_display(response["dialogueText"], true, response["displayAckHash"])
		return
	_show_fallback("R22_GODOT_TURN_OUTCOME_INVALID")


func _begin_dialogue_display(dialogue: String, action_queued: bool, display_ack_hash: String) -> void:
	_status_poll_generation += 1
	_pending_dialogue_text = dialogue
	_pending_dialogue_action_queued = action_queued
	_display_ack_hash = display_ack_hash
	_dialogue_label.text = dialogue
	_set_modal_state("displaying")
	_acknowledge_dialogue_after_frame.call_deferred(display_ack_hash)


func _acknowledge_dialogue_after_frame(expected_display_ack_hash: String) -> void:
	await get_tree().process_frame
	if _cognition_state != "displaying" or expected_display_ack_hash != _display_ack_hash or not _is_sha256(_display_ack_hash):
		return
	_start_cognition_request("cognition/displayed", HTTPClient.METHOD_POST, _display_ack_body(_turn_id, _display_ack_hash))


func _accept_display_ack(response: Dictionary) -> void:
	if not _exact(response, ["status"]) or response.get("status") != "acknowledged" or not _is_sha256(_display_ack_hash) or not _valid_dialogue_text(_pending_dialogue_text):
		_show_fallback("R22_GODOT_DISPLAY_ACK_INVALID")
		return
	var dialogue := _pending_dialogue_text
	var action_queued := _pending_dialogue_action_queued
	_display_ack_hash = ""
	_pending_dialogue_text = ""
	_pending_dialogue_action_queued = false
	_show_dialogue(dialogue, action_queued)
	_emit_trace("queued_for_r20" if action_queued else "dialogue_only", action_queued)


func _schedule_status_poll() -> void:
	if not _valid_turn_id(_turn_id):
		_show_fallback("R22_GODOT_STATUS_ID_INVALID")
		return
	if _status_poll_started_msec == 0:
		_status_poll_started_msec = Time.get_ticks_msec()
	_status_poll_count += 1
	if _status_poll_count > MAX_STATUS_POLLS or Time.get_ticks_msec() - _status_poll_started_msec > STATUS_POLL_TIMEOUT_MSEC:
		_show_fallback("R22_GODOT_STATUS_TIMEOUT")
		return
	_set_modal_state("dispatching")
	var generation := _status_poll_generation
	await get_tree().create_timer(STATUS_POLL_SECONDS).timeout
	if generation != _status_poll_generation or _cognition_state != "dispatching" or not _cognition_pending_route.is_empty():
		return
	_start_cognition_request("cognition/status/" + _turn_id, HTTPClient.METHOD_GET)


func _show_dialogue(dialogue: String, action_queued: bool) -> void:
	_status_poll_generation += 1
	_lease_held = false
	_dialogue_label.text = dialogue
	_resume_authority_on_close = action_queued
	_set_modal_state("dialogue")
	_close_button.text = "Continue action" if action_queued else "Continue"
	_close_button.grab_focus.call_deferred()


func _show_fallback(_diagnostic: String) -> void:
	_status_poll_generation += 1
	_lease_held = false
	_dialogue_label.text = FIXED_FALLBACK_TEXT
	_resume_authority_on_close = true
	_set_modal_state("fallback")
	_close_button.text = "Continue"
	_close_button.grab_focus.call_deferred()


func _emit_trace(status: String, action_queued: bool) -> void:
	print(R22_TRACE_MARKER + JSON.stringify({
		"actionQueued": action_queued,
		"status": status,
	}, "", true))


func _begin_live_physical_evidence(command: Dictionary) -> void:
	if _live_physical_sampling:
		_fail("R22_LIVE_PHYSICAL_EVIDENCE_REENTRY")
		return
	_reset_live_physical_evidence()
	_live_physical_command = command.duplicate(true)
	_live_physical_sampling = true
	_live_physical_previous_frame_usec = Time.get_ticks_usec()


func _live_physical_evidence_pending() -> bool:
	return _live_physical_sampling and not _live_physical_evidence_emitted


func _sample_live_physical_evidence() -> void:
	if not _live_physical_sampling or _failed or _live_physical_evidence_emitted:
		return
	var now := Time.get_ticks_usec()
	if _live_physical_previous_frame_usec <= 0:
		_live_physical_previous_frame_usec = now
		return
	if _live_physical_frames.size() < LIVE_PHYSICAL_SAMPLE_COUNT:
		_live_physical_frames.append(maxi(1, now - _live_physical_previous_frame_usec))
	_live_physical_previous_frame_usec = now
	_maybe_emit_live_physical_evidence()


func _maybe_emit_live_physical_evidence() -> void:
	if not _live_physical_sampling or not _live_physical_cycle_complete or _live_physical_evidence_emitted or _live_physical_frames.size() != LIVE_PHYSICAL_SAMPLE_COUNT:
		return
	var evidence := _build_live_physical_evidence(_live_physical_command, _trace, _live_physical_frames, _entity_binding_sha256, _live_physical_return_evidence)
	if evidence.is_empty():
		_fail("R22_LIVE_PHYSICAL_EVIDENCE_INVALID")
		return
	var canonical := JSON.stringify(evidence, "", true)
	if canonical.to_utf8_buffer().size() > MAX_LIVE_PHYSICAL_EVIDENCE_BYTES:
		_fail("R22_LIVE_PHYSICAL_EVIDENCE_LIMIT_EXCEEDED")
		return
	_live_physical_evidence_emitted = true
	_live_physical_sampling = false
	print(R22_LIVE_PHYSICAL_EVIDENCE_MARKER + canonical)


func _reset_live_physical_evidence() -> void:
	_live_physical_command = {}
	_live_physical_frames.clear()
	_live_physical_sampling = false
	_live_physical_cycle_complete = false
	_live_physical_return_evidence = {}
	_live_physical_evidence_emitted = false
	_live_physical_previous_frame_usec = 0


func _current_live_physical_return_evidence() -> Dictionary:
	var actor_id: Variant = _live_physical_command.get("actorEntityId")
	var actor := _actors.get(actor_id) as MatrixOasisNpcActorController
	if actor == null:
		return {}
	var error_mm := roundi(actor.global_position.distance_to(actor.home_transform.origin) * 1000.0)
	if actor.is_physics_processing() or error_mm < 0 or error_mm > 100:
		return {}
	return {"kind": "walked-home" if actor.visible else "hidden-home", "physicsProcessing": false, "positionErrorMm": error_mm, "returnedHome": true}


static func _build_live_physical_evidence(command: Dictionary, trace: Array, frames: Array[int], entity_binding_sha256: String, return_evidence: Dictionary) -> Dictionary:
	if frames.size() != LIVE_PHYSICAL_SAMPLE_COUNT or not _is_sha256(entity_binding_sha256):
		return {}
	if not _exact(return_evidence, ["kind", "physicsProcessing", "positionErrorMm", "returnedHome"]) or return_evidence.get("kind") not in ["walked-home", "hidden-home"] or return_evidence.get("returnedHome") != true or return_evidence.get("physicsProcessing") != false or typeof(return_evidence.get("positionErrorMm")) != TYPE_INT or return_evidence.get("positionErrorMm") < 0 or return_evidence.get("positionErrorMm") > 100:
		return {}
	if not _exact(command, ["actionId", "actorEntityId", "intentId", "nodeId", "npcIntentJson", "ruleIndex", "sequence"]):
		return {}
	# JSON.parse_string represents JSON integers as floats. Normalize only the
	# two bounded integer fields in this closed command before canonical hashing.
	var normalized_command := command.duplicate(true)
	var integer_bounds := {"sequence": [1, 10000], "ruleIndex": [0, 255]}
	for field: String in integer_bounds:
		var value: Variant = command[field]
		if typeof(value) not in [TYPE_INT, TYPE_FLOAT]:
			return {}
		if not is_finite(float(value)) or value != floor(value) or value < integer_bounds[field][0] or value > integer_bounds[field][1]:
			return {}
		normalized_command[field] = int(value)
	var intent_json: Variant = command.get("npcIntentJson")
	var intent: Variant = JSON.parse_string(intent_json) if typeof(intent_json) == TYPE_STRING else null
	if typeof(intent) != TYPE_DICTIONARY:
		return {}
	if intent.get("id") != command.get("intentId") or intent.get("actorEntityId") != command.get("actorEntityId") or intent.get("nodeId") != command.get("nodeId") or intent.get("actionId") != command.get("actionId") or not _valid_turn_id(intent.get("timelineId")):
		return {}
	if trace.size() < 2:
		return {}
	var physical_trace: Array = [trace[trace.size() - 2], trace[trace.size() - 1]]
	var arrived: Variant = physical_trace[0]
	var mirrored: Variant = physical_trace[1]
	if typeof(arrived) != TYPE_DICTIONARY or typeof(mirrored) != TYPE_DICTIONARY or arrived.get("state") != "arrived" or mirrored.get("state") != "mirrored" or arrived.get("sequence") != command.get("sequence") or mirrored.get("sequence") != command.get("sequence"):
		return {}
	var arrival: Variant = arrived.get("arrivalEvidence")
	if typeof(arrival) != TYPE_DICTIONARY or arrival.get("pathComplete") != true or arrival.get("floorVerified") != true or arrival.get("capsuleVerified") != true or arrival.get("domainVerified") != true:
		return {}
	if not _is_sha256(mirrored.get("beforeSnapshotSha256")) or not _is_sha256(mirrored.get("afterSnapshotSha256")) or mirrored.get("decision") not in ["accepted", "rejected"]:
		return {}
	var sorted_frames: Array = frames.duplicate()
	sorted_frames.sort()
	var median_micros: int = floori((float(sorted_frames[149]) + float(sorted_frames[150])) / 2.0)
	if median_micros <= 0 or median_micros > 1000000:
		return {}
	var canonical_command := JSON.stringify(normalized_command, "", true)
	return {
		"authority": {
			"afterSnapshotSha256": mirrored["afterSnapshotSha256"],
			"beforeSnapshotSha256": mirrored["beforeSnapshotSha256"],
			"decision": mirrored["decision"],
		},
		"canonicalR20Trace": JSON.stringify(physical_trace, "", true),
		"canonicalization": "matrix-oasis.canonical-json/1",
		"command": {
			"actionId": command["actionId"], "actorEntityId": command["actorEntityId"],
			"commandSha256": "sha256:" + canonical_command.sha256_text(),
			"intentId": command["intentId"], "intentSha256": "sha256:" + intent_json.sha256_text(),
			"nodeId": command["nodeId"], "ruleIndex": normalized_command["ruleIndex"], "sequence": normalized_command["sequence"],
		},
		"entityBindingSha256": entity_binding_sha256,
		"format": "matrix-oasis.r22-live-physical-evidence",
		"formatVersion": "0.1.0",
		"movement": {"outbound": arrival, "return": return_evidence},
		"performance": {
			"frameMicros": frames.duplicate(),
			"medianFpsMilli": floori(1000000000.0 / float(median_micros)),
			"medianFrameMicros": median_micros, "sampleCount": LIVE_PHYSICAL_SAMPLE_COUNT,
		},
		"timelineId": intent["timelineId"],
	}


static func _approval_body(turn_id: String, approval_hash: String) -> Dictionary:
	return {"approvalHash": approval_hash, "turnId": turn_id}


static func _display_ack_body(turn_id: String, display_ack_hash: String) -> Dictionary:
	return {"displayAckHash": display_ack_hash, "turnId": turn_id}


static func _valid_cognition_route(route: String, method: HTTPClient.Method) -> bool:
	if method == HTTPClient.METHOD_POST:
		return route in ["cognition/turn", "cognition/approve", "cognition/decline", "cognition/displayed"]
	if method == HTTPClient.METHOD_GET and route.begins_with("cognition/status/"):
		return _valid_turn_id(route.trim_prefix("cognition/status/"))
	return false


static func _valid_turn_id(value: Variant) -> bool:
	if typeof(value) != TYPE_STRING or value.is_empty() or value.length() > 128:
		return false
	if value.unicode_at(0) < 97 or value.unicode_at(0) > 122 or value.ends_with("-") or value.contains("--"):
		return false
	for index in value.length():
		var code: int = value.unicode_at(index)
		var lowercase: bool = code >= 97 and code <= 122
		var digit: bool = code >= 48 and code <= 57
		if not lowercase and not digit and code != 45:
			return false
	return true


static func _is_sha256(value: Variant) -> bool:
	if typeof(value) != TYPE_STRING or not value.begins_with("sha256:") or value.length() != 71:
		return false
	var text: String = value
	var digest: String = text.trim_prefix("sha256:")
	return digest == digest.to_lower() and digest.is_valid_hex_number()


static func _is_choice_id(value: Variant) -> bool:
	if typeof(value) != TYPE_STRING or not value.begins_with("choice-") or value.length() != 71:
		return false
	var text: String = value
	var digest: String = text.trim_prefix("choice-")
	return digest == digest.to_lower() and digest.is_valid_hex_number()


static func _valid_player_text(value: Variant) -> bool:
	return typeof(value) == TYPE_STRING and not value.strip_edges().is_empty() and value.to_utf8_buffer().size() <= MAX_PLAYER_TEXT_BYTES and _valid_plain_text(value, 4096)


static func _valid_dialogue_text(value: Variant) -> bool:
	return typeof(value) == TYPE_STRING and value.to_utf8_buffer().size() <= MAX_DIALOGUE_BYTES and value.split("\n", true).size() <= MAX_DIALOGUE_LINES and _valid_plain_text(value, MAX_DIALOGUE_BYTES)


static func _valid_plain_text(value: String, maximum_characters: int) -> bool:
	if value.is_empty() or value.length() > maximum_characters:
		return false
	for index in value.length():
		var code: int = value.unicode_at(index)
		if code == 0 or (code < 32 and code != 10) or (code >= 127 and code <= 159) or code in [0x2028, 0x2029, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069]:
			return false
	return true


static func _valid_disclosure(value: Dictionary) -> bool:
	if not _exact(value, [
		"endpoint", "maxCostMicrousd", "maxOutputTokens", "model", "priceLock",
		"providerRequestJson", "requestLimit", "retention", "retryLimit",
	]):
		return false
	if value.get("endpoint") != OPENAI_RESPONSES_ENDPOINT or value.get("model") != OPENAI_COGNITION_MODEL:
		return false
	if not _exact_integer(value.get("maxCostMicrousd"), MAX_CALL_COST_MICROUSD):
		return false
	if not _exact_integer(value.get("maxOutputTokens"), MAX_OUTPUT_TOKENS):
		return false
	if not _exact_integer(value.get("requestLimit"), PROVIDER_REQUEST_LIMIT):
		return false
	if not _exact_integer(value.get("retryLimit"), PROVIDER_RETRY_LIMIT):
		return false
	var request_json: Variant = value.get("providerRequestJson")
	if typeof(request_json) != TYPE_STRING or request_json.to_utf8_buffer().size() > 32768 or typeof(JSON.parse_string(request_json)) != TYPE_DICTIONARY:
		return false
	var price_lock: Variant = value.get("priceLock")
	if typeof(price_lock) != TYPE_DICTIONARY or not _exact(price_lock, [
		"cacheWriteInputMicrousdPerMillionTokens", "cachedInputMicrousdPerMillionTokens",
		"inputMicrousdPerMillionTokens", "outputMicrousdPerMillionTokens",
	]):
		return false
	if not _exact_integer(price_lock.get("inputMicrousdPerMillionTokens"), INPUT_MICROUSD_PER_MILLION_TOKENS):
		return false
	if not _exact_integer(price_lock.get("cachedInputMicrousdPerMillionTokens"), CACHED_INPUT_MICROUSD_PER_MILLION_TOKENS):
		return false
	if not _exact_integer(price_lock.get("cacheWriteInputMicrousdPerMillionTokens"), CACHE_WRITE_INPUT_MICROUSD_PER_MILLION_TOKENS):
		return false
	if not _exact_integer(price_lock.get("outputMicrousdPerMillionTokens"), OUTPUT_MICROUSD_PER_MILLION_TOKENS):
		return false
	var retention: Variant = value.get("retention")
	if typeof(retention) != TYPE_DICTIONARY or not _exact(retention, [
		"abuseMonitoringMaxDays", "promptCachingPossible", "store", "zeroDataRetentionClaimed",
	]):
		return false
	return (
		retention.get("store") == false
		and retention.get("zeroDataRetentionClaimed") == false
		and _exact_integer(retention.get("abuseMonitoringMaxDays"), 30)
		and retention.get("promptCachingPossible") == true
	)


static func _exact_integer(value: Variant, expected: int) -> bool:
	if typeof(value) == TYPE_INT:
		return value == expected
	return typeof(value) == TYPE_FLOAT and is_finite(value) and value == float(expected)


static func _disclosure_text(value: Dictionary) -> String:
	var price_lock: Dictionary = value["priceLock"]
	var retention: String = JSON.stringify(value["retention"], "  ", true)
	return "Model: %s\nEndpoint: %s\nMaximum cost: %d microusd\nMaximum output: %d tokens\nRequest limit: %d\nRetry limit: %d\nPrice lock (microusd / 1M tokens): input %d, cached input %d, cache-write input %d, output %d\nRetention: %s\n\nExact outbound JSON:\n%s" % [
		value["model"], value["endpoint"], int(value["maxCostMicrousd"]),
		int(value["maxOutputTokens"]), int(value["requestLimit"]), int(value["retryLimit"]),
		int(price_lock["inputMicrousdPerMillionTokens"]), int(price_lock["cachedInputMicrousdPerMillionTokens"]),
		int(price_lock["cacheWriteInputMicrousdPerMillionTokens"]), int(price_lock["outputMicrousdPerMillionTokens"]),
		retention, value["providerRequestJson"],
	]


static func _valid_static_diagnostic(value: Variant) -> bool:
	if typeof(value) != TYPE_STRING or not value.begins_with("R22_") or value.length() > 96:
		return false
	for index in value.length():
		var code: int = value.unicode_at(index)
		if not ((code >= 65 and code <= 90) or (code >= 48 and code <= 57) or code == 95):
			return false
	return true


static func _is_enter(event: InputEventKey) -> bool:
	return event.keycode in [KEY_ENTER, KEY_KP_ENTER] or event.physical_keycode in [KEY_ENTER, KEY_KP_ENTER]


static func _is_escape(event: InputEventKey) -> bool:
	return event.keycode == KEY_ESCAPE or event.physical_keycode == KEY_ESCAPE


static func _is_physical_key(event: InputEvent, key: Key) -> bool:
	return event is InputEventKey and event.pressed and not event.echo and event.physical_keycode == key
