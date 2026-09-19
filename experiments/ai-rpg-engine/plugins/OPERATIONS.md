# 可选插件候选：运行、合同与回退

本候选在独立分支 codex/rpg-plugin-branch-save，基于 bac37a6e。目录只含审核的第一方插件 rpg.branch-save@1.0.0；不支持下载或执行外部包。可信代码错误可隔离，不承诺第三方代码安全沙箱。Studio原布局和旧RPG合同不变。

## 生命周期合同

- `GET /rpg-app/api/plugins` 返回目录、精确版本、artifact/manifest SHA256及登记revision。POST同前缀 `/:id/install|uninstall` 校验操作ID、版本/hash及预期登记revision。
- 会话 `GET /rpg-app/earth/api/sessions/:id/plugins/rpg.branch-save` 查看启用状态；POST其 `/enable|disable` 另校验sessionId、预期会话revision及启用时的完整权限清单。安装不会启用，新分支不复制授权；卸载撤销全部会话授权。升级不自动安装。
- POST会话 `/branches` 接收 `{operationId,expectedSessionRevision,turn,name}`。会话必须版本匹配、已完成完整回合且无在途/未确认请求。相同操作和内容重放返回同一会话；改变内容拒绝。GET插件 `/nodes` 读取后台节点，要求仍授权。
- 权限仅本会话完成历史读取、受控分支准备与界面贡献。插件不能直接修改提示词、文件、联网或派发模型。节点保存和核心分支发布由宿主完成；提交时再次校验授权epoch。所有写操作同源校验，前端不能提交system或供应商配置。
- 同一数据目录只支持单宿主实例。owner.lock冲突应先核对进程，不可无条件删除。故障时关闭插件能力、保留核心历史；读写失败不得当成空白数据重新初始化。

## 数据与版本

`RPG_DATA_DIR/earth/`保留普通会话及`branches/`原子封套；封套中的核心session、不可变snapshot、插件节点和幂等操作分字段保存。`plugins/registry.json`登记安装/授权，`dispatches/earth/`为所有路线共用的原卡账本。节点名不注入模型。复制会话不会复制可执行请求、授权或预算。

新会话由服务器绑定作者文本、装配器、卡片、模型配置及存储源码hash，另绑定角色/世界/参数。匹配时才可续玩；旧会话无充分证据保持可读，不追认或迁移。完整原始消息和Provider原文保留；渲染限制不改变原文。当前账本采用原服务目录计数，禁止通过新目录或调高配置绕过原授权。

## 候选构建与无网检查

从 `experiments/ai-rpg-engine/` 运行：

```powershell
docker build -f studio/Dockerfile -t modelmirror-rpg:plugin-b4 .
docker run --rm --network none --read-only --tmpfs /data:rw,uid=1000,gid=1000,noexec,nosuid --entrypoint node modelmirror-rpg:plugin-b4 studio/plugin-container-smoke.mjs /data/check <当前runtimeHash>
```

runtimeHash从DELIVERY.json取得。检查只用新建tmpfs数据，强制Provider关闭和预算0；验证两卡静态资源、安装不启用、离线完整回合分支、幂等、卸载/宿主重启/读取与续玩、快照及父路线不变、重装不恢复权限。不是两卡真实模型或内容质量验收。不要把这些命令改成挂载已有用户卷的烟测。

Docker构建上下文由studio/Dockerfile.dockerignore排除.env、原件私有目录、会话、账本、node_modules及宿主构建产物。Dockerfile仅将锁定依赖、明确模块代码和作者资源复制到镜像；供应商密钥不在镜像、前端或提示词中。父前端另运行client的npm.cmd run build，RPG路由沿既有代理，不修改共享Compose。

## 保留配置与恢复

本批没有共享部署授权。后续发布须独立确认镜像、父前端、现有数据卷、原预算及服务配置的绑定，不重建空账本，不暴露凭据。先只读核对现有会话/分支/回执清单并保留备份；只在明确授权后用原数据目录与相同额度运行，禁止自动派发或重放pending。

优先停用插件回退，保留当前核心服务读取新分支格式。卸载不删除会话、节点或账本。旧B1及更早宿主无法枚举新branches目录，因此尚不允许直接降级镜像；需要先在数据副本验证读取能力。不要修改旧会话runtime来伪造兼容。恢复未确认生成先读原记录，不换ID重发；分支使用同一操作ID核对已提交结果。

只停止已核对归属的本批预览/临时容器。保留18433预览、历史B1/B2/B3清单和全部证据；本文不是操作共享8000/5173的授权。
