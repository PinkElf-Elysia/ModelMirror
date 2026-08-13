@tool
extends RefCounted

const GaussianResourceBuilder = preload("res://addons/gdgs/importers/builders/gaussian_resource_builder.gd")

const SH_COEFF_COUNT := 48
const SH_COEFFS_PER_BAND := {
	1: 3,
	2: 8,
	3: 15
}
const SQRT2 := 1.4142135623730951

static func decode(path: String) -> Dictionary:
	var zip_reader := ZIPReader.new()
	var open_error := zip_reader.open(path)
	if open_error != OK:
		return _error(open_error, "Unable to open SOG archive: %s" % path)

	var meta_bytes := zip_reader.read_file("meta.json")
	if meta_bytes.is_empty():
		zip_reader.close()
		return _error(ERR_FILE_NOT_FOUND, "Bundled SOG archive is missing meta.json")

	var meta = JSON.parse_string(meta_bytes.get_string_from_utf8())
	if typeof(meta) != TYPE_DICTIONARY:
		zip_reader.close()
		return _error(ERR_INVALID_DATA, "SOG metadata is not valid JSON")

	if int(meta.get("version", -1)) != 2:
		zip_reader.close()
		return _error(ERR_UNAVAILABLE, "Only SOG version 2 is supported")

	var count := int(meta.get("count", 0))
	if count <= 0:
		zip_reader.close()
		return _error(ERR_INVALID_DATA, "SOG metadata does not contain a valid gaussian count")

	var means_meta: Dictionary = meta.get("means", {})
	var means_files: Array = means_meta.get("files", [])
	if means_files.size() < 2:
		zip_reader.close()
		return _error(ERR_INVALID_DATA, "SOG means metadata is incomplete")

	var means_l := _load_image(zip_reader, String(means_files[0]), Image.FORMAT_RGB8)
	if not means_l.get("ok", false):
		zip_reader.close()
		return means_l
	var means_u := _load_image(zip_reader, String(means_files[1]), Image.FORMAT_RGB8)
	if not means_u.get("ok", false):
		zip_reader.close()
		return means_u

	var scales_meta: Dictionary = meta.get("scales", {})
	var scales_files: Array = scales_meta.get("files", [])
	if scales_files.is_empty():
		zip_reader.close()
		return _error(ERR_INVALID_DATA, "SOG scales metadata is incomplete")
	var scales_image := _load_image(zip_reader, String(scales_files[0]), Image.FORMAT_RGB8)
	if not scales_image.get("ok", false):
		zip_reader.close()
		return scales_image

	var quats_meta: Dictionary = meta.get("quats", {})
	var quats_files: Array = quats_meta.get("files", [])
	if quats_files.is_empty():
		zip_reader.close()
		return _error(ERR_INVALID_DATA, "SOG quaternion metadata is incomplete")
	var quats_image := _load_image(zip_reader, String(quats_files[0]), Image.FORMAT_RGBA8)
	if not quats_image.get("ok", false):
		zip_reader.close()
		return quats_image

	var sh0_meta: Dictionary = meta.get("sh0", {})
	var sh0_files: Array = sh0_meta.get("files", [])
	if sh0_files.is_empty():
		zip_reader.close()
		return _error(ERR_INVALID_DATA, "SOG sh0 metadata is incomplete")
	var sh0_image := _load_image(zip_reader, String(sh0_files[0]), Image.FORMAT_RGBA8)
	if not sh0_image.get("ok", false):
		zip_reader.close()
		return sh0_image

	var dims := Vector2i(int(means_l["width"]), int(means_l["height"]))
	if not _image_matches(means_u, dims) or not _image_matches(scales_image, dims) or not _image_matches(quats_image, dims) or not _image_matches(sh0_image, dims):
		zip_reader.close()
		return _error(ERR_INVALID_DATA, "SOG textures must share the same dimensions")
	if count > dims.x * dims.y:
		zip_reader.close()
		return _error(ERR_INVALID_DATA, "SOG texture atlas is too small for the gaussian count")

	var means_mins := _array_to_vector3(means_meta.get("mins", []))
	var means_maxs := _array_to_vector3(means_meta.get("maxs", []))
	var scales_codebook := _array_to_float_array(scales_meta.get("codebook", []))
	var sh0_codebook := _array_to_float_array(sh0_meta.get("codebook", []))

	if scales_codebook.size() < 256 or sh0_codebook.size() < 256:
		zip_reader.close()
		return _error(ERR_INVALID_DATA, "SOG codebooks must contain 256 entries")

	var shn_centroids := {}
	var shn_labels := {}
	var shn_bands := 0
	var shn_coeffs_per_channel := 0
	var shn_palette_count := 0
	var shn_codebook := PackedFloat32Array()

	if meta.has("shN"):
		var shn_meta: Dictionary = meta["shN"]
		shn_bands = int(shn_meta.get("bands", 0))
		if not SH_COEFFS_PER_BAND.has(shn_bands):
			zip_reader.close()
			return _error(ERR_INVALID_DATA, "SOG shN metadata contains an unsupported band count")
		var shn_files: Array = shn_meta.get("files", [])
		if shn_files.size() < 2:
			zip_reader.close()
			return _error(ERR_INVALID_DATA, "SOG shN metadata is incomplete")
		shn_palette_count = int(shn_meta.get("count", 0))
		shn_codebook = _array_to_float_array(shn_meta.get("codebook", []))
		if shn_palette_count <= 0 or shn_codebook.size() < 256:
			zip_reader.close()
			return _error(ERR_INVALID_DATA, "SOG shN metadata is invalid")

		shn_centroids = _load_image(zip_reader, String(shn_files[0]), Image.FORMAT_RGB8)
		if not shn_centroids.get("ok", false):
			zip_reader.close()
			return shn_centroids
		shn_labels = _load_image(zip_reader, String(shn_files[1]), Image.FORMAT_RGBA8)
		if not shn_labels.get("ok", false):
			zip_reader.close()
			return shn_labels
		if not _image_matches(shn_labels, dims):
			zip_reader.close()
			return _error(ERR_INVALID_DATA, "SOG shN label texture dimensions do not match the main atlas")

		shn_coeffs_per_channel = SH_COEFFS_PER_BAND[shn_bands]

	var canonical := GaussianResourceBuilder.create_canonical(count)
	var positions: PackedVector3Array = canonical["positions"]
	var scales_linear: PackedVector3Array = canonical["scales_linear"]
	var rotations: Array = canonical["rotations"]
	var opacities: PackedFloat32Array = canonical["opacities"]
	var sh_coeffs: PackedFloat32Array = canonical["sh_coeffs"]

	var means_l_data: PackedByteArray = means_l["data"]
	var means_u_data: PackedByteArray = means_u["data"]
	var scales_data: PackedByteArray = scales_image["data"]
	var quats_data: PackedByteArray = quats_image["data"]
	var sh0_data: PackedByteArray = sh0_image["data"]
	var shn_centroids_data := PackedByteArray()
	var shn_labels_data := PackedByteArray()
	if meta.has("shN"):
		shn_centroids_data = shn_centroids["data"]
		shn_labels_data = shn_labels["data"]

	for i in count:
		var means_rgb_offset := i * 3
		var quats_rgba_offset := i * 4
		var sh0_rgba_offset := i * 4

		var qx := (int(means_u_data[means_rgb_offset + 0]) << 8) | int(means_l_data[means_rgb_offset + 0])
		var qy := (int(means_u_data[means_rgb_offset + 1]) << 8) | int(means_l_data[means_rgb_offset + 1])
		var qz := (int(means_u_data[means_rgb_offset + 2]) << 8) | int(means_l_data[means_rgb_offset + 2])
		positions[i] = Vector3(
			_unlog(_lerp_range(means_mins.x, means_maxs.x, float(qx) / 65535.0)),
			_unlog(_lerp_range(means_mins.y, means_maxs.y, float(qy) / 65535.0)),
			_unlog(_lerp_range(means_mins.z, means_maxs.z, float(qz) / 65535.0))
		)

		scales_linear[i] = Vector3(
			maxf(exp(scales_codebook[int(scales_data[means_rgb_offset + 0])]), 1e-6),
			maxf(exp(scales_codebook[int(scales_data[means_rgb_offset + 1])]), 1e-6),
			maxf(exp(scales_codebook[int(scales_data[means_rgb_offset + 2])]), 1e-6)
		)

		var mode := int(quats_data[quats_rgba_offset + 3]) - 252
		if mode < 0 or mode > 3:
			zip_reader.close()
			return _error(ERR_INVALID_DATA, "SOG quaternion packing mode is invalid")
		rotations[i] = _decode_sog_quaternion(
			int(quats_data[quats_rgba_offset + 0]),
			int(quats_data[quats_rgba_offset + 1]),
			int(quats_data[quats_rgba_offset + 2]),
			mode
		)

		var sh_offset := i * SH_COEFF_COUNT
		sh_coeffs[sh_offset + 0] = sh0_codebook[int(sh0_data[sh0_rgba_offset + 0])]
		sh_coeffs[sh_offset + 1] = sh0_codebook[int(sh0_data[sh0_rgba_offset + 1])]
		sh_coeffs[sh_offset + 2] = sh0_codebook[int(sh0_data[sh0_rgba_offset + 2])]
		opacities[i] = float(sh0_data[sh0_rgba_offset + 3]) / 255.0

		if meta.has("shN"):
			var labels_offset := i * 4
			var label := int(shn_labels_data[labels_offset + 0]) | (int(shn_labels_data[labels_offset + 1]) << 8)
			if label >= shn_palette_count:
				zip_reader.close()
				return _error(ERR_INVALID_DATA, "SOG shN label index is out of range")

			for coeff_idx in range(shn_coeffs_per_channel):
				var centroid_x := (label % 64) * shn_coeffs_per_channel + coeff_idx
				var centroid_y := int(label / 64)
				var centroid_offset := (centroid_y * int(shn_centroids["width"]) + centroid_x) * 3
				if centroid_offset + 2 >= shn_centroids_data.size():
					zip_reader.close()
					return _error(ERR_INVALID_DATA, "SOG shN centroid texture layout is invalid")

				var dst := sh_offset + 3 + coeff_idx * 3
				sh_coeffs[dst + 0] = shn_codebook[int(shn_centroids_data[centroid_offset + 0])]
				sh_coeffs[dst + 1] = shn_codebook[int(shn_centroids_data[centroid_offset + 1])]
				sh_coeffs[dst + 2] = shn_codebook[int(shn_centroids_data[centroid_offset + 2])]

	zip_reader.close()
	return {
		"ok": true,
		"canonical": canonical
	}

