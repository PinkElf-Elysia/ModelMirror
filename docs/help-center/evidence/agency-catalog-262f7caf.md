# 专家目录更新验收

- 日期：2026-10-13；隔离分支基线：262f7caf。
- 来源：jnMetaCode/agency-agents-zh，固定提交 e5a6c0a730878aaa871ec5b95859aec2c2728475。
- 目录：277 位专家、20 个部门；公司经营部 7 位；保留原有 268 个本地 ID。
- 隔离预览：构建 client 后，使用 modelmirror-client:agency-catalog-20261012 镜像，将 client/dist 只读挂载至 /app/dist，绑定 127.0.0.1:15177:80。
- 未连接共享数据卷或模型服务；可选工作区状态请求不可用，不影响本次目录浏览验证。

## 两轮只读页面实操

两轮均从 /agents 打开目录，确认 277 位、20 个部门，选择公司经营部看到 7 位，搜索幕僚长，打开查看简历并核对固定提交的来源链接，最后清空岗位要求恢复 277 位。打开 /help/choose-model-agent-workflow 确认新增帮助步骤。

截图来自实际预览页面，原视口 1270×714，缩放至宽 1000 像素并压缩 PNG：

![公司经营部](/help-center/262f7caf/agent-market-company.png)

## 验证与边界

- 前端生产构建通过；存在原有大 chunk 提示。
- helpContent、AgentsPage.platform-capabilities、AgentConfigPage：3 文件 23 项通过。
- verify:help-images 通过；后端目录快照测试 4 项通过。
- 来源 --check 通过；错误来源提交、脏来源与快照漂移拒绝路径已验证。
- 共享栈此前已定向部署目录并通过用户验收；仅 server/client 更换，其余 32 个服务保留。此次补充的帮助文档在隔离预览验证，未再次部署共享栈。
- 未发起模型请求，未验证真实模型输出，未执行全量后端测试。
- 本次 10 个文件共同覆盖导入器、双端快照、回归测试、来源声明及帮助文章、元数据、截图、证据，无新增依赖或数据迁移。
- 代码回退：恢复本次涉及的文件并重新构建；共享栈回退使用本机 C:/tmp/modelmirror-agency-deploy-20261012/deploy.py rollback，保留数据卷。
