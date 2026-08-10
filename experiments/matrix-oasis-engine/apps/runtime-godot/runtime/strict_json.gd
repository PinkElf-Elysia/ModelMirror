class_name MatrixOasisStrictJson
extends RefCounted

const MAX_JSON_DEPTH := 256
const MAX_SAFE_INTEGER_TEXT := "9007199254740991"


static func parse_bytes(bytes: PackedByteArray, document_path: String) -> Dictionary:
	if bytes.is_empty():
		return _failure("GODOT_RUNTIME_JSON_SYNTAX", document_path)
	if bytes.size() >= 3 and bytes[0] == 0xef and bytes[1] == 0xbb and bytes[2] == 0xbf:
		return _failure("GODOT_RUNTIME_JSON_NON_CANONICAL", document_path)
	if not _is_valid_utf8(bytes):
		return _failure("GODOT_RUNTIME_JSON_UTF8_INVALID", document_path)
	var text := bytes.get_string_from_utf8()
	if text.is_empty() or text.to_utf8_buffer() != bytes:
		return _failure("GODOT_RUNTIME_JSON_UTF8_INVALID", document_path)
	var parser := Parser.new(text, document_path)
	return parser.parse()


static func _is_valid_utf8(bytes: PackedByteArray) -> bool:
	var index := 0
	while index < bytes.size():
		var first := bytes[index]
		if first <= 0x7f:
			index += 1
			continue
		if first >= 0xc2 and first <= 0xdf:
			if index + 1 >= bytes.size() or not _is_continuation(bytes[index + 1]):
				return false
			index += 2
			continue
		if first >= 0xe0 and first <= 0xef:
			if index + 2 >= bytes.size():
				return false
			var second := bytes[index + 1]
			if not _is_continuation(bytes[index + 2]):
				return false
			if first == 0xe0 and (second < 0xa0 or second > 0xbf):
				return false
			if first == 0xed and (second < 0x80 or second > 0x9f):
				return false
			if first not in [0xe0, 0xed] and not _is_continuation(second):
				return false
			index += 3
			continue
		if first >= 0xf0 and first <= 0xf4:
			if index + 3 >= bytes.size():
				return false
			var second := bytes[index + 1]
			if not _is_continuation(bytes[index + 2]) or not _is_continuation(bytes[index + 3]):
				return false
			if first == 0xf0 and (second < 0x90 or second > 0xbf):
				return false
			if first == 0xf4 and (second < 0x80 or second > 0x8f):
				return false
			if first not in [0xf0, 0xf4] and not _is_continuation(second):
				return false
			index += 4
			continue
		return false
	return true


static func _is_continuation(value: int) -> bool:
	return value >= 0x80 and value <= 0xbf


static func _failure(code: String, path: String) -> Dictionary:
	return {
		"ok": false,
		"diagnostics": [{
			"phase": "parse",
			"severity": "error",
			"code": code,
			"path": path,
			"message": code,
		}],
	}


static func _utf16_units(value: String) -> Array[int]:
	var units: Array[int] = []
	for index in range(value.length()):
		var scalar := value.unicode_at(index)
		if scalar <= 0xffff:
			units.append(scalar)
		else:
			var adjusted := scalar - 0x10000
			units.append(0xd800 + (adjusted >> 10))
			units.append(0xdc00 + (adjusted & 0x3ff))
	return units


static func compare_utf16(left: String, right: String) -> int:
	var left_units := _utf16_units(left)
	var right_units := _utf16_units(right)
	var count := mini(left_units.size(), right_units.size())
	for index in range(count):
		if left_units[index] < right_units[index]:
			return -1
		if left_units[index] > right_units[index]:
			return 1
	if left_units.size() < right_units.size():
		return -1
	if left_units.size() > right_units.size():
		return 1
	return 0


