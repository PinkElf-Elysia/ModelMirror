@tool
extends RefCounted

const BinaryPlyReader = preload("res://addons/gdgs/importers/parsers/binary_ply_reader.gd")
const GaussianResourceBuilder = preload("res://addons/gdgs/importers/builders/gaussian_resource_builder.gd")

const SH_COEFF_COUNT := 48

static func decode(path: String) -> Dictionary:
	var ply := BinaryPlyReader.read(path, true)
	if not ply.get("ok", false):
		return ply

	var vertex := BinaryPlyReader.get_element(ply, "vertex")
	if vertex.is_empty():
		return _error(ERR_INVALID_DATA, "PLY file does not contain a vertex element")

	var property_map: Dictionary = vertex.get("property_map", {})
	var required := [
		"x", "y", "z",
		"f_dc_0", "f_dc_1", "f_dc_2",
		"opacity",
		"scale_0", "scale_1",
		"rot_0", "rot_1", "rot_2", "rot_3"
	]
	for name in required:
		if not property_map.has(name):
			return _error(ERR_INVALID_DATA, "PLY file is missing required property '%s'" % name)

	var count := int(vertex.get("count", 0))
	var stride := int(vertex.get("stride", 0))
	var data: PackedByteArray = vertex.get("data", PackedByteArray())

	var canonical := GaussianResourceBuilder.create_canonical(count)
	var positions: PackedVector3Array = canonical["positions"]
	var scales_linear: PackedVector3Array = canonical["scales_linear"]
	var rotations: Array = canonical["rotations"]
	var opacities: PackedFloat32Array = canonical["opacities"]
	var sh_coeffs: PackedFloat32Array = canonical["sh_coeffs"]

	for i in count:
		var base := i * stride

		positions[i] = Vector3(
			float(_read_property(data, base, property_map, "x", 0.0)),
			float(_read_property(data, base, property_map, "y", 0.0)),
			float(_read_property(data, base, property_map, "z", 0.0))
		)

		var scale_2 := float(_read_property(data, base, property_map, "scale_2", log(1e-6)))
		scales_linear[i] = Vector3(
			exp(float(_read_property(data, base, property_map, "scale_0", 0.0))),
			exp(float(_read_property(data, base, property_map, "scale_1", 0.0))),
			exp(scale_2)
		)

		rotations[i] = Quaternion(
			float(_read_property(data, base, property_map, "rot_1", 0.0)),
			float(_read_property(data, base, property_map, "rot_2", 0.0)),
			float(_read_property(data, base, property_map, "rot_3", 0.0)),
			float(_read_property(data, base, property_map, "rot_0", 1.0))
		).normalized()

		opacities[i] = _sigmoid(float(_read_property(data, base, property_map, "opacity", 0.0)))

		var sh_offset := i * SH_COEFF_COUNT
		sh_coeffs[sh_offset + 0] = float(_read_property(data, base, property_map, "f_dc_0", 0.0))
		sh_coeffs[sh_offset + 1] = float(_read_property(data, base, property_map, "f_dc_1", 0.0))
		sh_coeffs[sh_offset + 2] = float(_read_property(data, base, property_map, "f_dc_2", 0.0))

		for coeff_idx in range(15):
			var coeff_offset := sh_offset + 3 + coeff_idx * 3
			sh_coeffs[coeff_offset + 0] = float(_read_property(data, base, property_map, "f_rest_%d" % coeff_idx, 0.0))
			sh_coeffs[coeff_offset + 1] = float(_read_property(data, base, property_map, "f_rest_%d" % (coeff_idx + 15), 0.0))
			sh_coeffs[coeff_offset + 2] = float(_read_property(data, base, property_map, "f_rest_%d" % (coeff_idx + 30), 0.0))

	return {
		"ok": true,
		"canonical": canonical
	}

static func _read_property(data: PackedByteArray, base: int, property_map: Dictionary, property_name: String, default_value: Variant) -> Variant:
	var prop: Dictionary = property_map.get(property_name, {})
	if prop.is_empty():
		return default_value
	return BinaryPlyReader.decode_scalar(data, base + int(prop["offset"]), String(prop["type"]))

static func _sigmoid(value: float) -> float:
	return 1.0 / (1.0 + exp(-value))

static func _error(code: Error, message: String) -> Dictionary:
	return {
		"ok": false,
		"error": code,
		"message": message
	}
