extends RefCounted

const SELECTOR_SCRIPT := preload("res://addons/gdgs/runtime/render/backend/gaussian_backend_selector.gd")
const REQUIRED_BACKEND := "Compute"
const FAILURE_CODE := "PACK_GODOT_SPATIAL_COMPUTE_UNAVAILABLE"


static func require_compute(context_node: Node) -> Dictionary:
	if DisplayServer.get_name() == "headless":
		return {"ok": false, "code": FAILURE_CODE}
	if str(ProjectSettings.get_setting("gdgs/rendering/backend", "")) != REQUIRED_BACKEND:
		return {"ok": false, "code": FAILURE_CODE}
	var backend: RefCounted = SELECTOR_SCRIPT.get_backend(context_node)
	if backend == null or not backend.has_method("get_display_name"):
		return {"ok": false, "code": FAILURE_CODE}
	if str(backend.call("get_display_name")) != REQUIRED_BACKEND:
		return {"ok": false, "code": FAILURE_CODE}
	return {"ok": true, "backend": backend}
