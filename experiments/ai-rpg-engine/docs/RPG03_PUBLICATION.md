# RPG-03 人工验收与 PR 交接

用户已明确“验收通过，可以PR”，授权本轮 Commit、Push 和 PR；未授权 Merge、Deploy、Release、Publish 或 RPG-04。

RPG03_STATUS.json、真实回执与研究 MANIFEST 保留自动验收时的 implemented_pending_manual_acceptance / claimAllowed=false 历史快照。本记录是其后的人工作出验收与发布授权，不修改原快照来追认权限。

固定实现基线：80221379cec850a2b25f5eeeb410233062f3e1ea。分支：codex/ai-rpg-rpg03-runtime。发布前 origin/main 为 ea6222900fe1534ac553e9f0a65935cc30ba757c；新增文件均属 RAG 等范围，与本轮变更无文件交集，不自动合并或迁移基线。测试证明固定候选，不声称已经验证上游合并结果。

验收：模块 230、父仓 stable Chat/service/canary 53、冻结文件 67；真实官方 Luna 派发 5/5（认证 1、历史失败 1、正常成功 2、取消 1）。剩余额度 0，无新增调用。取消仅确认客户端中止，上游确认和费用保持未知。独立验收实例已停止，私有内容与凭据不进入 Git。

交付包含运行合同、最小会话持久化与恢复、受控 HTTP/SSE、生成和取消、显式提交/放弃、受信插件宿主和开发 CLI。没有玩家前端、主持提示词编排、检索、长期记忆、市场或外部插件加载。原数据合同、内容工具和资源保持冻结。

回退：停用实验模块和隔离实例；不触碰共享服务、存储或历史证据。服务端新增逐请求字段默认 false，既有调用兼容。PR 编号及最终提交以 Git/GitHub 元数据为准，不写入自引用提交 hash。
