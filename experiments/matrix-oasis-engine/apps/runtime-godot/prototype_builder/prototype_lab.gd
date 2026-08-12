extends Node3D

const READY_MARKER := "MATRIX_OASIS_R10_PROTOTYPE_READY"
const ENVIRONMENT_ARGUMENT := "--matrix-oasis-environment-bundle="
const SMOKE_ARGUMENT := "--matrix-oasis-prototype-smoke"
const SCENE_LAB := preload("res://scene_binding/scene_lab.tscn")
const TARGET_FLOOR_SPAN_METERS := 30.0
const MAX_ALIGNED_HORIZONTAL_SPAN_METERS := 90.0
const SAFETY_FLOOR_THICKNESS_METERS := 0.1

var _smoke_requested := false
var _scene_lab: MatrixOasisSceneLab


func _ready() -> void:
	var environment_path := ""
	var artifact_arguments := PackedStringArray()
	for argument in OS.get_cmdline_user_args():
		if argument.begins_with(ENVIRONMENT_ARGUMENT):
			if not environment_path.is_empty():
				_fail("PACK_GODOT_PROTOTYPE_ARGUMENT_INVALID")
				return
			environment_path = argument.substr(ENVIRONMENT_ARGUMENT.length())
		elif argument == SMOKE_ARGUMENT:
			if _smoke_requested:
				_fail("PACK_GODOT_PROTOTYPE_ARGUMENT_INVALID")
				return
			_smoke_requested = true
		else:
			artifact_arguments.append(argument)
	if environment_path.is_empty() or not environment_path.is_absolute_path():
		_fail("PACK_GODOT_PROTOTYPE_ARGUMENT_INVALID")
		return
	var loaded := MatrixOasisSceneArtifactLoader.load_from_arguments(artifact_arguments)
	if not loaded["ok"]:
		_fail(loaded["diagnostics"][0]["code"])
		return
	var environment_loaded := MatrixOasisPrototypeEnvironmentLoader.load_from_path(environment_path, loaded["prepared_scene"])
	if not environment_loaded["ok"]:
		_fail(environment_loaded["diagnostics"][0]["code"])
		return
	var scene_lab := SCENE_LAB.instantiate() as MatrixOasisSceneLab
	if scene_lab == null or not _apply_panorama(scene_lab, environment_loaded["image"]):
		if scene_lab != null:
			scene_lab.free()
		_fail("PACK_GODOT_PROTOTYPE_INTERNAL_ERROR")
		return
	scene_lab.set_prepared_for_test(loaded["prepared_runtime"], loaded["prepared_scene"])
	add_child(scene_lab)
	var placement := scene_lab.get_node_or_null("SceneWorld/ScenePlacements/" + environment_loaded["environment_placement_id"])
	var visual := placement.get_node_or_null("Visual") if placement != null else null
	if not (visual is Node3D) or not _align_environment_collider(placement, scene_lab.player.global_position) or not _add_safety_floor(scene_lab):
		scene_lab.queue_free()
		_fail("PACK_GODOT_PROTOTYPE_INTERNAL_ERROR")
		return
	(visual as Node3D).visible = false
	_scene_lab = scene_lab
	print(READY_MARKER)
	if _smoke_requested:
		_complete_smoke.call_deferred()


func _apply_panorama(scene_lab: MatrixOasisSceneLab, image: Image) -> bool:
	var world_environment := scene_lab.get_node_or_null("WorldEnvironment") as WorldEnvironment
	if world_environment == null or image == null or image.is_empty():
		return false
	var texture := ImageTexture.create_from_image(image)
	if texture == null:
		return false
	var material := PanoramaSkyMaterial.new()
	material.panorama = texture
	var sky := Sky.new()
	sky.sky_material = material
	var environment := Environment.new()
	environment.background_mode = Environment.BG_SKY
	environment.sky = sky
	environment.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	environment.reflected_light_source = Environment.REFLECTION_SOURCE_SKY
	environment.ambient_light_energy = 0.8
	world_environment.environment = environment
	return true


