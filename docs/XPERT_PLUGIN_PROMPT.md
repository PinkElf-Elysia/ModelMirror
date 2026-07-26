# Xpert Prompt Command 与声明式 Plugin

## 1. 能力边界

`XPERT-PLUGIN-PROMPT-03` 提供两类可版本化资源：

- Prompt Profile：可编辑草稿、不可变发布版本、Xpert 固定绑定与斜杠命令。
- Plugin Package：可信本地 ZIP 中的声明式 Prompt、Skill、固定 Toolset 引用和中间件预设。

Plugin 是资源聚合包，不是通用代码插件。服务端不会从包中导入 Python/Node 模块，也不会执行初始化脚本。Skill 脚本仍只能通过既有 `skills_runtime` 和隔离 Sandbox 显式运行。

## 2. Prompt Profile

Prompt Profile 包含名称、slug、描述、1 至 5 个命令别名、模板、参数提示、标签和 `public_app_allowed`。草稿使用 revision 乐观并发控制；发布生成递增且不可变的版本。

第一版模板只允许一个占位符：

```text
{{args}}
```

参数是命令后的单段原始文本，最多 8,000 字符。别名只能使用小写 ASCII 字母、数字、`_` 和 `-`，发布时必须全局唯一。

Xpert 草稿保存 Prompt 绑定；发布 Xpert 时，`latest` 会解析为具体 Prompt 版本并写入不可变 XpertVersion。后续 Prompt 草稿或新版本不会改变已经发布的 Xpert。

## 3. 命令解析

已发布 Xpert Chat 接受以下输入：

- `/review 当前改动`：查找当前 Xpert 固定的 `review` 命令，渲染模板后运行当前 Xpert 的完整工作流。
- `//review 当前改动`：按普通文本 `/review 当前改动` 发送，不进行命令解析。
- `/unknown`：在模型调用前失败，并返回当前 Xpert 可用命令提示。

会话记录保留用户输入的原始命令；模型只接收渲染后的任务。命令不会启动其他工作流，也不能覆盖已发布 Xpert 的 role prompt。

公共 App 只暴露直接绑定且 `public_app_allowed=true` 的固定 Prompt 命令。Manifest 只返回名称、描述、别名和参数提示，不返回模板正文。

## 4. Plugin Package

ZIP 根目录必须包含 `modelmirror-plugin.json`：

```json
{
  "schema_version": 1,
  "name": "Research Toolkit",
  "slug": "research-toolkit",
  "description": "Research resources",
  "license": "MIT",
  "prompts": [],
  "skills": [],
  "toolsets": [
    {
      "toolset_id": "toolset_id",
      "version": 1,
      "schema_hash": "sha256"
    }
  ],
  "middleware_presets": []
}
```

限制：

- ZIP 最大 20 MB、最多 200 个文件。
- Prompt、Skill、Toolset 引用和中间件预设各最多 10 项。
- 拒绝绝对路径、`..`、symlink、`.git`、可疑隐藏路径和路径逃逸。
- Toolset 必须引用已发布固定版本和匹配的 `schema_hash`，不包含 Credential。
- 中间件必须已注册；同类配置冲突不得静默覆盖。
- Skill 文件随 Plugin 版本命名空间化安装，运行时仍受 Skill/Sandbox 安全边界约束。

导入只产生草稿；校验依赖后必须显式发布。发布版本固定包校验和、资源清单、Toolset schema hash、中间件配置和命名空间化 Skill ID。

## 5. 工作流绑定与执行

`plugin_resource` 使用专用资源边：

```text
plugin_resource -- plugin-binding / plugin --> workflow_agent
```

绑定边不参与控制流、变量可达性、拓扑排序或节点调度。同一 Plugin 节点只能绑定一个 `workflow_agent`，也不能同时使用普通控制边。

运行时将固定 Plugin 版本编译到目标 Agent：

- Toolset：复用固定版本 Toolset Provider、Tool Policy、HITL、Audit 和 checkpoint。
- Skill：通过 Plugin Skill resolver 进入 `skills_runtime`、Sandbox 和显式 Hook。
- 中间件：合并到目标 Agent Pipeline，并按 priority 稳定排序。
- Prompt：作为当前 Xpert 的私有命令；Classic Workflow 运行不自动解析聊天命令。

工具名、Prompt 别名或同类中间件发生冲突时，Workflow/Xpert 发布预检失败。

## 6. API

Prompt Profile：

- `GET/POST /api/prompt-profiles`
- `GET/PATCH /api/prompt-profiles/{profile_id}`
- `POST /api/prompt-profiles/{profile_id}/validate`
- `POST /api/prompt-profiles/{profile_id}/publish`
- `POST /api/prompt-profiles/{profile_id}/archive`
- `GET /api/prompt-profiles/{profile_id}/versions`
- `GET /api/prompt-profiles/{profile_id}/versions/{version}`

Plugin：

- `GET /api/plugins`
- `POST /api/plugins/import`
- `GET/PATCH /api/plugins/{plugin_id}`
- `POST /api/plugins/{plugin_id}/validate`
- `POST /api/plugins/{plugin_id}/publish`
- `POST /api/plugins/{plugin_id}/archive`
- `GET /api/plugins/{plugin_id}/versions`
- `GET /api/plugins/{plugin_id}/versions/{version}`

资源选择：

- `GET /api/workflow/resource-options?kind=plugin`

## 7. 安全与持久化

- Store 使用文件锁语义和原子替换；容器部署必须持久化 Xpert storage。
- API、audit 和 checkpoint 不返回 Credential、Skill 脚本正文、完整 Prompt 模板、工具输出或物理路径。
- 公共 App 拒绝 `plugin_resource`，因为 Plugin 可能隐式携带 Skill、Hook、Toolset 或私有中间件。
- Plugin ZIP、持久化 Store、生成的 Skill、`.env`、API key 和构建产物不得提交。

## 8. 验证

至少运行：

```bash
python -m pytest server/tests/test_xpert_plugin_prompt.py -q
python -m pytest server/tests/test_workflow_native_validate.py -q
python -m pytest server/tests/test_workflow_resource_nodes.py -q
python -m pytest server/tests/test_workflow_toolset_resource.py -q
python -m pytest server/tests/test_xpert_publish.py -q
python -m pytest server/tests/test_xpert_app_api.py -q
cd client
npm.cmd run build
```

Docker 验收应覆盖 Prompt 固定版本、未知命令与 `//`、Plugin ZIP 导入/发布、Plugin 资源绑定、冲突阻断、Skill Sandbox 执行、Goal/Handoff 固定版本复用、App Prompt 白名单和 App Plugin 阻断。
