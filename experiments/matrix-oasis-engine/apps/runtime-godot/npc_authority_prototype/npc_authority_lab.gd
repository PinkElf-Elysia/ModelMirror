extends Node3D

const READY_MARKER := "MATRIX_OASIS_R20_NPC_BRIDGE_READY"
const TRACE_MARKER := "MATRIX_OASIS_R20_NPC_TRACE_JSON:"
const LOOPBACK_BASE := "http://127.0.0.1:43120/v1/"
const SESSION_TOKEN_ENV := "MATRIX_OASIS_R20_SESSION_TOKEN"
const BINDING_PATH := "res://npc_authority_prototype/entity-bindings.json"
const FACTS_PATH := "res://npc_authority_prototype/environment-facts.json"
const SOLUTION_PATH := "res://npc_authority_prototype/spatial-solution.json"
const SOLVED_SCENE := preload("res://solved_spatial_prototype/solved_spatial_lab.tscn")
const ACTOR_SCRIPT := preload("res://npc_authority_prototype/npc_actor_controller.gd")
const MAX_STARTUP_FRAMES := 7200

@onready var _request: HTTPRequest = $AuthorityRequest

var _tested_scene: Node
var _scene_lab: MatrixOasisSolvedSpatialSceneLab
var _navigation_region: NavigationRegion3D
var _floor_anchors: Dictionary = {}
var _actors: Dictionary = {}
var _bindings: Dictionary = {}
var _session_token := ""
var _pending_route := ""
var _active_command: Dictionary = {}
var _active_actor: MatrixOasisNpcActorController
var _trace: Array = []
var _failed := false


func _ready() -> void:
	_session_token = OS.get_environment(SESSION_TOKEN_ENV)
	if _session_token.length() < 32:
		_fail("R20_GODOT_SESSION_TOKEN_INVALID")
		return
	_request.request_completed.connect(_on_request_completed)
	_tested_scene = SOLVED_SCENE.instantiate()
	if _tested_scene == null:
		_fail("R20_GODOT_SOLVED_SCENE_INVALID")
		return
	add_child(_tested_scene)
	_initialize.call_deferred()


func _initialize() -> void:
	for _frame in MAX_STARTUP_FRAMES:
		await get_tree().process_frame
		var candidate: Variant = _tested_scene.get("_scene_lab")
		if candidate is MatrixOasisSolvedSpatialSceneLab:
			_scene_lab = candidate
			break
	if _scene_lab == null or _scene_lab.player == null:
		_fail("R20_GODOT_SOLVED_SCENE_STARTUP_FAILED")
		return
	var bindings := _read_json(FileAccess.open("res://npc_authority_prototype/entity-bindings.json", FileAccess.READ))
	var facts := _read_json(FileAccess.open("res://npc_authority_prototype/environment-facts.json", FileAccess.READ))
	var solution := _read_json(FileAccess.open("res://npc_authority_prototype/spatial-solution.json", FileAccess.READ))
	if not _install_navigation(facts, solution) or not _install_actors(bindings):
		_fail("R20_GODOT_BRIDGE_INPUT_INVALID")
		return
	if not await _wait_for_navigation_sync():
		_fail("R20_GODOT_NAVIGATION_SYNC_FAILED")
		return
	_disable_player_timeline_writes()
	print(READY_MARKER)
	_request_command()


func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed(&"reset_session"):
		get_viewport().set_input_as_handled()
		if _active_command.is_empty() and _pending_route.is_empty():
			_post("reset", {})


func _disable_player_timeline_writes() -> void:
	_scene_lab.set_process_unhandled_input(false)
	_scene_lab.reset_button.disabled = true
	_scene_lab.interaction_ray.enabled = false
	var action_callable := Callable(_scene_lab, "_apply_action")
	if _scene_lab.interaction_ray.action_requested.is_connected(action_callable):
		_scene_lab.interaction_ray.action_requested.disconnect(action_callable)


