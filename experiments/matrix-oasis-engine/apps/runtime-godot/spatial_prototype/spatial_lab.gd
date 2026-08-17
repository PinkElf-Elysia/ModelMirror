extends Node3D

const READY_MARKER := "MATRIX_OASIS_R11_SPATIAL_READY"
const SMOKE_MARKER := "MATRIX_OASIS_R11_SPATIAL_SMOKE_OK"
const QUALIFICATION_MARKER := "MATRIX_OASIS_R11_SPATIAL_QUALIFICATION_JSON:"
const ASSEMBLY_ARGUMENT := "--matrix-oasis-spatial-assembly="
const RESOURCE_ARGUMENT := "--matrix-oasis-spatial-resource="
const SMOKE_ARGUMENT := "--matrix-oasis-spatial-smoke"
const QUALIFICATION_ARGUMENT := "--matrix-oasis-spatial-qualification"
const CAPTURE_ARGUMENT := "--matrix-oasis-spatial-capture"
const QUALIFICATION_WARMUP_FRAMES := 120
const QUALIFICATION_SAMPLE_FRAMES := 300
const SCENE_LAB := preload("res://spatial_prototype/spatial_scene_lab.tscn")
const SPLAT_GUARD_SCRIPT := preload("res://spatial_prototype/spatial_splat_guard.gd")
const SPLAT_NODE_SCRIPT := preload("res://addons/gdgs/runtime/nodes/gaussian_splat_node.gd")
const COMPOSITOR_EFFECT_SCRIPT := preload("res://addons/gdgs/runtime/compositor/gaussian_compositor_effect.gd")

var _scene_lab: MatrixOasisSceneLab
var _spatial_root: Node3D
var _splat_node: Node3D
var _compositor: Compositor
var _compositor_effect: CompositorEffect
var _smoke_requested := false
var _qualification_requested := false
var _capture_requested := false


