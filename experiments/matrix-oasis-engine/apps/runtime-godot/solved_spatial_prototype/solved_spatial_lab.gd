extends Node3D

const READY_MARKER := "MATRIX_OASIS_R14_SOLVED_SPATIAL_READY"
const TRACE_MARKER := "MATRIX_OASIS_R14_SPATIAL_TRACE_JSON:"
const ASSEMBLY_ARGUMENT := "--matrix-oasis-spatial-assembly="
const RESOURCE_ARGUMENT := "--matrix-oasis-spatial-resource="
const FACTS_ARGUMENT := "--matrix-oasis-environment-facts="
const SOLUTION_ARGUMENT := "--matrix-oasis-spatial-solution="
const VERIFICATION_ARGUMENT := "--matrix-oasis-spatial-verification="
const SMOKE_ARGUMENT := "--matrix-oasis-r14-smoke"
const SCENE_LAB := preload("res://solved_spatial_prototype/solved_scene_lab.tscn")
const SPLAT_GUARD_SCRIPT := preload("res://spatial_prototype/spatial_splat_guard.gd")
const SPLAT_NODE_SCRIPT := preload("res://addons/gdgs/runtime/nodes/gaussian_splat_node.gd")
const COMPOSITOR_EFFECT_SCRIPT := preload("res://addons/gdgs/runtime/compositor/gaussian_compositor_effect.gd")
const VISUAL_SUPPORT_BIN_MM := 50
const VISUAL_SUPPORT_MAX_SAMPLES := 120000
const VISUAL_SUPPORT_MIN_BIN_COUNT := 64
const VISUAL_SUPPORT_DENSE_PERMILLE := 200
const VISUAL_SUPPORT_BELOW_MM := 1000
const VISUAL_SUPPORT_ABOVE_MM := 3000
const SCENE_DEPTH_PRIORITY_MARGIN_METERS := 0.20
const SCENE_DEPTH_PRIORITY_MARGIN_MICROS := 200000
const MAX_WALKABLE_NORMAL_Y := 0.7071068

var _scene_lab: MatrixOasisSolvedSpatialSceneLab
var _spatial_root: Node3D
var _splat_node: Node3D
var _compositor: Compositor
var _compositor_effect: CompositorEffect
var _navigation_collision_body: StaticBody3D
var _smoke_requested := false
var _scene_depth_bias := 0.0
var _visual_support_height_mm := 0
var _visual_registration_offset_mm := 0
var _environment_obstruction_face_count := 0
var _visual_safety_box_count := 0


