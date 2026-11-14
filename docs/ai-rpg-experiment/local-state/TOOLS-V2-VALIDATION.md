# 原生工具第二轮：离线验证记录

本文件保留初始离线阶段证据，不代表当前尚未实测。当前原型验收、最终回归与真实调用统计见 [收尾记录](TOOLS-V2-CLOSEOUT.md)。

基线 3fffd5b49a94a73cbf00ac409a0d9b06308d34c7。工作树 codex/rpg-local-state-tools-v2；本文件所列初始验证使用模拟响应；后续真实调用另有记录。

## 验证命令与结果
在 `experiments/ai-rpg-engine`：
```text
node --test local-state/*.test.mjs local-state/tools/*.test.mjs authoring/variable-panel.test.mjs authoring/variable-runtime.test.mjs harness/shop-store.test.mjs
```
整组88项通过。随后新增单次生成未知恢复和费用计量检查，相关文件分别重跑通过；工具组19项通过后，再增补的计量文件4项通过。合计覆盖90个不同 Node 测试，不能把多次执行次数相加当测试数量。

在仓库根：
```text
python -m pytest server/tests/test_rpg_local_state_tools.py server/tests/test_rpg_bridge.py -q --tb=short --basetemp <独立短临时路径>
```
整组73项通过。随后将后端工具声明改为随服务器模块打包，新增合同一致性检查；新工具文件5项复测通过，合计覆盖74个不同后端测试。
已有 FastAPI on_event 弃用警告4条，不属于本轮改动；未修整全项目。

前端为已有原生 JS 页面，未改 TypeScript 或构建配置：`node --check experiments/ai-rpg-engine/local-state/public/app.mjs` 通过；HTTP新旧页面场景检查通过；内置浏览器完成初始化、查询后消耗、面板及查询依据检查。
`python -m py_compile server/model_router/rpg_bridge.py server/model_router/rpg_local_state_tools.py` 通过；`git diff --check` 通过。

测试环境问题：系统默认 pytest 临时目录权限不足；较长路径也阻断回执写入。改用本任务 C:/tmp/lsv2*-<随机后缀> 短目录，不改共享目录权限。父项目导入缺少仓库已声明的 duckdb==1.4.1，仅安装到本工作树 .rpg03-work/pydeps 并在测试进程设置 PYTHONPATH；没有改依赖文件或全局环境。

## 覆盖
- 一次直接生成、查询后两次生成，工具消息匹配、参数分片与签名续传。
- 数量5→3；空增量、模糊名称、无匹配、未设定和部分返回。
- 非法工具、非法参数、截断、再次查询、明确失败均不提交，不自动重试。
- 第一阶段和第二阶段结果未知，重启后核对原结果；重复提交不多结算。
- 路由冻结、累计预算、明确预算拒绝；阶段数量与供应商调用数可追溯。
- 候选预览不写状态、唯一采用、检查点分支和旧模式兼容。
- 插件装配、历史投影、世界书触发和记忆输入沿用第一轮检查；不宣称已调用真实总结/记忆模型。
- 宿主和原生 HTTP 调用器组合测试；后端通过 MockTransport 检验真正的上游工具 payload，而非仅测函数。

## 同开局同输入的离线请求组成
运行：`node experiments/ai-rpg-engine/local-state/tools/measure.mjs`。
输入固定为“核对丹药后服用两份养息丹”。查询决策为夹具，以下是实际组装请求字节，不是 token 或费用。

| 路径 | 请求体字节 |
|---|---:|
| 第一轮模式直接生成 | 535,310 |
| 原生工具声明、直接生成 | 537,122 |
| 查询路径首次请求 | 537,122 |
| 带工具结果的续写请求 | 537,983 |
| 查询路径累计 | 1,075,105 |

工具声明766字节，工具结果534字节；新模式系统消息528,178字节。查询续写重发提示词是主要成本风险，未通过更改世界书或上下文语义掩盖它。
费用计量复用公共模块的微美元向上取整，可能因二进制浮点多上取1微美元；原始供应商金额保存于 SSE，不把取整值冒充原精度报价。

## 初始离线阶段未验证（历史）
真实 Gemini 是否自主选择正确查询、参数和增量稳定性、正文质量、实际累计费用以及可靠剧情正文首字时间仍待新授权。
页面中的模拟费用0不代表真实免费。未部署正式前端，未改共享路由，未实现角色知识权限、多模型备用或世界书检索。