func _ready() -> void:
	var assembly_path := ""
	var resource_path := ""
	var artifact_arguments := PackedStringArray()
	for argument in OS.get_cmdline_user_args():
		if argument.begins_with(ASSEMBLY_ARGUMENT):
			if not assembly_path.is_empty():
				_fail("PACK_GODOT_SPATIAL_ARGUMENT_INVALID")
				return
			assembly_path = argument.substr(ASSEMBLY_ARGUMENT.length())
		elif argument.begins_with(RESOURCE_ARGUMENT):
			if not resource_path.is_empty():
				_fail("PACK_GODOT_SPATIAL_ARGUMENT_INVALID")
				return
			resource_path = argument.substr(RESOURCE_ARGUMENT.length())
		elif argument == SMOKE_ARGUMENT:
			if _smoke_requested:
				_fail("PACK_GODOT_SPATIAL_ARGUMENT_INVALID")
				return
			_smoke_requested = true
		elif argument == QUALIFICATION_ARGUMENT:
			if _qualification_requested:
				_fail("PACK_GODOT_SPATIAL_ARGUMENT_INVALID")
				return
			_qualification_requested = true
		elif argument == CAPTURE_ARGUMENT:
			if _capture_requested:
				_fail("PACK_GODOT_SPATIAL_ARGUMENT_INVALID")
				return
			_capture_requested = true
		else:
			artifact_arguments.append(argument)
	if assembly_path.is_empty() or resource_path.is_empty() or int(_smoke_requested) + int(_qualification_requested) + int(_capture_requested) > 1:
		_fail("PACK_GODOT_SPATIAL_ARGUMENT_INVALID")
		return
	var compute: Dictionary = SPLAT_GUARD_SCRIPT.require_compute(self)
	if not compute["ok"]:
		_fail(compute["code"])
		return
	var loaded := MatrixOasisSceneArtifactLoader.load_from_arguments(artifact_arguments)
	if not loaded["ok"]:
		_fail(loaded["diagnostics"][0]["code"])
		return
	var spatial := MatrixOasisSpatialAssemblyLoader.load_from_path(
		assembly_path,
		resource_path,
		loaded["prepared_runtime"],
		loaded["prepared_scene"],
	)
	if not spatial["ok"]:
		_fail(spatial["diagnostics"][0]["code"])
		return
	var scene_lab := SCENE_LAB.instantiate() as MatrixOasisSpatialSceneLab
	if scene_lab == null:
		_fail("PACK_GODOT_SPATIAL_INTERNAL_ERROR")
		return
	scene_lab.set_prepared_for_test(loaded["prepared_runtime"], loaded["prepared_scene"])
	if spatial["assembly"]["transforms"].has("nodeBindingLayout") and not scene_lab.set_spatial_binding_layout(
		spatial["assembly"]["transforms"]["nodeBindingLayout"],
		spatial["assembly"]["transforms"]["root"],
		spatial["assembly"]["transforms"]["eulerOrder"],
	):
		_fail("PACK_GODOT_SPATIAL_INTERNAL_ERROR")
		return
	if spatial["assembly"]["transforms"].has("navigation") and not scene_lab.set_spatial_navigation(
		spatial["assembly"]["transforms"]["navigation"],
	):
		_fail("PACK_GODOT_SPATIAL_INTERNAL_ERROR")
		return
	add_child(scene_lab)
	if not _install_spatial_environment(scene_lab, spatial["assembly"], spatial["gaussian"]):
		scene_lab.queue_free()
		_fail("PACK_GODOT_SPATIAL_INTERNAL_ERROR")
		return
	if spatial["assembly"]["transforms"].has("navigation") and not scene_lab.activate_spatial_navigation():
		scene_lab.queue_free()
		_fail("PACK_GODOT_SPATIAL_INTERNAL_ERROR")
		return
	_scene_lab = scene_lab
	if _capture_requested:
		scene_lab.player.input_enabled = false
		scene_lab.player.set_process_unhandled_input(false)
		scene_lab.player.process_mode = Node.PROCESS_MODE_DISABLED
		scene_lab.player.physics_interpolation_mode = Node.PHYSICS_INTERPOLATION_MODE_OFF
		var capture_camera := scene_lab.get_node_or_null("FirstPersonPlayer/Head/Camera3D") as Camera3D
		if capture_camera == null:
			_fail("PACK_GODOT_SPATIAL_INTERNAL_ERROR")
			return
		capture_camera.physics_interpolation_mode = Node.PHYSICS_INTERPOLATION_MODE_OFF
		scene_lab.player.reset_physics_interpolation()
		capture_camera.reset_physics_interpolation()
		Input.mouse_mode = Input.MOUSE_MODE_VISIBLE
	print(READY_MARKER)
	if _smoke_requested:
		_complete_smoke.call_deferred()
	elif _qualification_requested:
		_complete_qualification.call_deferred()