class Parser extends RefCounted:
	var _text: String
	var _document_path: String
	var _index := 0


	func _init(text: String, document_path: String) -> void:
		_text = text
		_document_path = document_path


	func parse() -> Dictionary:
		var parsed := _parse_value(1)
		if not parsed["ok"]:
			return parsed
		if _index != _text.length():
			return _error(_unexpected_code())
		if typeof(parsed["value"]) != TYPE_DICTIONARY:
			return _error("GODOT_RUNTIME_SCHEMA_INVALID", "schema")
		return {"ok": true, "value": parsed["value"]}


	func _parse_value(depth: int) -> Dictionary:
		if _index >= _text.length():
			return _error("GODOT_RUNTIME_JSON_SYNTAX")
		var code := _text.unicode_at(_index)
		match code:
			0x7b:
				if depth > MAX_JSON_DEPTH:
					return _error("GODOT_RUNTIME_JSON_DEPTH_EXCEEDED")
				return _parse_object(depth)
			0x5b:
				if depth > MAX_JSON_DEPTH:
					return _error("GODOT_RUNTIME_JSON_DEPTH_EXCEEDED")
				return _parse_array(depth)
			0x22:
				return _parse_string()
			0x74:
				return _parse_literal("true", true)
			0x66:
				return _parse_literal("false", false)
			0x6e:
				return _parse_literal("null", null)
			0x2d, 0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39:
				return _parse_integer()
			_:
				return _error(_unexpected_code())


	func _parse_object(depth: int) -> Dictionary:
		_index += 1
		var value := {}
		var previous_key := ""
		var has_previous := false
		if _consume(0x7d):
			return {"ok": true, "value": value}
		while true:
			if _index >= _text.length() or _text.unicode_at(_index) != 0x22:
				return _error(_unexpected_code())
			var key_result := _parse_string()
			if not key_result["ok"]:
				return key_result
			var key: String = key_result["value"]
			if has_previous:
				var comparison := MatrixOasisStrictJson.compare_utf16(previous_key, key)
				if comparison == 0:
					return _error("GODOT_RUNTIME_JSON_DUPLICATE_KEY")
				if comparison > 0:
					return _error("GODOT_RUNTIME_JSON_NON_CANONICAL")
			previous_key = key
			has_previous = true
			if not _consume(0x3a):
				return _error(_unexpected_code())
			var child := _parse_value(depth + 1)
			if not child["ok"]:
				return child
			value[key] = child["value"]
			if _consume(0x7d):
				return {"ok": true, "value": value}
			if not _consume(0x2c):
				return _error(_unexpected_code())
		return _error("GODOT_RUNTIME_JSON_SYNTAX")


	func _parse_array(depth: int) -> Dictionary:
		_index += 1
		var value := []
		if _consume(0x5d):
			return {"ok": true, "value": value}
		while true:
			var child := _parse_value(depth + 1)
			if not child["ok"]:
				return child
			value.append(child["value"])
			if _consume(0x5d):
				return {"ok": true, "value": value}
			if not _consume(0x2c):
				return _error(_unexpected_code())
		return _error("GODOT_RUNTIME_JSON_SYNTAX")


	func _parse_string() -> Dictionary:
		_index += 1
		var value := ""
		while _index < _text.length():
			var code := _text.unicode_at(_index)
			_index += 1
			if code == 0x22:
				return {"ok": true, "value": value}
			if code < 0x20:
				return _error("GODOT_RUNTIME_JSON_SYNTAX")
			if code >= 0xd800 and code <= 0xdfff:
				return _error("GODOT_RUNTIME_UNSUPPORTED_TEXT")
			if code != 0x5c:
				value += String.chr(code)
				continue
			if _index >= _text.length():
				return _error("GODOT_RUNTIME_JSON_SYNTAX")
			var escaped := _text.unicode_at(_index)
			_index += 1
			match escaped:
				0x22:
					value += "\""
				0x5c:
					value += "\\"
				0x62:
					value += String.chr(0x08)
				0x74:
					value += String.chr(0x09)
				0x6e:
					value += String.chr(0x0a)
				0x66:
					value += String.chr(0x0c)
				0x72:
					value += String.chr(0x0d)
				0x75:
					var unicode_escape := _parse_unicode_escape()
					if not unicode_escape["ok"]:
						return unicode_escape
					value += String.chr(unicode_escape["value"])
				_:
					return _error("GODOT_RUNTIME_JSON_NON_CANONICAL")
		return _error("GODOT_RUNTIME_JSON_SYNTAX")


	func _parse_unicode_escape() -> Dictionary:
		if _index + 4 > _text.length():
			return _error("GODOT_RUNTIME_JSON_SYNTAX")
		var value := 0
		for offset in range(4):
			var code := _text.unicode_at(_index + offset)
			var nibble := -1
			if code >= 0x30 and code <= 0x39:
				nibble = code - 0x30
			elif code >= 0x61 and code <= 0x66:
				nibble = code - 0x61 + 10
			else:
				return _error("GODOT_RUNTIME_JSON_NON_CANONICAL")
			value = (value << 4) | nibble
		_index += 4
		if value >= 0xd800 and value <= 0xdfff:
			return _error("GODOT_RUNTIME_UNSUPPORTED_TEXT")
		if value > 0x1f or value in [0x08, 0x09, 0x0a, 0x0c, 0x0d]:
			return _error("GODOT_RUNTIME_JSON_NON_CANONICAL")
		return {"ok": true, "value": value}


	func _parse_literal(literal: String, value: Variant) -> Dictionary:
		if _text.substr(_index, literal.length()) != literal:
			return _error("GODOT_RUNTIME_JSON_SYNTAX")
		_index += literal.length()
		return {"ok": true, "value": value}


	func _parse_integer() -> Dictionary:
		var start := _index
		var negative := _consume(0x2d)
		if _index >= _text.length():
			return _error("GODOT_RUNTIME_JSON_SYNTAX")
		if _text.unicode_at(_index) == 0x30:
			_index += 1
			if negative:
				return _error("GODOT_RUNTIME_JSON_NON_CANONICAL")
			if _index < _text.length() and _is_digit(_text.unicode_at(_index)):
				return _error("GODOT_RUNTIME_JSON_NON_CANONICAL")
			return {"ok": true, "value": 0}
		if not _is_nonzero_digit(_text.unicode_at(_index)):
			return _error("GODOT_RUNTIME_JSON_SYNTAX")
		var digits_start := _index
		while _index < _text.length() and _is_digit(_text.unicode_at(_index)):
			_index += 1
		var digits := _text.substr(digits_start, _index - digits_start)
		if digits.length() > MAX_SAFE_INTEGER_TEXT.length() or (
			digits.length() == MAX_SAFE_INTEGER_TEXT.length() and digits > MAX_SAFE_INTEGER_TEXT
		):
			return _error("GODOT_RUNTIME_INTEGER_OUT_OF_RANGE")
		if _index < _text.length() and _text.unicode_at(_index) in [0x2e, 0x45, 0x65, 0x2b]:
			return _error("GODOT_RUNTIME_JSON_NON_CANONICAL")
		var parsed := _text.substr(start, _index - start).to_int()
		return {"ok": true, "value": parsed}


	func _consume(code: int) -> bool:
		if _index < _text.length() and _text.unicode_at(_index) == code:
			_index += 1
			return true
		return false


	func _unexpected_code() -> String:
		if _index < _text.length() and _text.unicode_at(_index) in [0x20, 0x09, 0x0a, 0x0d]:
			return "GODOT_RUNTIME_JSON_NON_CANONICAL"
		return "GODOT_RUNTIME_JSON_SYNTAX"


	func _error(code: String, phase: String = "parse") -> Dictionary:
		return {
			"ok": false,
			"diagnostics": [{
				"phase": phase,
				"severity": "error",
				"code": code,
				"path": _document_path,
				"message": code,
			}],
		}


	func _is_digit(code: int) -> bool:
		return code >= 0x30 and code <= 0x39


	func _is_nonzero_digit(code: int) -> bool:
		return code >= 0x31 and code <= 0x39
