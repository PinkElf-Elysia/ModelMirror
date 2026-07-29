# Dify 历史集成与兼容状态

> **状态：历史/归档。** 本文不再是安装、架构或主路径指南。当前
> `/workflow` 使用 classic React Flow，`/rag` 使用 ModelMirror 本地知识系统。
> 当前部署见 [DEPLOYMENT.md](./DEPLOYMENT.md)。

最后校准日期：2026-07-28
原方案日期：2026-06-10

## 当前事实

仓库仍保留：

- `server/api/dify_proxy.py`
- `/api/dify/*`
- `client/src/components/dify/DifyWorkspaceFrame.tsx`
- `client/src/pages/WorkflowEditorPage.tsx`
- `DIFY_API_BASE_URL`、`DIFY_API_KEY` 和 `VITE_DIFY_WEB_URL` 示例

但：

- `client/src/App.tsx` 没有把 Dify iframe 挂载到 `/workflow` 或 `/rag`。
- `docker-compose.yml` 没有 Dify 服务或 Dify 健康依赖。
- 默认启动、测试和人工验收不要求 Dify。
- legacy `/api/dify/health` 不能作为 ModelMirror 平台健康门禁。

这些文件属于待独立审计清理的 compatibility surface。删除前需要确认没有私有部署
或外部调用方依赖，不能在普通文档整理中直接移除。

## 历史方案

2026-06-10 的回退版本曾采用：

```text
/workflow -> Dify Web iframe
/rag      -> Dify Web iframe
/api/dify/* -> Dify App API proxy
```

当时这样做是为了在一次失败的大规模重写后恢复可用入口。该决策解决了当时的
P0，但后续 classic 工作流、本地 RAG、Knowledge Pipeline、Agent Runtime 与
Data X 已逐步形成 ModelMirror 原生闭环，因此“Dify 是稳定主路径”的结论已经
失效。

失败背景见
[postmortem-workflow-rewrite.md](./postmortem-workflow-rewrite.md)，后续原生建设
结果见 [workflow-native-design.md](./workflow-native-design.md) 和
[RAG_INTEGRATION.md](./RAG_INTEGRATION.md)。

## 如需临时兼容

只有在明确知道调用方仍依赖 legacy 代理时，才配置：

```bash
DIFY_API_BASE_URL=http://localhost:5001/v1
DIFY_API_KEY=app-your-dify-api-key
```

可检查：

```bash
curl http://localhost:8000/api/dify/health
```

注意：

- 这不会把 `/workflow` 或 `/rag` 切回 Dify。
- 不要把 `DIFY_API_KEY` 放入前端。
- 通用代理可能扩大外部 API 面，公网部署前必须独立审计授权和错误脱敏。
- 模镜没有为当前 Dify 版本提供持续兼容承诺或自动化回归。

## 清理门禁

未来删除 legacy Dify 代码前必须：

1. 扫描所有代码、环境示例、私有部署说明和外部调用方。
2. 为 `/workflow`、`/rag`、Compose、设置页和后端启动运行回归。
3. 从环境示例、`server/main.py` 和第三方声明中同步移除无效引用。
4. 单独提交，提供无需迁移业务数据的回退方案。

在完成上述门禁前，状态是“代码保留但不推广”，不是“稳定集成”。
