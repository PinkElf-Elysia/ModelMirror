class_name MatrixOasisActionTerminalGrid
extends Node3D

const MAX_TERMINALS := 64
const COLUMN_COUNT := 8
const COLUMN_SPACING := 1.7
const ROW_SPACING := 2.25
const GRID_ORIGIN_Z := -2.4
const TERMINAL_SCENE := preload("res://playable/action_terminal_3d.tscn")

var _terminals: Array[MatrixOasisActionTerminal3D] = []


func rebuild(actions: Array) -> bool:
	if actions.size() > MAX_TERMINALS:
		return false
	var candidates: Array[MatrixOasisActionTerminal3D] = []
	for index in actions.size():
		if typeof(actions[index]) != TYPE_DICTIONARY:
			_free_candidates(candidates)
			return false
		var terminal := TERMINAL_SCENE.instantiate() as MatrixOasisActionTerminal3D
		if terminal == null or not terminal.configure(actions[index], index):
			if terminal != null:
				terminal.free()
			_free_candidates(candidates)
			return false
		terminal.position = terminal_position(index, actions.size())
		candidates.append(terminal)
	clear_terminals()
	for terminal in candidates:
		add_child(terminal)
		_terminals.append(terminal)
	return true


func clear_terminals() -> void:
	for terminal in _terminals:
		if is_instance_valid(terminal):
			remove_child(terminal)
			terminal.free()
	_terminals.clear()


func _free_candidates(candidates: Array[MatrixOasisActionTerminal3D]) -> void:
	for terminal in candidates:
		if is_instance_valid(terminal):
			terminal.free()


func get_terminal_count() -> int:
	return _terminals.size()


func get_terminal(index: int) -> MatrixOasisActionTerminal3D:
	return _terminals[index] if index >= 0 and index < _terminals.size() else null


static func terminal_position(index: int, total: int) -> Vector3:
	if index < 0 or total < 1 or index >= total or total > MAX_TERMINALS:
		return Vector3.ZERO
	var row := index / COLUMN_COUNT
	var column := index % COLUMN_COUNT
	var first_index := row * COLUMN_COUNT
	var row_count := mini(COLUMN_COUNT, total - first_index)
	var centered_column := float(column) - float(row_count - 1) * 0.5
	return Vector3(centered_column * COLUMN_SPACING, 0.85, GRID_ORIGIN_Z - float(row) * ROW_SPACING)