func _ready() -> void:
	var values := {"assembly": "", "facts": "", "resource": "", "solution": "", "verification": ""}
	var artifact_arguments := PackedStringArray()
	for argument in OS.get_cmdline_user_args():
		var captured := false
		for prefix in [ASSEMBLY_ARGUMENT, FACTS_ARGUMENT, RESOURCE_ARGUMENT, SOLUTION_ARGUMENT, VERIFICATION_ARGUMENT]:
			if argument.begins_with(prefix):
				var key: String = {ASSEMBLY_ARGUMENT: "assembly", FACTS_ARGUMENT: "facts", RESOURCE_ARGUMENT: "resource", SOLUTION_ARGUMENT: "solution", VERIFICATION_ARGUMENT: "verification"}[prefix]
				if not values[key].is_empty():
					_fail("PACK_GODOT_SOLVED_SPATIAL_ARGUMENT_INVALID")
					return
				values[key] = argument.substr(prefix.length())
				captured = true
				break
		if captured:
			continue
		if argument == SMOKE_ARGUMENT:
			if _smoke_requested:
				_fail("PACK_GODOT_SOLVED_SPATIAL_ARGUMENT_INVALID")
				return
			_smoke_requested = true
		else:
			artifact_arguments.append(argument)
	if values.values().any(func(value: Variant) -> bool: return str(value).is_empty()):
		_fail("PACK_GODOT_SOLVED_SPATIAL_ARGUMENT_INVALID")
		return
	var compute: Dictionary = SPLAT_GUARD_SCRIPT.require_compute(self)
	if not compute["ok"]:
		_fail(compute["code"])
		return
	var loaded := MatrixOasisSceneArtifactLoader.load_from_arguments(artifact_arguments)
	if not loaded["ok"]:
		_fail(loaded["diagnostics"][0]["code"])
		return
	var spatial := MatrixOasisSpatialAssemblyLoader.load_from_path(values["assembly"], values["resource"], loaded["prepared_runtime"], loaded["prepared_scene"])
	if not spatial["ok"]:
		_fail(spatial["diagnostics"][0]["code"])
		return
	var environment_placement_id: String = spatial["assembly"]["environment"]["collider"]["placementId"]
	var solved := MatrixOasisSpatialSolutionLoader.load_from_paths(values["solution"], values["verification"], values["facts"], values["assembly"], loaded["prepared_runtime"], loaded["prepared_scene"], environment_placement_id)
	if not solved["ok"]:
		_fail(solved["diagnostics"][0]["code"])
		return
	var scene_lab := SCENE_LAB.instantiate() as MatrixOasisSolvedSpatialSceneLab
	if scene_lab == null or not scene_lab.set_solution_for_runtime(solved["solution"], solved["placementIdMap"], solved["runtimeSupportHeightMm"]) or \
		not scene_lab.set_spatial_boundary(spatial["assembly"]["transforms"]["walkableEnvelope"],
			spatial["assembly"]["transforms"]["root"], spatial["assembly"]["transforms"]["eulerOrder"]) or \
		not scene_lab.set_navigation_domain(solved["navigationFaces"]):
		_fail("PACK_GODOT_SOLVED_SPATIAL_INTERNAL_ERROR")
		return
	scene_lab.set_prepared_for_test(loaded["prepared_runtime"], loaded["prepared_scene"])
	add_child(scene_lab)
	if not _install_spatial_environment(scene_lab, spatial["assembly"], spatial["gaussian"], solved["navigationFaces"], solved["navigationBoundaryFaces"], solved["runtimeSupportHeightMm"], solved["visualSafety"]):
		scene_lab.queue_free()
		_fail("PACK_GODOT_SOLVED_SPATIAL_INTERNAL_ERROR")
		return
	if not scene_lab.activate_spatial_boundary():
		scene_lab.queue_free()
		_fail("PACK_GODOT_SOLVED_SPATIAL_INTERNAL_ERROR")
		return
	_scene_lab = scene_lab
	print(READY_MARKER)
	print(TRACE_MARKER + JSON.stringify({"traceVersion": 1, "status": "ready", "placementCount": solved["solution"]["placements"].size(), "nodeContextCount": solved["solution"]["nodeContexts"].size(), "sceneDepthPriorityMarginMicros": SCENE_DEPTH_PRIORITY_MARGIN_MICROS, "visualRegistrationOffsetMm": _visual_registration_offset_mm, "environmentObstructionFaceCount": _environment_obstruction_face_count, "visualSafetyBoxCount": _visual_safety_box_count}))
	if _smoke_requested:
		_complete_smoke.call_deferred()


func _install_spatial_environment(scene_lab: MatrixOasisSolvedSpatialSceneLab, assembly: Dictionary, gaussian: Resource, navigation_faces: PackedVector3Array, navigation_boundary_faces: PackedVector3Array, runtime_support_height_mm: int, visual_safety: Dictionary) -> bool:
	var scene_world := scene_lab.get_node_or_null("SceneWorld") as Node3D
	var placement_id: String = assembly["environment"]["collider"]["placementId"]
	var placement := scene_lab.get_node_or_null("SceneWorld/ScenePlacements/" + placement_id) as Node3D
	var visual := placement.get_node_or_null("Visual") as Node3D if placement != null else null
	var camera := scene_lab.get_node_or_null("FirstPersonPlayer/Head/Camera3D") as Camera3D
	var world_environment := scene_lab.get_node_or_null("WorldEnvironment") as WorldEnvironment
	if scene_world == null or placement == null or visual == null or camera == null or world_environment == null or gaussian == null or camera.compositor != null or world_environment.environment == null or world_environment.environment.sky != null:
		return false
	var root := Node3D.new()
	root.name = "R14SolvedSpatialEnvironment"
	scene_world.add_child(root)
	_apply_root_transform(root, assembly["transforms"]["root"], assembly["transforms"]["eulerOrder"])
	placement.reparent(root, false)
	_apply_collider_transform(placement, assembly["transforms"]["collider"])
	visual.visible = false
	for collision_object: Node in placement.find_children("*", "CollisionObject3D", true, false):
		(collision_object as CollisionObject3D).collision_layer = 0
		(collision_object as CollisionObject3D).collision_mask = 0
	var environment_faces := _collect_faces(visual, placement.global_transform)
	if environment_faces.is_empty():
		root.queue_free()
		return false
	var obstruction_faces := _steep_environment_faces(environment_faces)
	var splat := SPLAT_NODE_SCRIPT.new() as Node3D
	if splat == null:
		root.queue_free()
		return false
	splat.name = "SolvedSpatialSplat"
	splat.set("gaussian", gaussian)
	root.add_child(splat)
	_apply_splat_transform(splat, assembly["transforms"]["splat"], assembly["transforms"]["eulerOrder"])
	if not _register_visual_support(splat, gaussian, scene_lab, runtime_support_height_mm):
		root.queue_free()
		return false
	if _visual_registration_offset_mm != visual_safety["visualRegistrationOffsetMm"]:
		root.queue_free()
		return false
	var visual_safety_faces := _visual_safety_faces(visual_safety)
	if visual_safety_faces.is_empty() != visual_safety["boxes"].is_empty():
		root.queue_free()
		return false
	var navigation_body := _install_navigation_collision(scene_world, navigation_faces,
		navigation_boundary_faces, obstruction_faces, visual_safety_faces)
	if navigation_body == null:
		root.queue_free()
		return false
	var effect := COMPOSITOR_EFFECT_SCRIPT.new() as CompositorEffect
	if effect == null:
		root.queue_free()
		return false
	_scene_depth_bias = float(assembly["environment"]["renderer"]["depthBiasMicros"]) / 1000000.0 - SCENE_DEPTH_PRIORITY_MARGIN_METERS
	effect.set("depth_bias", _scene_depth_bias)
	effect.set("depth_test_min_alpha", float(assembly["environment"]["renderer"]["depthTestMinAlphaPermille"]) / 1000.0)
	effect.set("depth_capture_alpha", float(assembly["environment"]["renderer"]["depthCaptureAlphaPermille"]) / 1000.0)
	var compositor := Compositor.new()
	compositor.compositor_effects = [effect]
	camera.compositor = compositor
	_spatial_root = root
	_splat_node = splat
	_compositor = compositor
	_compositor_effect = effect
	_navigation_collision_body = navigation_body
	_environment_obstruction_face_count = obstruction_faces.size()
	_visual_safety_box_count = visual_safety["boxes"].size()
	return true


