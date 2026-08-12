# ADR-0011：R10 Marble 原型闭环治理

状态：accepted

R10固定使用`marble-1.1`纯文本生成，消费panorama PNG作为360°环境视觉、collider GLB作为世界碰撞。SPZ、网页HQ mesh和对父Marble服务的运行依赖均不进入本轮。

模块新增独立Node provider、环境Bundle、确定性Assembler、loopback宿主和R10 Godot wrapper。R7 Scene Pack与scene lab保持冻结；panorama通过私有环境Bundle进入wrapper，不扩展正式Scene Pack。

真实调用由两道内容哈希绑定审批控制。普通verify只允许loopback和仓外已验证缓存，不读取供应商凭据或产生费用。任何候选失败保持上一份current与Godot世界不变。

回退按R10.6至R10.1逆序`git revert`；Git回退不删除`C:\tmp`运行目录或远程Marble world。
