class_name MatrixOasisSceneComposer
extends RefCounted

const WORLD_COLLISION_LAYER := 1


static func compose(prepared_scene: MatrixOasisPreparedScene) -> Dictionary:
	if prepared_scene == null:
		return _failure()
	var scene := prepared_scene._copy_scene_for_runtime()
	if typeof(scene) != TYPE_DICTIONARY or typeof(scene.get("placements")) != TYPE_ARRAY or typeof(scene.get("nodeBindings")) != TYPE_ARRAY:
		return _failure()
	var root := Node3D.new()
	root.name = "ScenePlacements"
	var placements := {}
	for placement_value in scene["placements"]:
		if typeof(placement_value) != TYPE_DICTIONARY:
			root.free()
			return _failure()
		var placement: Dictionary = placement_value
		var visual := prepared_scene._instantiate_visual_for_runtime(placement["visualAssetId"])
		if visual == null:
			root.free()
			return _failure()
		var placement_root := Node3D.new()
		placement_root.name = placement["id"]
		_apply_transform(placement_root, placement["transform"])
		visual.name = "Visual"
		placement_root.add_child(visual)
		var colliders: Array[StaticBody3D] = []
		if placement["colliderAssetId"] != null:
			var faces := prepared_scene._collider_faces_for_runtime(placement["colliderAssetId"])
			if faces.is_empty() or faces.size() % 3 != 0:
				placement_root.free()
				root.free()
				return _failure()
			var shape := ConcavePolygonShape3D.new()
			shape.set_faces(faces)
			var collision := CollisionShape3D.new()
			collision.name = "Collision"
			collision.shape = shape
			var body := StaticBody3D.new()
			body.name = "StaticCollider"
			body.collision_layer = 0
			body.collision_mask = 0
			body.add_child(collision)
			placement_root.add_child(body)
			colliders.append(body)
		placement_root.visible = false
		root.add_child(placement_root)
		placements[placement["id"]] = {"node": placement_root, "colliders": colliders}
	var bindings := {}
	for binding_value in scene["nodeBindings"]:
		if typeof(binding_value) != TYPE_DICTIONARY:
			root.free()
			return _failure()
		var binding: Dictionary = binding_value
		bindings[binding["nodeId"]] = binding.duplicate(true)
	var world := MatrixOasisComposedScene.new(root, placements, bindings)
	var result := {"ok": true, "world": world}
	result.make_read_only()
	return result


static func anchor_transform(anchor: Dictionary) -> Transform3D:
	var position: Array = anchor["positionMm"]
	var yaw := deg_to_rad(float(anchor["yawMilliDegrees"]) / 1000.0)
	return Transform3D(
		Basis(Vector3.UP, yaw),
		Vector3(float(position[0]), float(position[1]), float(position[2])) / 1000.0,
	)


static func _apply_transform(node: Node3D, transform: Dictionary) -> void:
	var position: Array = transform["positionMm"]
	var rotation: Array = transform["rotationMilliDegrees"]
	var scale: Array = transform["scalePermille"]
	node.position = Vector3(float(position[0]), float(position[1]), float(position[2])) / 1000.0
	node.rotation_degrees = Vector3(float(rotation[0]), float(rotation[1]), float(rotation[2])) / 1000.0
	node.scale = Vector3(float(scale[0]), float(scale[1]), float(scale[2])) / 1000.0


static func _failure() -> Dictionary:
	var diagnostic := {"phase": "runtime", "severity": "error", "code": "PACK_GODOT_SCENE_INTERNAL_ERROR", "path": "/scenePack", "message": "PACK_GODOT_SCENE_INTERNAL_ERROR"}
	diagnostic.make_read_only()
	var diagnostics := [diagnostic]
	diagnostics.make_read_only()
	var result := {"ok": false, "diagnostics": diagnostics}
	result.make_read_only()
	return result