func _register_visual_support(splat: Node3D, gaussian: Resource,
	scene_lab: MatrixOasisSolvedSpatialSceneLab, runtime_support_height_mm: int) -> bool:
	var positions: Variant = gaussian.get("xyz")
	if typeof(positions) != TYPE_PACKED_VECTOR3_ARRAY or positions.is_empty() or abs(runtime_support_height_mm) > 1000000:
		return false
	var stride := maxi(1, ceili(float(positions.size()) / float(VISUAL_SUPPORT_MAX_SAMPLES)))
	var histogram: Dictionary = {}
	var maximum_bin_count := 0
	var sampled := 0
	for index in range(0, positions.size(), stride):
		var global_point: Vector3 = splat.to_global(positions[index])
		var height_mm := roundi(global_point.y * 1000.0)
		if height_mm < runtime_support_height_mm - VISUAL_SUPPORT_BELOW_MM or \
			height_mm > runtime_support_height_mm + VISUAL_SUPPORT_ABOVE_MM or \
			not scene_lab._inside_spatial_boundary(global_point):
			continue
		var bin_mm := roundi(float(height_mm) / float(VISUAL_SUPPORT_BIN_MM)) * VISUAL_SUPPORT_BIN_MM
		var count: int = int(histogram.get(bin_mm, 0)) + 1
		histogram[bin_mm] = count
		maximum_bin_count = maxi(maximum_bin_count, count)
		sampled += 1
	if sampled < VISUAL_SUPPORT_MIN_BIN_COUNT or maximum_bin_count < VISUAL_SUPPORT_MIN_BIN_COUNT:
		return false
	var threshold := maxi(VISUAL_SUPPORT_MIN_BIN_COUNT,
		ceili(float(maximum_bin_count * VISUAL_SUPPORT_DENSE_PERMILLE) / 1000.0))
	var bins: Array = histogram.keys()
	bins.sort()
	var selected_bin: Variant = null
	for bin_value: Variant in bins:
		if typeof(bin_value) == TYPE_INT and int(histogram[bin_value]) >= threshold:
			selected_bin = bin_value
			break
	if selected_bin == null:
		return false
	_visual_support_height_mm = int(selected_bin)
	_visual_registration_offset_mm = runtime_support_height_mm - _visual_support_height_mm
	if abs(_visual_registration_offset_mm) > VISUAL_SUPPORT_ABOVE_MM:
		return false
	splat.global_position += Vector3.UP * (float(_visual_registration_offset_mm) / 1000.0)
	return true


func _install_navigation_collision(parent: Node3D, floor_faces: PackedVector3Array,
	boundary_faces: PackedVector3Array, obstruction_faces: PackedVector3Array,
	visual_safety_faces: PackedVector3Array) -> StaticBody3D:
	if floor_faces.is_empty() or floor_faces.size() % 3 != 0 or boundary_faces.is_empty() or boundary_faces.size() % 3 != 0:
		return null
	var faces := floor_faces.duplicate()
	faces.append_array(boundary_faces)
	faces.append_array(obstruction_faces)
	faces.append_array(visual_safety_faces)
	var shape := ConcavePolygonShape3D.new()
	shape.backface_collision = true
	shape.set_faces(faces)
	var collision := CollisionShape3D.new()
	collision.name = "Collision"
	collision.shape = shape
	var body := StaticBody3D.new()
	body.name = "R14VerifiedNavigationCollision"
	body.collision_layer = 1
	body.collision_mask = 0
	body.add_child(collision)
	parent.add_child(body)
	return body


