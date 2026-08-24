# R16 Creator资格威胁模型

## 信任边界

source、solved、evidence与qualification缓存、current指针、浏览器请求、Provider配置和Godot进程均不受信任。入口必须复验canonical bytes、交叉身份、哈希、文件对象身份和限定根目录。

## 主要威胁

1. 旧source、solved或evidence被冒充为完整Creator资格；
2. Solution变化后复用旧Evidence；
3. 缓存命中仍读取凭据、联网或产生供应商调用；
4. 资格失败、进程重启或并发请求覆盖上一份current；
5. R15修复被扩展为修改语义、阈值、资产或案例坐标；
6. API跨站、cookie绕过、重复审批或状态竞态跳过资格阶段；
7. junction、symlink、路径穿越或文件换身污染资格目录；
8. 仅合成或单案例结果被描述为可泛化MVP；
9. 人工验收前切换默认入口或解除声明门。

## 控制

- 独立闭合Qualification合同绑定R10/R12 source、R14 Solution/Verification和R15 Replay/Evidence；
- 每个新Solution首次完整取证，同一资格缓存只离线复验；
- same-origin、HttpOnly/SameSite cookie、单活动run和单Godot进程继续生效；
- 同父staging、FileHandle/realpath/bigint身份复验和最后原子current；
- 失败保留旧current，无法安全清理的staging保留并静态报告；
- 双真实缓存与合成拓扑全部零网络、零凭据、零费用；
- R16.7由人工验收显式解锁。
