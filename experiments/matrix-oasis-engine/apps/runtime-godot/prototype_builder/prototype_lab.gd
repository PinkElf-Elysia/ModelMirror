extends Node3D

const READY_MARKER := "MATRIX_OASIS_R10_PROTOTYPE_READY"
const ENVIRONMENT_ARGUMENT := "--matrix-oasis-environment-bundle="
const SMOKE_ARGUMENT := "--matrix-oasis-prototype-smoke"
const SCENE_LAB := preload("res://scene_binding/scene_lab.tscn")

var _smoke_requested := false


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
	if not (visual is Node3D):
		scene_lab.queue_free()
		_fail("PACK_GODOT_PROTOTYPE_INTERNAL_ERROR")
		return
	(visual as Node3D).visible = false
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


func _complete_smoke() -> void:
	await get_tree().process_frame
	await get_tree().process_frame
	get_tree().quit(0)


func _fail(code: String) -> void:
	printerr(code if code.begins_with("PACK_") or code.begins_with("GODOT_") else "PACK_GODOT_PROTOTYPE_INTERNAL_ERROR")
	get_tree().quit(1)