func _visual_safety_faces(value: Dictionary) -> PackedVector3Array:
	var faces := PackedVector3Array()
	for box: Dictionary in value["boxes"]:
		var center := _millimeters(box["centerMm"])
		var half := _millimeters(box["sizeMm"]) * 0.5
		var x0 := center.x - half.x
		var x1 := center.x + half.x
		var y0 := center.y - half.y
		var y1 := center.y + half.y
		var z0 := center.z - half.z
		var z1 := center.z + half.z
		var a := Vector3(x0, y0, z0)
		var b := Vector3(x0, y0, z1)
		var c := Vector3(x1, y0, z1)
		var d := Vector3(x1, y0, z0)
		var e := Vector3(x0, y1, z0)
		var f := Vector3(x0, y1, z1)
		var g := Vector3(x1, y1, z1)
		var h := Vector3(x1, y1, z0)
		faces.append_array(PackedVector3Array([
			a, b, c, a, c, d, e, h, g, e, g, f,
			a, d, h, a, h, e, d, c, g, d, g, h,
			c, b, f, c, f, g, b, a, e, b, e, f,
		]))
	return faces


func _steep_environment_faces(faces: PackedVector3Array) -> PackedVector3Array:
	var output := PackedVector3Array()
	for index in range(0, faces.size(), 3):
		if index + 2 >= faces.size():
			return PackedVector3Array()
		var first: Vector3 = faces[index]
		var normal := (faces[index + 1] - first).cross(faces[index + 2] - first)
		if normal.length_squared() <= 0.000000000001:
			continue
		if absf(normal.normalized().y) < MAX_WALKABLE_NORMAL_Y:
			output.append(first)
			output.append(faces[index + 1])
			output.append(faces[index + 2])
	return output


func _collect_faces(node: Node, parent_transform: Transform3D) -> PackedVector3Array:
	var local_transform := parent_transform
	if node is Node3D:
		local_transform = parent_transform * (node as Node3D).transform
	var faces := PackedVector3Array()
	if node is MeshInstance3D and (node as MeshInstance3D).mesh != null:
		for vertex in (node as MeshInstance3D).mesh.get_faces():
			faces.append(local_transform * vertex)
	for child in node.get_children():
		faces.append_array(_collect_faces(child, local_transform))
	return faces


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
	node.scale = Vector3.ONE * (float(value["scaleMicros"]) / 1000000.0)


func _apply_splat_transform(node: Node3D, value: Dictionary, euler_order: String) -> void:
	if euler_order != "YXZ":
		return
	node.position = _millimeters(value["localTranslationMm"])
	node.rotation_order = EULER_ORDER_YXZ
	node.rotation_degrees = _milli_degrees(value["localRotationMilliDegrees"])
	node.scale = Vector3.ONE * (float(value["scaleMicros"]) / 1000000.0)


func _complete_smoke() -> void:
	await get_tree().process_frame
	await get_tree().physics_frame
	if _scene_lab == null or _scene_lab.player == null or _spatial_root == null or _splat_node == null or _splat_node.get("gaussian") == null or _compositor == null or _compositor_effect == null or _navigation_collision_body == null or absf(float(_compositor_effect.get("depth_bias")) - _scene_depth_bias) > 0.001 or _visual_support_height_mm + _visual_registration_offset_mm != _scene_lab._runtime_support_height_mm or _scene_lab._visible_solution_placement_count_for_test() < 1 or not _scene_lab._runtime_support_consistent_for_test():
		_fail("PACK_GODOT_SOLVED_SPATIAL_SMOKE_FAILED")
		return
	print("MATRIX_OASIS_R14_SOLVED_SPATIAL_SMOKE_OK")
	get_tree().quit(0)


static func _millimeters(value: Array) -> Vector3:
	return Vector3(float(value[0]), float(value[1]), float(value[2])) / 1000.0


static func _milli_degrees(value: Array) -> Vector3:
	return Vector3(float(value[0]), float(value[1]), float(value[2])) / 1000.0


func _fail(code: String) -> void:
	printerr(code if code.begins_with("PACK_") or code.begins_with("GODOT_") else "PACK_GODOT_SOLVED_SPATIAL_INTERNAL_ERROR")
	get_tree().quit(1)
