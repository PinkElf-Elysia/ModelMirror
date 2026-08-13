@tool
extends RefCounted

const TYPE_SIZES := {
	"char": 1,
	"uchar": 1,
	"short": 2,
	"ushort": 2,
	"int": 4,
	"uint": 4,
	"float": 4,
	"double": 8
}

const TYPE_ALIASES := {
	"int8": "char",
	"uint8": "uchar",
	"int16": "short",
	"uint16": "ushort",
	"int32": "int",
	"uint32": "uint",
	"float32": "float",
	"float64": "double"
}

static func read(path: String, include_data := true) -> Dictionary:
	var file := FileAccess.open(path, FileAccess.READ)
	if file == null:
		return _error(FileAccess.get_open_error(), "Unable to open PLY file: %s" % path)

	file.big_endian = false

	var magic := file.get_line().strip_edges()
	if magic != "ply":
		return _error(ERR_FILE_UNRECOGNIZED, "Missing PLY magic header")

	var format := ""
	var elements: Array = []
	var current_element: Dictionary = {}

	while not file.eof_reached():
		var raw_line := file.get_line()
		var line := raw_line.strip_edges()
		if line.is_empty():
			continue
		if line == "end_header":
			break

		var parts := line.split(" ", false)
		if parts.is_empty():
			continue

		match parts[0]:
			"comment", "obj_info":
				continue
			"format":
				if parts.size() < 3:
					return _error(ERR_INVALID_DATA, "Malformed PLY format declaration")
				format = parts[1]
			"element":
				if parts.size() < 3:
					return _error(ERR_INVALID_DATA, "Malformed PLY element declaration")
				current_element = {
					"name": parts[1],
					"count": int(parts[2]),
					"stride": 0,
					"properties": [],
					"property_map": {}
				}
				elements.push_back(current_element)
			"property":
				if current_element.is_empty():
					return _error(ERR_INVALID_DATA, "PLY property declared before any element")
				if parts.size() < 3:
					return _error(ERR_INVALID_DATA, "Malformed PLY property declaration")
				if parts[1] == "list":
					return _error(ERR_UNAVAILABLE, "PLY list properties are not supported")

				var raw_type: String = parts[1]
				var prop_type: String = TYPE_ALIASES.get(raw_type, raw_type)
				if not TYPE_SIZES.has(prop_type):
					return _error(ERR_UNAVAILABLE, "Unsupported PLY property type: %s" % raw_type)

				var prop := {
					"name": parts[2],
					"type": prop_type,
					"offset": current_element["stride"],
					"size": TYPE_SIZES[prop_type]
				}
				current_element["properties"].push_back(prop)
				current_element["property_map"][prop["name"]] = prop
				current_element["stride"] += prop["size"]
			_:
				return _error(ERR_INVALID_DATA, "Unsupported PLY header token: %s" % parts[0])

	if format != "binary_little_endian":
		return _error(ERR_UNAVAILABLE, "Only binary_little_endian PLY is supported")

	if elements.is_empty():
		return _error(ERR_INVALID_DATA, "PLY file does not contain any elements")

	if include_data:
		for element in elements:
			var byte_size := int(element["count"]) * int(element["stride"])
			element["data"] = file.get_buffer(byte_size)
			if element["data"].size() != byte_size:
				return _error(ERR_FILE_CORRUPT, "Unexpected end of PLY data while reading '%s'" % element["name"])

	return {
		"ok": true,
		"format": format,
		"elements": elements
	}

static func get_element(ply: Dictionary, name: String) -> Dictionary:
	var elements: Array = ply.get("elements", [])
	for element in elements:
		if element.get("name", "") == name:
			return element
	return {}

static func decode_scalar(data: PackedByteArray, byte_offset: int, data_type: String) -> Variant:
	match data_type:
		"char":
			var value := int(data[byte_offset])
			return value if value < 128 else value - 256
		"uchar":
			return int(data[byte_offset])
		"short":
			return data.decode_s16(byte_offset)
		"ushort":
			return data.decode_u16(byte_offset)
		"int":
			return data.decode_s32(byte_offset)
		"uint":
			return data.decode_u32(byte_offset)
		"float":
			return data.decode_float(byte_offset)
		"double":
			return data.decode_double(byte_offset)
		_:
			return null

static func _error(code: Error, message: String) -> Dictionary:
	return {
		"ok": false,
		"error": code,
		"message": message
	}
