# AGENTS.md — 矩阵绿洲独立实验模块

本文件适用于 `experiments/matrix-oasis-engine/**`，并在父级 `AGENTS.md` 基础上增加更严格的隔离规则。

## 强制边界

1. 只修改本目录；任何父仓文件变更必须先取得用户明确批准。
2. 禁止依赖父 `client/`、`server/`、根配置、数据库、Docker、CI、路由、资产或构建产物。
3. 禁止模块外 `file:` / `link:`、符号链接、绝对路径或目录穿越。
4. 不提交密钥、真实 `.env`、日志、依赖目录、构建产物、Godot缓存、测试报告或生成资产。
5. 一批只解决一个可验证目标；先验证后提交；失败不得进入下一批。
6. 回退只使用 `git revert`，不得重置、覆盖或清理用户工作区。

## R12 专属限制

- R12只证明最初末班地铁案例从纯自然语言到可玩3D的真实全链路，并用既有中性真实缓存证明同一实现可泛化；不新增AI NPC、记忆、动画、语音、任务生成、世界事件、战斗、存档、多人、正式导出或父项目接入。
- R1–R11的合同、验证器、编译器、Runtime、Scene/Spatial格式、examples、既有Creator/Godot模式、vendor、ADR和验收记录全部字节冻结；只有机器白名单精确列出的兼容扩展、R12接线、测试和文档可修改。
- 冻结末班地铁JSON只作语义oracle。生成提示和一方源码不得包含冻结ID、Schema片段或案例专属执行分支；所有验收策略只使用通用数量、类型和图约束。
- Generator保留原两参数行为；acceptance profile为可选第三参数，模型总请求最多3次。Assembler profile v1保持默认，v2最多4 zones、6个非环境brief、32 placements、每zone 8项。
- Marble空间源必须在同一world链读取官方full-res SPZ、panorama、collider、metric scale与ground offset；缺失尺度元数据即失败，不允许人工常量回退。
- 普通verify只使用合成夹具或已验证仓外缓存，不联网、不产生费用、不读取供应商凭据。真实模型及Marble+Meshy调用必须分别获得当次批准，批准不可跨阶段或任务复用。
- 所有真实资产、供应商ID/URL/原始响应、截图、日志和资格run只允许保存在 `C:\tmp`，不得提交或写入诊断。
- 所有metric scale、ground offset、坐标变换、落地和避碰证据必须进入canonical bundle/report；不得用案例坐标、人工试摆或隐藏常量掩盖错误。
- `docs/MVP_STATUS.json`与`check:mvp-claim`是初版声明硬门。在R12真实末班地铁资格、Node/Godot三结局与循环、人工图形/性能和中性泛化全部通过前，`claimAllowed`必须保持`false`。
- 不push、不创建PR，直至用户明确回复“R12验收通过，可以创建PR”。
- 不删除或复用其他分支/worktree，不重建共享栈。主线前进时先报告差异，不擅自rebase。

## 提交前检查

```powershell
npm.cmd run verify
npm.cmd run check:round-scope
git status --short
git diff --cached --name-only
```

所有相对固定基线的变更路径必须以 `experiments/matrix-oasis-engine/` 开头。