func _align_environment_collider(placement: Node3D, player_spawn: Vector3) -> bool:
	if placement == null:
		return false
	var collision := placement.get_node_or_null("StaticCollider/Collision") as CollisionShape3D
	var shape := collision.shape as ConcavePolygonShape3D if collision != null else null
	if shape == null:
		return false
	var faces := shape.get_faces()
	if faces.size() < 3 or faces.size() % 3 != 0:
		return false
	var minimum := faces[0]
	var maximum := faces[0]
	for vertex in faces:
		minimum = minimum.min(vertex)
		maximum = maximum.max(vertex)
	var span := maximum - minimum
	var narrow_horizontal_span := minf(span.x, span.z)
	if narrow_horizontal_span <= 0.01:
		return false
	var uniform_scale := TARGET_FLOOR_SPAN_METERS / narrow_horizontal_span
	if not is_finite(uniform_scale) or uniform_scale <= 0.0 or maxf(span.x, span.z) * uniform_scale > MAX_ALIGNED_HORIZONTAL_SPAN_METERS:
		return false
	var center := (minimum + maximum) * 0.5
	var sample_x := center.x + player_spawn.x / uniform_scale
	var sample_z := center.z + player_spawn.z / uniform_scale
	var floor_height := _lowest_surface_height(faces, sample_x, sample_z)
	if not is_finite(floor_height):
		floor_height = minimum.y
	placement.scale = Vector3.ONE * uniform_scale
	placement.position = Vector3(-center.x * uniform_scale, -floor_height * uniform_scale, -center.z * uniform_scale)
	return true


func _lowest_surface_height(faces: PackedVector3Array, sample_x: float, sample_z: float) -> float:
	var lowest := INF
	for index in range(0, faces.size(), 3):
		var a := faces[index]
		var b := faces[index + 1]
		var c := faces[index + 2]
		var denominator := (b.z - c.z) * (a.x - c.x) + (c.x - b.x) * (a.z - c.z)
		if absf(denominator) <= 0.000001:
			continue
		var weight_a := ((b.z - c.z) * (sample_x - c.x) + (c.x - b.x) * (sample_z - c.z)) / denominator
		var weight_b := ((c.z - a.z) * (sample_x - c.x) + (a.x - c.x) * (sample_z - c.z)) / denominator
		var weight_c := 1.0 - weight_a - weight_b
		if weight_a >= -0.000001 and weight_b >= -0.000001 and weight_c >= -0.000001:
			lowest = minf(lowest, weight_a * a.y + weight_b * b.y + weight_c * c.y)
	return lowest


func _add_safety_floor(scene_lab: MatrixOasisSceneLab) -> bool:
	var world := scene_lab.get_node_or_null("SceneWorld") as Node3D
	if world == null or world.get_node_or_null("R10SafetyFloor") != null:
		return false
	var shape := BoxShape3D.new()
	shape.size = Vector3(TARGET_FLOOR_SPAN_METERS, SAFETY_FLOOR_THICKNESS_METERS, TARGET_FLOOR_SPAN_METERS)
	var collision := CollisionShape3D.new()
	collision.name = "Collision"
	collision.shape = shape
	var body := StaticBody3D.new()
	body.name = "R10SafetyFloor"
	body.collision_layer = MatrixOasisSceneComposer.WORLD_COLLISION_LAYER
	body.collision_mask = 0
	body.position = Vector3(0.0, -SAFETY_FLOOR_THICKNESS_METERS, 0.0)
	body.add_child(collision)
	world.add_child(body)
	return true


func _complete_smoke() -> void:
	if _scene_lab == null or _scene_lab.player == null:
		_fail("PACK_GODOT_PROTOTYPE_MOVEMENT_SMOKE_FAILED")
		return
	await get_tree().physics_frame
	await get_tree().physics_frame
	var start := _scene_lab.player.global_position
	_scene_lab.player.set_synthetic_move_input(Vector2.RIGHT)
	for _frame in range(30):
		await get_tree().physics_frame
	_scene_lab.player.clear_synthetic_move_input()
	var finish := _scene_lab.player.global_position
	var horizontal_distance := Vector2(finish.x - start.x, finish.z - start.z).length()
	if horizontal_distance < 0.25 or absf(finish.y - start.y) > 0.75:
		_fail("PACK_GODOT_PROTOTYPE_MOVEMENT_SMOKE_FAILED")
		return
	get_tree().quit(0)


func _fail(code: String) -> void:
	printerr(code if code.begins_with("PACK_") or code.begins_with("GODOT_") else "PACK_GODOT_PROTOTYPE_INTERNAL_ERROR")
	get_tree().quit(1)