static func _load_image(zip_reader: ZIPReader, filename: String, target_format: int) -> Dictionary:
	var bytes := zip_reader.read_file(filename)
	if bytes.is_empty():
		return _error(ERR_FILE_NOT_FOUND, "SOG archive entry '%s' is missing" % filename)

	var image := Image.new()
	var load_error := image.load_webp_from_buffer(bytes)
	if load_error != OK:
		return _error(load_error, "Unable to decode WebP image '%s'" % filename)

	if image.get_format() != target_format:
		image.convert(target_format)

	return {
		"ok": true,
		"width": image.get_width(),
		"height": image.get_height(),
		"data": image.get_data()
	}

static func _image_matches(image_info: Dictionary, dims: Vector2i) -> bool:
	return int(image_info.get("width", -1)) == dims.x and int(image_info.get("height", -1)) == dims.y

static func _array_to_vector3(values: Array) -> Vector3:
	return Vector3(
		float(values[0]) if values.size() > 0 else 0.0,
		float(values[1]) if values.size() > 1 else 0.0,
		float(values[2]) if values.size() > 2 else 0.0
	)

static func _array_to_float_array(values: Array) -> PackedFloat32Array:
	var result := PackedFloat32Array()
	result.resize(values.size())
	for i in values.size():
		result[i] = float(values[i])
	return result

static func _decode_sog_quaternion(a: int, b: int, c: int, mode: int) -> Quaternion:
	var comps := [0.0, 0.0, 0.0, 0.0]
	var decoded := [
		(float(a) / 255.0 - 0.5) * SQRT2,
		(float(b) / 255.0 - 0.5) * SQRT2,
		(float(c) / 255.0 - 0.5) * SQRT2
	]
	var decoded_idx := 0
	var sum_sq := 0.0

	for component_idx in 4:
		if component_idx == mode:
			continue
		comps[component_idx] = decoded[decoded_idx]
		sum_sq += decoded[decoded_idx] * decoded[decoded_idx]
		decoded_idx += 1

	comps[mode] = sqrt(maxf(0.0, 1.0 - sum_sq))
	return Quaternion(comps[1], comps[2], comps[3], comps[0]).normalized()

static func _unlog(value: float) -> float:
	var sign_value := -1.0 if value < 0.0 else 1.0
	return sign_value * (exp(abs(value)) - 1.0)

static func _lerp_range(min_value: float, max_value: float, normalized: float) -> float:
	return min_value + (max_value - min_value) * normalized

static func _error(code: Error, message: String) -> Dictionary:
	return {
		"ok": false,
		"error": code,
		"message": message
	}