func _install_spatial_environment(scene_lab: MatrixOasisSceneLab, assembly: Dictionary, gaussian: Resource) -> bool:
	var scene_world := scene_lab.get_node_or_null("SceneWorld") as Node3D
	var placement_id: String = assembly["environment"]["collider"]["placementId"]
	var placement := scene_lab.get_node_or_null("SceneWorld/ScenePlacements/" + placement_id) as Node3D
	var visual := placement.get_node_or_null("Visual") as Node3D if placement != null else null
	var camera := scene_lab.get_node_or_null("FirstPersonPlayer/Head/Camera3D") as Camera3D
	var world_environment := scene_lab.get_node_or_null("WorldEnvironment") as WorldEnvironment
	if scene_world == null or placement == null or visual == null or camera == null or world_environment == null or gaussian == null:
		return false
	if camera.compositor != null or world_environment.environment == null or world_environment.environment.sky != null:
		return false
	var root := Node3D.new()
	root.name = "R11SpatialEnvironment"
	scene_world.add_child(root)
	_apply_root_transform(root, assembly["transforms"]["root"], assembly["transforms"]["eulerOrder"])
	var has_placement_layout: bool = assembly["transforms"].has("placementLayout")
	if has_placement_layout and not _apply_placement_layout(scene_lab, placement_id, root, assembly["transforms"]["placementLayout"]):
		root.queue_free()
		return false
	if not _apply_placement_ground_target(scene_lab, placement_id, root, assembly["transforms"]["placementGroundTargetMm"], has_placement_layout):
		root.queue_free()
		return false
	if not _install_walkable_boundary(root, assembly["transforms"]["walkableEnvelope"], assembly["transforms"].get("navigation", null)):
		root.queue_free()
		return false
	placement.reparent(root, false)
	_apply_collider_transform(placement, assembly["transforms"]["collider"])
	visual.visible = false
	var splat: Node3D = SPLAT_NODE_SCRIPT.new() as Node3D
	if splat == null:
		root.queue_free()
		return false
	splat.name = "SpatialSplat"
	splat.set("gaussian", gaussian)
	root.add_child(splat)
	# GaussianSplatNode assumes conventional Y-down PLY and applies a default
	# -180 degree Z correction on enter_tree. This source is SPZ v2 RUB (Y-up),
	# so reapplying the signed zero rotation after enter_tree is required and
	# makes the R11 assembly the only runtime transform.
	_apply_splat_transform(splat, assembly["transforms"]["splat"], assembly["transforms"]["eulerOrder"])
	var effect := COMPOSITOR_EFFECT_SCRIPT.new() as CompositorEffect
	if effect == null:
		root.queue_free()
		return false
	_apply_renderer_settings(effect, assembly["environment"]["renderer"])
	var compositor := Compositor.new()
	compositor.compositor_effects = [effect]
	camera.compositor = compositor
	_spatial_root = root
	_splat_node = splat
	_compositor = compositor
	_compositor_effect = effect
	return true


func _install_walkable_boundary(root: Node3D, value: Dictionary, navigation: Variant) -> bool:
	if value["profile"] == "collider-agent-navigation-component-v7":
		if typeof(navigation) != TYPE_DICTIONARY or navigation.get("profile") != "collider-agent-grid-v1":
			return false
		var evidence := Node3D.new()
		evidence.name = "R11NavigationEvidence"
		root.add_child(evidence)
		return true
	var minimum := _millimeters(value["minimumMm"])
	var maximum := _millimeters(value["maximumMm"])
	var wall_thickness := float(value["wallThicknessMm"]) / 1000.0
	var floor_thickness := float(value["floorThicknessMm"]) / 1000.0
	var size := maximum - minimum
	if size.x <= wall_thickness or size.y < 3.0 or size.z <= wall_thickness or wall_thickness <= 0.0 or floor_thickness <= 0.0:
		return false
	var enclosure := Node3D.new()
	enclosure.name = "R11WalkableEnvelope"
	root.add_child(enclosure)
	_add_static_box(enclosure, "Floor", Vector3((minimum.x + maximum.x) * 0.5, minimum.y - floor_thickness * 0.5, (minimum.z + maximum.z) * 0.5), Vector3(size.x, floor_thickness, size.z))
	_add_static_box(enclosure, "WestWall", Vector3(minimum.x - wall_thickness * 0.5, size.y * 0.5, (minimum.z + maximum.z) * 0.5), Vector3(wall_thickness, size.y, size.z + wall_thickness * 2.0))
	_add_static_box(enclosure, "EastWall", Vector3(maximum.x + wall_thickness * 0.5, size.y * 0.5, (minimum.z + maximum.z) * 0.5), Vector3(wall_thickness, size.y, size.z + wall_thickness * 2.0))
	_add_static_box(enclosure, "NorthWall", Vector3((minimum.x + maximum.x) * 0.5, size.y * 0.5, minimum.z - wall_thickness * 0.5), Vector3(size.x, size.y, wall_thickness))
	_add_static_box(enclosure, "SouthWall", Vector3((minimum.x + maximum.x) * 0.5, size.y * 0.5, maximum.z + wall_thickness * 0.5), Vector3(size.x, size.y, wall_thickness))
	return enclosure.get_child_count() == 5


