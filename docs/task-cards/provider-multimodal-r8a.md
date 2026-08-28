# 任务卡：R8A 多模态 Provider 控制面基础

## 范围

- 原始实施基线：`origin/main@f0150fb5daebcf5a4a70b0f350e6129dae7ff8d0`；收尾时经交叉审计
  安全快进并在 `origin/main@172137fc752803be68e9a319a8ba23ea83d4142f` 重跑门禁。
- 只建设 SQLite v18、scope、entry、shape、Adapter、Policy、Binding、资格会话和 Receipt 基础。
- 不接管图片、PDF、音频、视频或 Realtime 数据面，不执行真实付费认证。
- 不改变 `/api/chat` SSE、R5—R7、Catalog 数量、提示词选择器或现有多模态协议。

## 安全边界

- 所有 R8 Feature Flag 默认 `false`，所有 R8 Policy 在 R8A 均为
  `data_plane_integrated=false`。
- R8 资格请求必须在目录刷新和任何 Provider 网络请求前稳定阻断。
- Adapter、Provider 类型、scope 和执行形态必须精确匹配；资格不得跨形态继承。
- v18 只保存脱敏身份、指纹、状态与指标，不保存用户媒体、Prompt、转录、SDP 或模型正文。
- 重启将已派发但未完成的资格会话标记为 `uncertain`，绝不自动重放。

## 验收

- v17→v18 加法迁移、旧数据保留、租户隔离和旧代码忽略新表通过。
- Adapter/provider/scope/shape 错配测试通过；newAPI 不可取得视频任务或 SDP Realtime 资格。
- Settings 可管理新 scope 并显示明确 Adapter；认证和激活均诚实标记为后续批次。
- R5/R6/R7 与多模态现有路径回归，前端测试、typecheck、build 和 Compose config 通过。
- 严格证伪重复派发、派发后回退、内容/凭据泄露和配置漂移；最终停在 Commit/Push/PR 前。

## 回滚

关闭所有 R8 Feature Flag 并回退代码；保留 v18 表、资格会话、Receipt 和媒体任务新增列。
不得删除 Router SQLite、Provider 凭据、媒体任务或 newAPI 数据。

## Help Center Impact

- 影响用户体验：是。Settings 增加多模态 scope、Adapter、资格与 Policy 基础状态。
- 正式文章：`client/src/content/help-center/articles/recover-unavailable-feature.md`，明确
  R8A 可配置不等于数据面已接入，认证与激活仍会在付费调用前阻断。
- 独立预览与帮助增量证据：
  `docs/help-center/evidence/provider-multimodal-r8a.md`。