func _install_navigation(facts: Dictionary, solution: Dictionary) -> bool:
	var navigation: Variant = solution.get("navigation")
	if typeof(navigation) != TYPE_DICTIONARY or typeof(navigation.get("zoneDomains")) != TYPE_ARRAY:
		return false
	var domain_ids: Dictionary = {}
	for domain: Variant in navigation["zoneDomains"]:
		if typeof(domain) != TYPE_DICTIONARY or typeof(domain.get("floorAnchorIds")) != TYPE_ARRAY:
			return false
		for anchor_id: Variant in domain["floorAnchorIds"]:
			if typeof(anchor_id) != TYPE_STRING or domain_ids.has(anchor_id):
				return false
			domain_ids[anchor_id] = true
	var faces: Variant = _scene_lab.get("_navigation_domain_faces")
	var support_height: Variant = _scene_lab.get("_runtime_support_height_mm")
	if typeof(faces) != TYPE_PACKED_VECTOR3_ARRAY or faces.is_empty() or faces.size() % 3 != 0 or typeof(support_height) != TYPE_INT:
		return false
	var mesh := NavigationMesh.new()
	var vertices := PackedVector3Array()
	var vertex_indices: Dictionary = {}
	for index in range(0, faces.size(), 3):
		var polygon := PackedInt32Array()
		for offset in 3:
			var point: Vector3 = faces[index + offset]
			if not vertex_indices.has(point):
				vertex_indices[point] = vertices.size()
				vertices.append(point)
			polygon.append(vertex_indices[point])
		mesh.add_polygon(polygon)
	mesh.vertices = vertices
	_navigation_region = NavigationRegion3D.new()
	_navigation_region.name = "R20NpcNavigationRegion"
	_navigation_region.navigation_mesh = mesh
	add_child(_navigation_region)
	var component_index := int(navigation.get("componentIndex", -1))
	for anchor: Variant in facts.get("floorAnchors", []):
		if typeof(anchor) != TYPE_DICTIONARY or int(anchor.get("componentIndex", -2)) != component_index or not domain_ids.has(anchor.get("id")):
			continue
		var position: Variant = anchor.get("positionMm")
		if typeof(position) != TYPE_ARRAY or position.size() != 3:
			return false
		_floor_anchors[anchor["id"]] = Vector3(float(position[0]), float(support_height), float(position[2])) / 1000.0
	return not _floor_anchors.is_empty()


func _install_actors(document: Dictionary) -> bool:
	if not _exact(document, ["bindings", "canonicalization", "format", "formatVersion", "identities"]) or document.get("format") != "matrix-oasis.npc-entity-binding" or document.get("formatVersion") != "0.1.0":
		return false
	var values: Variant = document.get("bindings")
	if typeof(values) != TYPE_ARRAY or values.size() > 6:
		return false
	var world: Variant = _scene_lab.get("_composed_world")
	var placement_paths: Variant = _scene_lab.get("_scene_placement_ids")
	var root := world._root_for_runtime() as Node3D if world is MatrixOasisComposedScene else null
	if root == null or typeof(placement_paths) != TYPE_DICTIONARY:
		return false
	for binding: Variant in values:
		if typeof(binding) != TYPE_DICTIONARY or not _exact(binding, ["actorEntityId", "assetBriefId", "homeFloorAnchorId", "homePositionMm", "placementId", "runtimeEntityId", "visibleNodeIds"]):
			return false
		var actor_id: Variant = binding.get("actorEntityId")
		var placement_id: Variant = binding.get("placementId")
		if typeof(actor_id) != TYPE_STRING or actor_id.is_empty() or _actors.has(actor_id) or typeof(placement_id) != TYPE_STRING or not placement_paths.has(placement_id) or not _floor_anchors.has(binding.get("homeFloorAnchorId")):
			return false
		var placement := root.get_node_or_null(NodePath(placement_paths[placement_id])) as Node3D
		var actor := ACTOR_SCRIPT.new() as MatrixOasisNpcActorController
		if actor == null or not actor.configure(actor_id, placement):
			return false
		actor.movement_arrived.connect(_on_actor_arrived.bind(actor))
		actor.movement_failed.connect(_on_actor_failed.bind(actor))
		_actors[actor_id] = actor
		_bindings[actor_id] = binding.duplicate(true)
	return not _actors.is_empty()


