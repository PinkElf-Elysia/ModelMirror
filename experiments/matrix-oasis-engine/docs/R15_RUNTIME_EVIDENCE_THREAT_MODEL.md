# R15运行证据威胁模型

## 信任边界

Runtime、Receipt、Facts、Intent、Asset Bundle、R14 Solution/Verification、Replay Plan、缓存目录和媒体输出均不受信任。入口必须复验canonical bytes、身份、哈希、计数和FileHandle/realpath/bigint对象身份。

## 主要威胁

1. 用直接Runtime调用、trace helper或玩家传送伪造实际可玩证据；
2. Replay Plan省略ending、循环、disabled action、reset或可达节点；
3. 导航截断、错误terminal focus、超距或遮挡仍被记录为成功；
4. Movie Maker固定FPS被误报为实时性能；
5. 黑屏、冻结、闪动、穿墙或异常传送被结构化绿测掩盖；
6. 自动修复改写Intent、阈值、资产或案例坐标，形成题材过拟合；
7. staging、current或媒体目录被junction、symlink或换身；
8. 资格脚本读取凭据、联网或泄漏路径和供应商信息；
9. 单案例证据被误报为泛化或Creator闭环；
10. R15通过被提前描述为MVP完成。

## 控制

- 仅`Input.parse_input_event()`驱动实际InputMap、控制器、射线和terminal；
- 通用Runtime BFS、闭合合同、静态诊断、固定上限和身份链；
- 等待physics/navigation同步，逐checkpoint记录path、floor、capsule、focus、视线和资产AABB；
- 功能录像与实时300帧性能分离；
- 初始证据加最多两轮candidate-exclusion-only重求解；
- 无法映射的失败立即停报，不隐藏重试；
- 仓外事务发布和进程有界清理；
- R11与R12走同一代码路径，普通verify零网络；
- `pending-runtime-evidence / claimAllowed=false`机器锁定。