func _apply_placement_ground_target(scene_lab: MatrixOasisSceneLab, environment_placement_id: String, spatial_root: Node3D, target_mm: int, use_layout_height: bool) -> bool:
	var placements := scene_lab.get_node_or_null("SceneWorld/ScenePlacements") as Node3D
	if placements == null or spatial_root == null or target_mm != 150:
		return false
	var root_target_global_y := spatial_root.to_global(Vector3.ZERO).y + float(target_mm) / 1000.0
	var adjusted := 0
	for child in placements.get_children():
		if child is Node3D and child.name != environment_placement_id:
			var placement := child as Node3D
			var minimum_y := _mesh_minimum_global_y(placement)
			if not is_finite(minimum_y):
				return false
			var layout_target_global_y := maxf(spatial_root.to_global(Vector3.ZERO).y, placement.global_position.y) + float(target_mm) / 1000.0
			var target_global_y := layout_target_global_y if use_layout_height else root_target_global_y
			placement.global_position.y += target_global_y - minimum_y
			adjusted += 1
	return adjusted >= 1


func _apply_placement_layout(scene_lab: MatrixOasisSceneLab, environment_placement_id: String, spatial_root: Node3D, layout: Array) -> bool:
	var placements := scene_lab.get_node_or_null("SceneWorld/ScenePlacements") as Node3D
	if placements == null or spatial_root == null:
		return false
	var expected: Dictionary = {}
	for child in placements.get_children():
		if child is Node3D and child.name != environment_placement_id:
			expected[String(child.name)] = child
	if expected.size() != layout.size():
		return false
	for entry: Dictionary in layout:
		var placement_id: String = entry["placementId"]
		var placement := expected.get(placement_id) as Node3D
		if placement == null:
			return false
		var local_target := _millimeters(entry["positionMm"])
		var global_target := spatial_root.to_global(local_target)
		placement.global_position = global_target
		expected.erase(placement_id)
	return expected.is_empty()


func _mesh_minimum_global_y(root: Node3D) -> float:
	var minimum_y := INF
	var pending: Array[Node] = [root]
	while not pending.is_empty():
		var current: Node = pending.pop_back()
		if current is MeshInstance3D:
			var mesh_instance := current as MeshInstance3D
			if mesh_instance.mesh != null:
				var bounds := mesh_instance.get_aabb()
				for corner_index in range(8):
					var corner := bounds.position + Vector3(
						bounds.size.x if (corner_index & 1) != 0 else 0.0,
						bounds.size.y if (corner_index & 2) != 0 else 0.0,
						bounds.size.z if (corner_index & 4) != 0 else 0.0,
					)
					minimum_y = minf(minimum_y, mesh_instance.to_global(corner).y)
		for nested in current.get_children():
			pending.push_back(nested)
	return minimum_y


func _add_static_box(parent: Node3D, node_name: String, center: Vector3, size: Vector3) -> void:
	var shape := BoxShape3D.new()
	shape.size = size
	var collision := CollisionShape3D.new()
	collision.name = "Collision"
	collision.shape = shape
	var body := StaticBody3D.new()
	body.name = node_name
	body.position = center
	body.collision_layer = 1
	body.collision_mask = 0
	body.add_child(collision)
	parent.add_child(body)


func _apply_renderer_settings(effect: CompositorEffect, value: Dictionary) -> void:
	effect.set("depth_bias", float(value["depthBiasMicros"]) / 1000000.0)
	effect.set("depth_test_min_alpha", float(value["depthTestMinAlphaPermille"]) / 1000.0)
	effect.set("depth_capture_alpha", float(value["depthCaptureAlphaPermille"]) / 1000.0)


func _apply_root_transform(node: Node3D, value: Dictionary, euler_order: String) -> void:
	if euler_order != "YXZ":
		return
	node.rotation_order = EULER_ORDER_YXZ
	node.position = _millimeters(value["translationMm"])
	node.rotation_degrees = _milli_degrees(value["rotationMilliDegrees"])
	node.scale = Vector3.ONE