func _wait_for_navigation_sync() -> bool:
	var navigation_map := _navigation_region.get_navigation_map()
	for _frame in 8:
		await get_tree().physics_frame
		if NavigationServer3D.map_get_iteration_id(navigation_map) > 0:
			return true
	return false


func _request_command() -> void:
	_get("command")


func _get(route: String) -> void:
	_start_request(route, HTTPClient.METHOD_GET, "")


func _post(route: String, body: Dictionary) -> void:
	_start_request(route, HTTPClient.METHOD_POST, JSON.stringify(body, "", true))


func _start_request(route: String, method: HTTPClient.Method, body: String) -> void:
	if _failed or not _pending_route.is_empty() or route not in ["command", "arrived", "mirror", "reset", "verify"]:
		_fail("R20_GODOT_REQUEST_STATE_INVALID")
		return
	_pending_route = route
	var headers := PackedStringArray(["Authorization: Bearer " + _session_token])
	if method == HTTPClient.METHOD_POST:
		headers.append("Content-Type: application/json")
	var error := _request.request(LOOPBACK_BASE + route, headers, method, body)
	if error != OK:
		_pending_route = ""
		_fail("R20_GODOT_REQUEST_FAILED")


func _on_request_completed(result: int, response_code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	var route := _pending_route
	_pending_route = ""
	if result != HTTPRequest.RESULT_SUCCESS or response_code != 200 or body.size() > 32768:
		_fail("R20_GODOT_RESPONSE_INVALID")
		return
	var response: Variant = JSON.parse_string(body.get_string_from_utf8())
	if typeof(response) != TYPE_DICTIONARY:
		_fail("R20_GODOT_RESPONSE_INVALID")
		return
	match route:
		"command": _accept_command_response(response)
		"arrived": _accept_adjudication_response(response)
		"mirror": _accept_mirror_response(response)
		"reset": _accept_reset_response(response)
		"verify": _accept_verify_response(response)
		_: _fail("R20_GODOT_RESPONSE_ROUTE_INVALID")


func _accept_command_response(response: Dictionary) -> void:
	if response.get("status") in ["quiescent", "ended"]:
		_post("verify", {})
		return
	var command: Variant = response.get("command")
	if response.get("status") != "command" or typeof(command) != TYPE_DICTIONARY or not _actors.has(command.get("actorEntityId")):
		_fail("R20_GODOT_COMMAND_INVALID")
		return
	var actor := _actors[command["actorEntityId"]] as MatrixOasisNpcActorController
	var contexts: Variant = _scene_lab.get("_solution_contexts")
	var context: Variant = contexts.get(command.get("nodeId")) if typeof(contexts) == TYPE_DICTIONARY else null
	var anchor_id: Variant = context.get("actionTerminal", {}).get("approachFloorAnchorId") if typeof(context) == TYPE_DICTIONARY else null
	var binding: Dictionary = _bindings[command["actorEntityId"]]
	if typeof(anchor_id) != TYPE_STRING or not _floor_anchors.has(anchor_id) or not binding["visibleNodeIds"].has(command.get("nodeId")) or not actor.visible:
		_fail("R20_GODOT_COMMAND_TARGET_INVALID")
		return
	_active_command = command.duplicate(true)
	_active_actor = actor
	_trace.append({"sequence": command["sequence"], "actorEntityId": command["actorEntityId"], "actionId": command["actionId"], "state": "moving"})
	if not actor.begin_move(_floor_anchors[anchor_id]):
		_fail("R20_GODOT_MOVEMENT_START_FAILED")


func _on_actor_arrived(evidence: Dictionary, actor: MatrixOasisNpcActorController) -> void:
	if actor != _active_actor:
		_fail("R20_GODOT_ACTOR_MISMATCH")
		return
	if actor.is_returning():
		actor.restore_home()
		_active_actor = null
		_active_command = {}
		_request_command()
		return
	var request_body := evidence.duplicate(true)
	request_body["sequence"] = _active_command["sequence"]
	_post("arrived", request_body)


func _on_actor_failed(code: String, actor: MatrixOasisNpcActorController) -> void:
	if actor == _active_actor:
		_fail(code)


func _accept_adjudication_response(response: Dictionary) -> void:
	if response.get("status") != "adjudicated" or _active_actor == null:
		_fail("R20_GODOT_ADJUDICATION_INVALID")
		return
	var before := _snapshot_sha256()
	if before != response.get("beforeSnapshotSha256"):
		_fail("R20_GODOT_BEFORE_MIRROR_MISMATCH")
		return
	if response.get("decision") == "accepted":
		if not _scene_lab._apply_action(_active_command["actionId"]):
			_fail("R20_GODOT_RUNTIME_MIRROR_FAILED")
			return
	elif response.get("decision") != "rejected":
		_fail("R20_GODOT_ADJUDICATION_INVALID")
		return
	for actor: MatrixOasisNpcActorController in _actors.values():
		actor.refresh_visibility_collision()
	var after := _snapshot_sha256()
	if after != response.get("afterSnapshotSha256"):
		_fail("R20_GODOT_AFTER_MIRROR_MISMATCH")
		return
	_post("mirror", {"sequence": _active_command["sequence"], "beforeSnapshotSha256": before, "afterSnapshotSha256": after})


func _accept_mirror_response(response: Dictionary) -> void:
	if response.get("status") != "committed" or _active_actor == null:
		_fail("R20_GODOT_MIRROR_ACK_INVALID")
		return
	_trace.append({"sequence": _active_command["sequence"], "actorEntityId": _active_command["actorEntityId"], "actionId": _active_command["actionId"], "state": "mirrored", "snapshotSha256": _snapshot_sha256()})
	if not _active_actor.visible:
		_active_actor.hide_at_home()
		_active_actor = null
		_active_command = {}
		_request_command()
		return
	if not _active_actor.begin_move(_active_actor.home_transform.origin, true):
		_fail("R20_GODOT_RETURN_START_FAILED")


func _accept_reset_response(response: Dictionary) -> void:
	if response.get("status") != "reset" or not _scene_lab._reset_session():
		_fail("R20_GODOT_RESET_FAILED")
		return
	for actor: MatrixOasisNpcActorController in _actors.values():
		actor.hide_at_home()
		actor.visible = _bindings[actor.actor_entity_id]["visibleNodeIds"].has(_scene_lab.get("_inspection")["location"]["id"])
	_active_actor = null
	_active_command = {}
	_trace.clear()
	_request_command()


func _accept_verify_response(response: Dictionary) -> void:
	if response.get("status") != "verified" or typeof(response.get("revision")) != TYPE_INT:
		_fail("R20_GODOT_VERIFY_FAILED")
		return
	print(TRACE_MARKER + JSON.stringify({"traceVersion": 1, "revision": response["revision"], "events": _trace}, "", true))


func _snapshot_sha256() -> String:
	return "sha256:" + JSON.stringify(_scene_lab.snapshot_copy_for_test(), "", true).sha256_text()


func _read_json(file: FileAccess) -> Dictionary:
	if file == null:
		return {}
	var value: Variant = JSON.parse_string(file.get_as_text())
	return value if typeof(value) == TYPE_DICTIONARY else {}


static func _exact(value: Dictionary, keys: Array) -> bool:
	var actual := value.keys()
	actual.sort()
	var expected := keys.duplicate()
	expected.sort()
	return actual == expected


func _fail(code: String) -> void:
	if _failed:
		return
	_failed = true
	printerr(code if code.begins_with("R20_") else "R20_GODOT_ENTITY_BRIDGE_INTERNAL_ERROR")
	get_tree().quit(1)
