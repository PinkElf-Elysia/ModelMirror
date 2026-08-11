# Godot 官方运动参考适配说明

来源文件：`godotengine/godot-demo-projects/3d/kinematic_character/player/cubio.gd`，固定 commit `b4eff8de9d7ba5a4f1a2dea8bae60f28816b7eea`。

R6 只借鉴以下 MIT 模式：CharacterBody3D 在 `_physics_process` 中移动、相机方向的水平移动、重力、加减速选择，以及传送后调用 `reset_physics_interpolation()`。

正式 `first_person_controller.gd` 是独立第一方实现：删除跳跃/胜利逻辑，改用 InputMap 双键位、固定 3.5/12/16 参数、鼠标 yaw/pitch、输入归一、`move_toward` 速度收敛与测试 seam。参考文本扩展名为 `.reference.txt`，Godot 不会导入或执行它。