func _apply_collider_transform(node: Node3D, value: Dictionary) -> void:
	node.position = _millimeters(value["localTranslationMm"])
	node.rotation_order = EULER_ORDER_YXZ
	node.rotation_degrees = Vector3.ZERO
	var uniform_scale := float(value["scaleMicros"]) / 1000000.0
	node.scale = Vector3.ONE * uniform_scale


func _apply_splat_transform(node: Node3D, value: Dictionary, euler_order: String) -> void:
	if euler_order != "YXZ":
		return
	node.position = _millimeters(value["localTranslationMm"])
	node.rotation_order = EULER_ORDER_YXZ
	node.rotation_degrees = _milli_degrees(value["localRotationMilliDegrees"])
	var uniform_scale := float(value["scaleMicros"]) / 1000000.0
	node.scale = Vector3.ONE * uniform_scale


func _millimeters(value: Array) -> Vector3:
	return Vector3(float(value[0]), float(value[1]), float(value[2])) / 1000.0


func _milli_degrees(value: Array) -> Vector3:
	return Vector3(float(value[0]), float(value[1]), float(value[2])) / 1000.0


func _complete_smoke() -> void:
	await get_tree().process_frame
	await get_tree().process_frame
	if _scene_lab == null or _scene_lab.player == null or _spatial_root == null or _splat_node == null or _splat_node.get("gaussian") == null or _compositor == null or _compositor_effect == null:
		_fail("PACK_GODOT_SPATIAL_SMOKE_FAILED")
		return
	var camera := _scene_lab.get_node_or_null("FirstPersonPlayer/Head/Camera3D") as Camera3D
	if camera == null or camera.compositor != _compositor:
		_fail("PACK_GODOT_SPATIAL_SMOKE_FAILED")
		return
	var start := _scene_lab.player.global_position
	_scene_lab.player.set_synthetic_move_input(Vector2.RIGHT)
	for _frame in range(30):
		await get_tree().physics_frame
	_scene_lab.player.clear_synthetic_move_input()
	var finish := _scene_lab.player.global_position
	if Vector2(finish.x - start.x, finish.z - start.z).length() < 0.25:
		_fail("PACK_GODOT_SPATIAL_SMOKE_FAILED")
		return
	print(SMOKE_MARKER)
	get_tree().quit(0)


func _complete_qualification() -> void:
	if _scene_lab == null or _splat_node == null or _splat_node.get("gaussian") == null:
		_fail("PACK_GODOT_SPATIAL_QUALIFICATION_FAILED")
		return
	for _frame in range(QUALIFICATION_WARMUP_FRAMES):
		await RenderingServer.frame_post_draw
	var samples := PackedInt64Array()
	samples.resize(QUALIFICATION_SAMPLE_FRAMES)
	var previous := Time.get_ticks_usec()
	var frames_before := Engine.get_frames_drawn()
	for index in range(QUALIFICATION_SAMPLE_FRAMES):
		await RenderingServer.frame_post_draw
		var current := Time.get_ticks_usec()
		samples[index] = maxi(1, current - previous)
		previous = current
	var frames_after := Engine.get_frames_drawn()
	samples.sort()
	var median_usec := (samples[149] + samples[150]) / 2
	var viewport_size := get_viewport().get_visible_rect().size
	var gaussian: Resource = _splat_node.get("gaussian") as Resource
	var report := {
		"qualificationVersion": 1,
		"width": int(viewport_size.x),
		"height": int(viewport_size.y),
		"pointCount": int(gaussian.get("point_count")),
		"warmupFrames": QUALIFICATION_WARMUP_FRAMES,
		"sampleFrames": QUALIFICATION_SAMPLE_FRAMES,
		"drawnFrames": frames_after - frames_before,
		"medianFrameUsec": median_usec,
		"medianFpsMilli": int(1000000000.0 / float(median_usec)),
	}
	print(QUALIFICATION_MARKER + JSON.stringify(report))
	get_tree().quit(0)


func _fail(code: String) -> void:
	printerr(code if code.begins_with("PACK_") or code.begins_with("GODOT_") else "PACK_GODOT_SPATIAL_INTERNAL_ERROR")
	get_tree().quit(1)
