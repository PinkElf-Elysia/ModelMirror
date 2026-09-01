extends SceneTree

const MARKER := "R20_NPC_P1_PROBE_JSON:"
const PROBE_OK_MARKER := "R20_NPC_P1_PROBE_OK"
const ACTOR_SCRIPT := preload("res://npc_authority_prototype/npc_actor_controller.gd")
const LAB_SCRIPT := preload("res://npc_authority_prototype/npc_authority_lab.gd")
const LOWER_FLOOR_Y := 0.0
const UPPER_FLOOR_Y := 4.0


func _init() -> void:
	_run.call_deferred()


func _run() -> void:
	var performance := _probe_performance_selection()
	if performance.is_empty():
		return
	var world := Node3D.new()
	world.name = "NpcP1ProbeWorld"
	root.add_child(world)
	_install_floor(world, "LowerFloor", LOWER_FLOOR_Y)
	_install_floor(world, "UpperFloor", UPPER_FLOOR_Y)
	var navigation_region := _install_lower_navigation(world)
	var navigation_map := navigation_region.get_navigation_map()
	var synchronized := false
	for _frame in 16:
		await physics_frame
		if NavigationServer3D.map_get_iteration_id(navigation_map) > 0:
			synchronized = true
			break
	if not synchronized:
		_fail("R20_NPC_P1_NAVIGATION_SYNC_FAILED")
		return
	if not _probe_scaled_visual(world):
		return

	var lower_actor := _create_actor(world, "LowerActor", LOWER_FLOOR_Y)
	if not lower_actor.begin_move(Vector3(0.0, UPPER_FLOOR_Y, 0.0)):
		_fail("R20_NPC_P1_LOWER_MOVE_FAILED")
		return
	lower_actor.set_physics_process(false)
	var wrong_y_evidence: Dictionary = lower_actor._physics_evidence()
	if wrong_y_evidence.get("pathComplete") != false or wrong_y_evidence.get("floorVerified") != false:
		_fail("R20_NPC_P1_STACKED_FLOOR_WRONG_Y_ACCEPTED")
		return

	var upper_actor := _create_actor(world, "UpperActor", UPPER_FLOOR_Y)
	if not upper_actor.begin_move(Vector3(0.0, UPPER_FLOOR_Y, 0.0)):
		_fail("R20_NPC_P1_UPPER_MOVE_FAILED")
		return
	upper_actor.set_physics_process(false)
	var wrong_domain_evidence: Dictionary = upper_actor._physics_evidence()
	if wrong_domain_evidence.get("floorVerified") != true or wrong_domain_evidence.get("domainVerified") != false:
		_fail("R20_NPC_P1_STACKED_DOMAIN_WRONG_Y_ACCEPTED")
		return

	var lab := LAB_SCRIPT.new()
	var invalid_return_evidence := {
		"pathComplete": true,
		"floorVerified": false,
		"capsuleVerified": true,
		"domainVerified": true,
		"movementTicks": 1,
		"pathLengthMm": 1,
	}
	if lab._arrival_evidence_valid(invalid_return_evidence):
		_fail("R20_NPC_P1_RETURN_FALSE_EVIDENCE_ACCEPTED")
		return
	lab.free()

	print(MARKER + JSON.stringify({
		"stackedFloorWrongYRejected": true,
		"stackedDomainWrongYRejected": true,
		"scaledVisualPhysicsRootNormalized": true,
		"returnFalseEvidenceRejected": true,
		"performance": performance,
	}))
	print(PROBE_OK_MARKER)
	quit(0)


func _probe_performance_selection() -> Dictionary:
	var lab := LAB_SCRIPT.new()
	var frames: Array[int] = []
	for index in 600:
		frames.append(10000 if index < 240 else 50000)
	var selected: Array[int] = lab._select_performance_samples(frames)
	lab.free()
	if selected.size() != 300 or selected.front() != frames.front() or selected.back() != frames.back():
		_fail("R20_NPC_P1_PERFORMANCE_CYCLE_COVERAGE_INVALID")
		return {}
	selected.sort()
	var median_micros: int = selected[selected.size() / 2]
	var median_fps_milli := floori(1000000000.0 / float(median_micros))
	if median_micros != 50000 or median_fps_milli >= 30000:
		_fail("R20_NPC_P1_BEHAVIOR_SLOW_FRAMES_DROPPED")
		return {}
	return {
		"sampleCount": selected.size(),
		"medianFrameMicros": median_micros,
		"medianFpsMilli": median_fps_milli,
		"firstFrameCovered": true,
		"lastFrameCovered": true,
	}


func _install_floor(parent: Node3D, floor_name: String, floor_y: float) -> void:
	var body := StaticBody3D.new()
	body.name = floor_name
	body.collision_layer = 1
	body.collision_mask = 0
	var collision := CollisionShape3D.new()
	var shape := BoxShape3D.new()
	shape.size = Vector3(8.0, 0.1, 8.0)
	collision.shape = shape
	collision.position.y = floor_y - 0.05
	body.add_child(collision)
	parent.add_child(body)


func _install_lower_navigation(parent: Node3D) -> NavigationRegion3D:
	var mesh := NavigationMesh.new()
	mesh.vertices = PackedVector3Array([
		Vector3(-2.0, LOWER_FLOOR_Y, -2.0),
		Vector3(-2.0, LOWER_FLOOR_Y, 2.0),
		Vector3(2.0, LOWER_FLOOR_Y, 2.0),
		Vector3(2.0, LOWER_FLOOR_Y, -2.0),
	])
	mesh.add_polygon(PackedInt32Array([0, 1, 2, 3]))
	var region := NavigationRegion3D.new()
	region.name = "LowerNavigationRegion"
	region.navigation_mesh = mesh
	parent.add_child(region)
	return region


func _create_actor(parent: Node3D, actor_name: String, floor_y: float) -> CharacterBody3D:
	var placement := Node3D.new()
	placement.name = actor_name
	placement.position = Vector3(0.0, floor_y, 0.0)
	parent.add_child(placement)
	var actor := ACTOR_SCRIPT.new()
	if not actor.configure(actor_name, placement):
		_fail("R20_NPC_P1_ACTOR_CONFIGURE_FAILED")
		return null
	return actor


func _probe_scaled_visual(parent: Node3D) -> bool:
	var placement := Node3D.new()
	placement.name = "ScaledVisual"
	placement.position = Vector3(1.0, LOWER_FLOOR_Y, 1.0)
	placement.rotation = Vector3(0.1, 0.35, -0.05)
	placement.scale = Vector3(2.0, 0.5, 3.0)
	parent.add_child(placement)
	var visual_transform := placement.global_transform
	var actor := ACTOR_SCRIPT.new()
	if not actor.configure("ScaledActor", placement):
		_fail("R20_NPC_P1_SCALED_ACTOR_CONFIGURE_FAILED")
		return false
	if not actor.global_basis.get_scale().is_equal_approx(Vector3.ONE):
		_fail("R20_NPC_P1_PHYSICS_ROOT_SCALE_INHERITED")
		return false
	if not placement.global_transform.is_equal_approx(visual_transform):
		_fail("R20_NPC_P1_VISUAL_TRANSFORM_CHANGED")
		return false
	var collision := actor.get_node_or_null("NpcCapsule") as CollisionShape3D
	if collision == null or not collision.global_basis.get_scale().is_equal_approx(Vector3.ONE):
		_fail("R20_NPC_P1_CAPSULE_SCALE_INHERITED")
		return false
	actor.queue_free()
	return true


func _fail(code: String) -> void:
	printerr(code)
	quit(2)
