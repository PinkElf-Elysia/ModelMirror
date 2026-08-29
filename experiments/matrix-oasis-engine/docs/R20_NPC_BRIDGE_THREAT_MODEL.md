# R20确定性NPC实体桥威胁模型

- **双写与分叉**：Node协调器是唯一写者；提交使用CAS、连续revision与哈希链，分叉或孤立commit一律fail closed。
- **Godot伪造到达**：只有锁定会话token、正确timeline/command sequence及physics阶段路径、地面和胶囊证据均有效时才允许裁决。
- **镜像漂移**：Node接受Action后，Godot必须回传相同before/after snapshot hash；不一致立即冻结时间线。
- **恢复换身**：恢复、发布、关闭及人工预览前从空Runtime完整重放；checkpoint、commit和引用文件均复验身份。
- **网络与密钥**：宿主仅绑定`127.0.0.1:43120`，无CORS、无redirect、无外部网络；token只经子进程环境传递且不落盘。
- **权限扩大**：行为规则只能选择R19已精确授权的现有Action；Action的`entityIds`不得提升actor权限。
- **空间回归**：目标仅取R14已验证approach anchor与R13 Facts；禁止案例坐标、翻转、ground target和随机避障。
- **范围漂移**：不实现AI、记忆、关系、对话、事件、任务或动画，不切换Creator默认入口。
