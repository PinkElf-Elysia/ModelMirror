class_name MatrixOasisComposedScene
extends RefCounted

const WORLD_COLLISION_LAYER := 1

var _root: Node3D
var _placements: Dictionary
var _bindings: Dictionary
var _disposed := false


func _init(root: Node3D, placements: Dictionary, bindings: Dictionary) -> void:
	_root = root
	_placements = placements.duplicate()
	_bindings = bindings.duplicate(true)
	for placement_id in _placements:
		var record: Dictionary = _placements[placement_id]
		record.make_read_only()
	_placements.make_read_only()
	for node_id in _bindings:
		_make_read_only(_bindings[node_id])
	_bindings.make_read_only()


func _root_for_runtime() -> Node3D:
	return null if _disposed or not is_instance_valid(_root) else _root


func _has_binding_for_runtime(node_id: String) -> bool:
	return not _disposed and _bindings.has(node_id)


func _binding_for_runtime(node_id: String) -> Dictionary:
	return _bindings[node_id].duplicate(true) if _has_binding_for_runtime(node_id) else {}


func _can_apply_node_for_runtime(node_id: String) -> bool:
	if not _has_binding_for_runtime(node_id):
		return false
	var visible := {}
	for placement_id in _bindings[node_id]["visiblePlacementIds"]:
		if not _placements.has(placement_id):
			return false
		visible[placement_id] = true
	for placement_id in _placements:
		var record: Dictionary = _placements[placement_id]
		var placement_node := record["node"] as Node3D
		if placement_node == null or not is_instance_valid(placement_node):
			return false
		for body in record["colliders"]:
			if body == null or not is_instance_valid(body):
				return false
	return true


func _apply_node_for_runtime(node_id: String) -> bool:
	if not _can_apply_node_for_runtime(node_id):
		return false
	var visible := {}
	for placement_id in _bindings[node_id]["visiblePlacementIds"]:
		visible[placement_id] = true
	for placement_id in _placements:
		var record: Dictionary = _placements[placement_id]
		var shown := visible.has(placement_id)
		var placement_node := record["node"] as Node3D
		placement_node.visible = shown
		for body in record["colliders"]:
			(body as StaticBody3D).collision_layer = WORLD_COLLISION_LAYER if shown else 0
	return true


func _hide_all_for_runtime() -> void:
	if _disposed:
		return
	for placement_id in _placements:
		var record: Dictionary = _placements[placement_id]
		(record["node"] as Node3D).visible = false
		for body in record["colliders"]:
			(body as StaticBody3D).collision_layer = 0


func _dispose_for_runtime() -> void:
	if _disposed:
		return
	_disposed = true
	if is_instance_valid(_root):
		if _root.get_parent() != null:
			_root.get_parent().remove_child(_root)
		_root.free()


func _placement_visible_for_test(placement_id: String) -> bool:
	if _disposed or not _placements.has(placement_id):
		return false
	return (_placements[placement_id]["node"] as Node3D).visible


func _placement_collision_layer_for_test(placement_id: String) -> int:
	if _disposed or not _placements.has(placement_id):
		return -1
	var colliders: Array = _placements[placement_id]["colliders"]
	return 0 if colliders.is_empty() else (colliders[0] as StaticBody3D).collision_layer


func _visible_placement_ids_for_runtime(ordered_ids: Array) -> Array:
	var visible: Array = []
	if _disposed:
		return visible
	for placement_id in ordered_ids:
		if typeof(placement_id) == TYPE_STRING and _placements.has(placement_id) and (
			_placements[placement_id]["node"] as Node3D
		).visible:
			visible.append(placement_id)
	return visible


static func _make_read_only(value: Variant) -> void:
	if typeof(value) == TYPE_DICTIONARY:
		for key in value.keys():
			_make_read_only(value[key])
		value.make_read_only()
	elif typeof(value) == TYPE_ARRAY:
		for child in value:
			_make_read_only(child)
		value.make_read_only()
