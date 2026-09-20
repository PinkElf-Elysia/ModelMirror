# M2 二次压缩收尾

用户已明确接受：“有偏差，但在初版可接受范围内，可以收尾后PR”。接受限于本次初版有损压缩，不等于长期稳定、无损或无限上下文证明。四项具体偏差保留于 REAL-REPEAT-REVIEW.md，输入输出不改写。

## 基线与发布范围

- 原实现基线 fb762c5a；最新 origin/main 为09e8a7d6，新增103条路径均在独立 matrix-oasis-engine，与本补充批次无交叉。
- 在本独立 codex/rpg-summary-overflow 工作区合入最新主线，整合基线56fe2e2d。未动脏主工作区、旧会话、原调用账本或旧预览。
- 已有M2 PR #387仍OPEN，因此更新同一PR（远端codex/rpg-rolling-summary-pr），不重复提交M2基础实现。保留Draft条件，合并由用户审核。
- 本批源文件由此前多个不超过五文件的小批交付累计；收尾仅补帮助、截图、状态和证据。无新增依赖，不改作者文本、世界书或剧情参数，不升级旧会话。

## 本次实际验证

| 检查 | 结果 |
| --- | --- |
| `node --test experiments/ai-rpg-engine/studio/*.test.mjs experiments/ai-rpg-engine/plugins/*.test.mjs` | 178项：177通过、1个旧私有副本用例跳过、0失败 |
| 卡片 `npm run test` | 26通过 |
| 卡片 `npm run build -- --base=/rpg-app/earth/` | TypeScript及生产构建通过 |
| Vitest七文件：RpgRollingSummary、RpgPluginsPage、RpgHistoryWindow、RpgModelSelector、RpgChatState、StudioBetaPanels、helpContent | 62通过 |
| 父前端 `npm run build` | 通过，原有大chunk警告 |
| `node client/scripts/verify-help-images.mjs` | 失败：3项主线已有未引用图片；从origin/main提取的专用副本与候选stderr逐字相同，本轮图片无新增失败 |
| 正式浏览器 | 桌面及390px手机重放安装→单会话授权→选模型→保存无派发→第三回合超限压缩→原文→Esc取消→刷新；另一合成会话失败暂停、草稿保留、明确再次压缩成功 |

本次前端检查先发现文章基线元数据仍为87431327而图片为c37f1b1a；改为当前56fe2e2d并归档旧截图。随后追加教程出现9条编号步骤，超出既有5–8条规则，删除重复的续玩说明后通过。测试约束未放宽。首条命令误写两份测试扩展名/目录，最终以实际七文件完整重跑，不把早期五文件当完整检查。

独立离线服务18485/18487、独立closeout/ui-data-v1；真实Provider关闭。实际10次合成派发含4次初始种子。成功序列story,story,summary,compression,story；失败序列story,story,summary,compression,compression。手动再次压缩没有再发summary或story。后者仍只有两回合。保存/刷新/Esc不派发。外层390px无横向溢出；iframe跨文档宽度未由DOM读出，视觉可用，前批375px内框测量另保留。未穷举全部键盘路径。

日志及线级请求位于模块忽略目录 `.rpg04-work/rolling-summary-overflow/closeout/`。帮助图片原始JPEG位于docs/help-center/evidence/rpg-summary-overflow/56fe2e2d；公开PNG仅转格式/展示尺寸/调色板，不生成或重绘。旧c37f1b1a PNG移入历史证据目录。

## 真实调用与质量边界

本补充批次新授权2+1共3次全部用尽：生成10031字符合成摘要；压成1900；原1900逐字保留加本地8811字符和分隔符，10713再压成1933。三次complete/stop，无自动重试，未新增剧情或认证。正文、参数、请求/输出hash和账本保存在忽略证据目录，仓库仅提交登记和审阅记录，不上传模型原文、运行数据或凭据。本次收尾0次真实调用。

既有M2后台扩展证据也仍在原工作区：rolling-summary-b5/extension-2/ASSESSMENT.md及FULL-ASSEMBLY-AUDIT.json，记录新增6次（3剧情+3后台摘要）、累计11/11，6剧情和5摘要，11实际请求装配核对。该证据记录冲突未标注、可选建议变待办和剧情漂移，最后一组当时标为待审阅；本次不追认为全部质量通过。旧PR描述中“后台未运行”已过时，更新PR时注明后续实际运行及其局限；不改旧历史报告。

## 条件与回退

3项主线已有图片问题为b266b870/studio-beta-headings.jpg、eeb5bbd2/creator-cache.jpg、eeb5bbd2/studio-layout.jpg。本次不清理其他模块资产。PR保留Draft条件；CI仅以远端实际状态为准。

回退优先停用新版自动总结，保留完整历史、摘要版本、分支、配置、请求与额度。旧宿主降级仍须先对副本验证，不能直接指向新数据目录。本轮无共享部署、自动合并、真实额度扩张或旧会话迁移；旧18473与18483入口保留。停止新预览前必须重新核对进程归属，不能照抄PID。
