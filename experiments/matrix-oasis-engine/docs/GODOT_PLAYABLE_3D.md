# R6 第一人称可玩 3D 合同

R6 将冻结 R5 Runtime inspection 投影为动态 3D Action 终端。终端不扩展 Runtime Pack Schema，也不持久化坐标或绑定信息。

固定参数：60 Hz；速度 3.5 m/s；加速度 12 m/s²；减速度 16 m/s²；俯仰 ±85°；交互距离 3 m；终端最多 64 个、8 列确定性网格。世界、玩家、交互区分别使用碰撞层 1、2、3。

输入：WASD/方向键移动，鼠标观察，E/Enter 执行中心射线命中的可用 action，Esc 释放鼠标，左键重新捕获。ending 清空终端；reset 重建入口会话和玩家起点并重置物理插值。

成功 action 原子提交 R5 返回的 snapshot、inspection、transition 与 Cue 后重建终端。未知、超距、禁用或 Runtime 失败都不改变会话和世界。

稳定输出标识：

- `MATRIX_OASIS_R6_PLAYABLE_3D_READY`
- `MATRIX_OASIS_R6_3D_TRACE_JSON:`
