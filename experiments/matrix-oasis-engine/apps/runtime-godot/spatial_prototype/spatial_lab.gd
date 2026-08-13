extends Node3D

const READY_MARKER := "MATRIX_OASIS_R11_SPATIAL_READY"
const SMOKE_MARKER := "MATRIX_OASIS_R11_SPATIAL_SMOKE_OK"
const ASSEMBLY_ARGUMENT := "--matrix-oasis-spatial-assembly="
const RESOURCE_ARGUMENT := "--matrix-oasis-spatial-resource="
const SMOKE_ARGUMENT := "--matrix-oasis-spatial-smoke"
const SCENE_LAB := preload("res://scene_binding/scene_lab.tscn")
const SPLAT_GUARD_SCRIPT := preload("res://spatial_prototype/spatial_splat_guard.gd")
const SPLAT_NODE_SCRIPT := preload("res://addons/gdgs/runtime/nodes/gaussian_splat_node.gd")
const COMPOSITOR_EFFECT_SCRIPT := preload("res://addons/gdgs/runtime/compositor/gaussian_compositor_effect.gd")

var _scene_lab: MatrixOasisSceneLab
var _spatial_root: Node3D
var _splat_node: Node3D
var _compositor: Compositor
var _compositor_effect: CompositorEffect
var _smoke_requested := false


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
		else:
			artifact_arguments.append(argument)
	if assembly_path.is_empty() or resource_path.is_empty():
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
	var scene_lab := SCENE_LAB.instantiate() as MatrixOasisSceneLab
	if scene_lab == null:
		_fail("PACK_GODOT_SPATIAL_INTERNAL_ERROR")
		return
	scene_lab.set_prepared_for_test(loaded["prepared_runtime"], loaded["prepared_scene"])
	add_child(scene_lab)
	if not _install_spatial_environment(scene_lab, spatial["assembly"], spatial["gaussian"]):
		scene_lab.queue_free()
		_fail("PACK_GODOT_SPATIAL_INTERNAL_ERROR")
		return
	_scene_lab = scene_lab
	print(READY_MARKER)
	if _smoke_requested:
		_complete_smoke.call_deferred()


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
	# GaussianSplatNode has an upstream editor default orientation. Overwriting
	# after enter_tree makes the signed R11 assembly the only runtime transform.
	_apply_splat_transform(splat, assembly["transforms"]["splat"], assembly["transforms"]["eulerOrder"])
	var effect := COMPOSITOR_EFFECT_SCRIPT.new() as CompositorEffect
	if effect == null:
		root.queue_free()
		return false
	var compositor := Compositor.new()
	compositor.compositor_effects = [effect]
	camera.compositor = compositor
	_spatial_root = root
	_splat_node = splat
	_compositor = compositor
	_compositor_effect = effect
	return true


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


func _fail(code: String) -> void:
	printerr(code if code.begins_with("PACK_") or code.begins_with("GODOT_") else "PACK_GODOT_SPATIAL_INTERNAL_ERROR")
	get_tree().quit(1)
