@tool
extends RefCounted

const GaussianResourceScript = preload("res://addons/gdgs/runtime/resources/gaussian_resource.gd")

const STRUCT_SIZE := 60
const SH_FLOAT_COUNT := 48

static func create_canonical(count: int) -> Dictionary:
	var positions := PackedVector3Array()
	positions.resize(count)

	var scales_linear := PackedVector3Array()
	scales_linear.resize(count)

	var rotations: Array = []
	rotations.resize(count)

	var opacities := PackedFloat32Array()
	opacities.resize(count)

	var sh_coeffs := PackedFloat32Array()
	sh_coeffs.resize(count * SH_FLOAT_COUNT)

	return {
		"count": count,
		"positions": positions,
		"scales_linear": scales_linear,
		"rotations": rotations,
		"opacities": opacities,
		"sh_coeffs": sh_coeffs
	}

static func build(canonical: Dictionary) -> Dictionary:
	var count := int(canonical.get("count", 0))
	var positions: PackedVector3Array = canonical.get("positions", PackedVector3Array())
	var scales_linear: PackedVector3Array = canonical.get("scales_linear", PackedVector3Array())
	var rotations: Array = canonical.get("rotations", [])
	var opacities: PackedFloat32Array = canonical.get("opacities", PackedFloat32Array())
	var sh_coeffs: PackedFloat32Array = canonical.get("sh_coeffs", PackedFloat32Array())

	if count < 0:
		return _error(ERR_INVALID_DATA, "Canonical gaussian count is invalid")
	if positions.size() != count or scales_linear.size() != count or rotations.size() != count or opacities.size() != count:
		return _error(ERR_INVALID_DATA, "Canonical gaussian arrays are inconsistent")
	if sh_coeffs.size() != count * SH_FLOAT_COUNT:
		return _error(ERR_INVALID_DATA, "Canonical SH coefficient buffer has an unexpected size")

	var center := Vector3.ZERO
	if count > 0:
		for i in count:
			center += positions[i]
		center /= float(count)

	var points := PackedFloat32Array()
	points.resize(count * STRUCT_SIZE)

	var xyz := PackedVector3Array()
	xyz.resize(count)

	var aabb_min_v := Vector3(INF, INF, INF)
	var aabb_max_v := Vector3(-INF, -INF, -INF)

	for i in count:
		var pos: Vector3 = positions[i] - center
		var scale_linear: Vector3 = scales_linear[i]
		var rotation_value = rotations[i]
		var rotation := Quaternion(0.0, 0.0, 0.0, 1.0)
		if rotation_value is Quaternion:
			rotation = rotation_value.normalized()

		scale_linear = Vector3(
			maxf(scale_linear.x, 1e-6),
			maxf(scale_linear.y, 1e-6),
			maxf(scale_linear.z, 1e-6)
		)

		xyz[i] = pos
		aabb_min_v = aabb_min_v.min(pos)
		aabb_max_v = aabb_max_v.max(pos)

		var base := i * STRUCT_SIZE
		points[base + 0] = pos.x
		points[base + 1] = pos.y
		points[base + 2] = pos.z
		points[base + 3] = 0.0

		var scale_mat := Basis.from_scale(scale_linear)
		var rot_mat := Basis(rotation).transposed()
		var cov_3d := (scale_mat * rot_mat).transposed() * (scale_mat * rot_mat)

		points[base + 4] = cov_3d.x[0]
		points[base + 5] = cov_3d.y[0]
		points[base + 6] = cov_3d.z[0]
		points[base + 7] = cov_3d.y[1]
		points[base + 8] = cov_3d.z[1]
		points[base + 9] = cov_3d.z[2]

		points[base + 10] = clampf(opacities[i], 0.0, 1.0)
		points[base + 11] = 0.0

		var sh_offset := i * SH_FLOAT_COUNT
		for j in SH_FLOAT_COUNT:
			points[base + 12 + j] = sh_coeffs[sh_offset + j]

	var resource = GaussianResourceScript.new()
	resource.point_count = count
	resource.point_data_float = points
	resource.point_data_byte = points.to_byte_array()
	resource.xyz = xyz
	resource.aabb = AABB(aabb_min_v, aabb_max_v - aabb_min_v) if count > 0 else AABB()

	return {
		"ok": true,
		"resource": resource
	}

static func _error(code: Error, message: String) -> Dictionary:
	return {
		"ok": false,
		"error": code,
		"message": message
	}
