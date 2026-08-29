class_name MatrixOasisNpcActorController
extends CharacterBody3D

signal movement_arrived(evidence: Dictionary)
signal movement_failed(code: String)

const PHYSICS_TICKS_PER_SECOND := 60
const BODY_RADIUS := 0.35
const BODY_HEIGHT := 1.8
const FLOOR_SNAP := 0.2
const SPEED_PER_TICK := 0.05
const TURN_RADIANS_PER_TICK := deg_to_rad(3.0)
const ARRIVAL_TOLERANCE := 0.1
const MOVEMENT_TICK_LIMIT := 1800
const MAXIMUM_PATH_LENGTH := 100.0

var actor_entity_id := ""
var home_transform := Transform3D.IDENTITY
var _agent: NavigationAgent3D
var _collision: CollisionShape3D
var _moving := false
var _returning := false
var _movement_ticks := 0
var _path_length := 0.0
var _last_position := Vector3.ZERO
var _gravity := 9.8


func configure(actor_id: String, placement: Node3D) -> bool:
	if is_node_ready() or actor_id.is_empty() or placement == null or placement.get_parent() == null:
		return false
	actor_entity_id = actor_id
	var parent := placement.get_parent()
	var original_name := placement.name
	var initially_visible := placement.visible
	home_transform = placement.global_transform
	placement.name = StringName(str(original_name) + "Visual")
	name = original_name
	parent.add_child(self)
	global_transform = home_transform
	placement.reparent(self, true)
	placement.visible = true
	visible = initially_visible
	for child: Node in placement.find_children("*", "CollisionObject3D", true, false):
		(child as CollisionObject3D).collision_layer = 0
		(child as CollisionObject3D).collision_mask = 0
	_collision = CollisionShape3D.new()
	_collision.name = "NpcCapsule"
	var capsule := CapsuleShape3D.new()
	capsule.radius = BODY_RADIUS
	capsule.height = BODY_HEIGHT
	_collision.shape = capsule
	_collision.position.y = BODY_HEIGHT * 0.5
	add_child(_collision)
	_agent = NavigationAgent3D.new()
	_agent.name = "NpcNavigationAgent"
	_agent.path_desired_distance = ARRIVAL_TOLERANCE
	_agent.target_desired_distance = ARRIVAL_TOLERANCE
	_agent.avoidance_enabled = false
	add_child(_agent)
	collision_layer = 2 if visible else 0
	collision_mask = 1
	floor_snap_length = FLOOR_SNAP
	up_direction = Vector3.UP
	_gravity = float(ProjectSettings.get_setting("physics/3d/default_gravity", 9.8))
	set_physics_process(false)
	return true


func begin_move(target_floor_position: Vector3, returning := false) -> bool:
	if _agent == null or _moving or not target_floor_position.is_finite():
		return false
	visible = true
	collision_layer = 2
	_returning = returning
	_movement_ticks = 0
	_path_length = 0.0
	_last_position = global_position
	_agent.target_position = target_floor_position
	_moving = true
	set_physics_process(true)
	return true


func hide_at_home() -> void:
	_moving = false
	set_physics_process(false)
	global_transform = home_transform
	velocity = Vector3.ZERO
	visible = false
	collision_layer = 0
	reset_physics_interpolation()


func refresh_visibility_collision() -> void:
	for child: Node in find_children("*", "StaticBody3D", true, false):
		(child as StaticBody3D).collision_layer = 0
		(child as StaticBody3D).collision_mask = 0
	collision_layer = 2 if visible else 0


func restore_home() -> void:
	global_transform = home_transform
	velocity = Vector3.ZERO
	reset_physics_interpolation()


func is_returning() -> bool:
	return _returning


func _physics_process(_delta: float) -> void:
	if not _moving or _agent == null:
		return
	_movement_ticks += 1
	if _movement_ticks > MOVEMENT_TICK_LIMIT:
		_stop_failed("R20_NPC_MOVEMENT_TICK_LIMIT")
		return
	var target := _agent.target_position
	var flat_distance := Vector2(global_position.x, global_position.z).distance_to(Vector2(target.x, target.z))
	if _agent.is_navigation_finished() and flat_distance <= ARRIVAL_TOLERANCE:
		_stop_arrived()
		return
	var next := _agent.get_next_path_position()
	var direction := next - global_position
	direction.y = 0.0
	if direction.length_squared() <= 0.000001:
		_stop_failed("R20_NPC_NAVIGATION_STALLED")
		return
	direction = direction.normalized()
	var desired_yaw := atan2(-direction.x, -direction.z)
	rotation.y = rotate_toward(rotation.y, desired_yaw, TURN_RADIANS_PER_TICK)
	velocity.x = direction.x * SPEED_PER_TICK * PHYSICS_TICKS_PER_SECOND
	velocity.z = direction.z * SPEED_PER_TICK * PHYSICS_TICKS_PER_SECOND
	velocity.y = minf(velocity.y, 0.0) if is_on_floor() else velocity.y - _gravity / float(PHYSICS_TICKS_PER_SECOND)
	move_and_slide()
	var moved := Vector2(global_position.x - _last_position.x, global_position.z - _last_position.z).length()
	_path_length += moved
	_last_position = global_position
	if _path_length > MAXIMUM_PATH_LENGTH:
		_stop_failed("R20_NPC_PATH_LENGTH_LIMIT")


func _stop_arrived() -> void:
	_moving = false
	set_physics_process(false)
	velocity = Vector3.ZERO
	var evidence := _physics_evidence()
	evidence["movementTicks"] = _movement_ticks
	evidence["pathLengthMm"] = roundi(_path_length * 1000.0)
	movement_arrived.emit(evidence)


func _stop_failed(code: String) -> void:
	_moving = false
	set_physics_process(false)
	velocity = Vector3.ZERO
	movement_failed.emit(code)


func _physics_evidence() -> Dictionary:
	var space := get_world_3d().direct_space_state
	var floor_query := PhysicsRayQueryParameters3D.create(
		global_position + Vector3.UP * FLOOR_SNAP,
		global_position + Vector3.DOWN * FLOOR_SNAP,
		1,
	)
	floor_query.exclude = [get_rid()]
	var floor_hit := space.intersect_ray(floor_query)
	var shape_query := PhysicsShapeQueryParameters3D.new()
	shape_query.shape = _collision.shape
	shape_query.transform = Transform3D(global_basis, global_position + _collision.position + Vector3.UP * 0.025)
	shape_query.collision_mask = 1
	shape_query.exclude = [get_rid()]
	var closest := NavigationServer3D.map_get_closest_point(_agent.get_navigation_map(), global_position)
	return {
		"pathComplete": _agent.is_navigation_finished(),
		"floorVerified": not floor_hit.is_empty(),
		"capsuleVerified": space.intersect_shape(shape_query, 1).is_empty(),
		"domainVerified": Vector2(closest.x, closest.z).distance_to(Vector2(global_position.x, global_position.z)) <= ARRIVAL_TOLERANCE,
	}
