# Studio RPG 集成与运行

基线 a7d99925584e818e7f2df84b1646256abfc08ed6，分支 codex/studio-rpg-integration。2026-09-17 用户已审阅两份真实输出与入口，明确“通过，继续提交补充 PR”。该意见仅覆盖本轮；行间 RPG05 仍为 archived_partial_demo，旧调优和完整质量验收继续延期。

## 封装与入口

Studio → RPG（/rpg）集中提供地球 OL 和行间两张卡，/rpg/:cardId 同源嵌入原卡 UI。client/server.mjs 仅增加 /rpg-app/ 到独立 Node 服务的代理；父仓 /api/chat 和后端源码没有为本轮修改。

studio/Dockerfile 从仓库锁文件构建两套 UI，打包冻结提示词、已批准卡包与服务端模型配置，无需旧 .local 资源。rpg05-prompt.json 只机械提取旧批准实验的 system；其来源及 hash 在文件中。未嵌入测试角色、旧测试开局或额外 JSON 协议。密钥不在镜像、Git、前端或请求消息中。

默认均通过 OpenRouter → google-ai-studio → google/gemini-3.8-flash，不降级、不自动重试。供应商 slug 与展示名分别保存。公开 endpoints 元数据确认 temperature、top_p、max_tokens 支持，context=1048576、max completion=65536；本宿主最大输出限制32768。配置与模型资格以本次实际成功回执为证，不外推以后永久可用。

地球：temperature .7、top_p .8、max_tokens 16384；TOP K、惩罚、thinking_budget 不传，不宣称0思考（本次回执有743 reasoning tokens）。context8192仅作者界面设置，不截断。首轮 system=完整冻结作者system去掉此前用户授权的一句无条件深层声明，user=角色+输入；后续 system + 全部原始历史 + prefix/input/suffix，仅一次。世界书候选默认关闭。

行间：温度0、max_tokens8192，保留旧基准参数，只把默认模型改成用户指定Gemini。system=已冻结的3925字轻量system；首轮玩法/所选世界/开局/角色为独立user，再接本轮行动；续轮保留首轮配置和全部原始user/assistant历史。并不套用地球前后置词。

## 授权、证据与验收

本轮独立预算每卡1次，已用2/2、剩余0。每份在发送前原子占位，失败/取消/未知也保留，不随重启重置。两个模型调用均HTTP200、finish=stop、[DONE]完整，实际模型和供应商吻合，实际 messages 与冻结预检逐字一致。没有自动追加测试。

完整原文、SSE、请求、占位和回执保留在 Docker rpg_sessions 卷的 /data/dispatches/{earth|rpg05}/slot-1。非秘密归档位于本轮 .rpg04-work/studio-deploy/provider-evidence。回执 hash、实际用量和用户意见见 studio/STATUS.json。上游报告费用合计0.055086美元；未做独立账单审计。技术回执不进入模型消息。

验收命令：

- node --test experiments/ai-rpg-engine/studio/studio.test.mjs：4通过；同镜像 --network none 再运行4通过。
- 地球 npm test：26通过；行间 UI：3通过；旧 ui-host*.test.mjs：186通过。
- 主前端构建、两卡TypeScript/构建、主代理header测试通过；帮助内容与页面51通过。
- 浏览器：Studio统一入口→两卡，原创建界面、返回、恢复真实输出、原文入口；390px RPG列表无横向溢出。截图只登记已实际归档JPEG，不宣称全页PNG交付。

初始发现与修复：新页面漏注册路由，点击落回/models；补路由后实查成功。旧UI测试在Node中缺location，增加typeof保护后同套3通过。派发前纠正供应商标识为slug，删除此前实测没有的额外推理参数。返回选择页的余额改为随cardId变化刷新。新增指南初次6项失败，修正目录、标题、结构和基线截图路径后同套51通过；一次中间命令因错误工作目录未运行测试。以上均在实际调用/交付前核验；不把修复后的结果冒充初始版本通过。

未运行：完整父仓后端回归、全部169截图状态、完整键盘路径、所有移动端卡页、长期多回合真实测试。原城市/姓名/道号词库、概率、世界书触发元数据缺口未补造。两次样本不等于普遍内容质量通过。

## 部署与配置保留

共享server/client从原部署b5e0e85e更新到上述基线+本轮前端修改，新增modelmirror-rpg。server/client现有环境值、网络、挂载均保留；已有关闭项没有因无关需要被盲目打开。RPG_ENABLED=true，真实通道启用，但用完授权预算后禁止追加生成。第三方容器和编码worker镜像未重建；其运行时代码目录跨上述基线无差异，冻结配置继续保留。

用户明确允许部署脚本只在内存载入并传递既有配置。studio/deploy-shared.py 不输出或落盘凭据，操作日志仅含键名、镜像与挂载元数据。首次自动审批拒绝早期未经确认的读取尝试；取得明确授权后才继续。一次UTF-8读取失败发生在部署前；一次原始路径比较失败来自Docker Desktop的等价路径拼写，原失败记录保留，规范化后确认挂载未变。

首次部署命令（须显式批准共享更新）：设置RPG_ENV_FILE为既有服务.env绝对路径，先构建server/client/rpg候选镜像，再运行python studio/deploy-shared.py plan，检查后deploy。新装环境可以在现有Compose最后追加docker-compose.rpg.yml；默认secret指向server/.env且只由服务内提取OPENROUTER_API_KEY。禁止把整个.env复制进构建上下文。RPG_PUBLIC_ORIGINS只允许部署对应来源。

回退：server/client旧镜像标签modelmirror-server:pre-studio-20260917与modelmirror-client:pre-studio-20260917保留。在同一组原Compose+配置保留overlay上将image指向旧标签，使用up -d --no-build --no-deps server client；不要down或删卷。RPG可单独停止，但保留rpg_sessions、调用账本和原件。回退脚本不可将当前镜像覆盖原始回退基准。主工作区和旧预览不受本轮源码撤回影响。

## Help Center Impact

新增play-rpg-cards指南、实验模块RPG条目与真实入口按钮截图；基线a7d99925、日期2026-09-17。独立18421预览及共享5173入口实际重放，正式调用各一次另有授权。用户已人工通过。历史Earth DELIVERY保留；本轮源文件/图片hash由studio/DELIVERY.json统一登记。
