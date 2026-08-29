# R20候选与Godot规则二次核查

R20未因R18结论直接引入依赖，而是重新核对与本轮固定策略相关的版本和运行规则。

- Beehave锁定证据仍为`v2.9.3 / 773a5f6d…`。该版本的兼容说明要求Godot 4.5及以上使用2.10或更高版本，但没有满足本轮固定来源门的2.10 tag，因此仅保留备选参考。
- LimboAI改用与Godot 4.6.3对应的`v1.7.1 / e2be164b…`；官方GDExtension归档为32,217,368字节，SHA-256为`ceb17571…e11a814e`。原生二进制和扩展表面没有为固定声明顺序策略提供足够净收益，因此不集成。
- Godot 4.6规则固定为：等待NavigationServer同步，设置`target_position`，每个physics frame调用`get_next_path_position()`；`CharacterBody3D`只在`_physics_process`中设置velocity并调用`move_and_slide()`。
- Kenney角色clip与版本证据仍不足。本轮只对已有静态Meshy人物做绑定，并用程序化胶囊做负载，不宣称动画完成。

结论：内部确定性Runtime为R20生产路径；Beehave、LimboAI和Kenney仅为可观察切换条件。若后续需要复杂可视行为编辑、并行树中止语义或完整角色动画，必须在对应轮次重新核查并独立资格。
