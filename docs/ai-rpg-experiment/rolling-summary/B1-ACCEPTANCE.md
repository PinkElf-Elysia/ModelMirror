# M2 B1 策略与版本验收候选

B0已获用户确认。B1离线候选完成，等待本批确认后进入B2。基线87431327f3ef27843caa7ebe3115454e06d8ffbe；分支codex/rpg-rolling-summary。未Commit、Push、PR或共享部署，真实派发0次。

## 已实现

- 受信插件rpg.rolling-summary@1.0.0：安装和会话授权分离，版本/hash/权限绑定，停用与卸载撤权。旧宿主默认目录仍为3项；只有显式M2宿主选项才列出第4项，避免提前暴露半成品。
- 纯覆盖计算：T≤1不需摘要；摘要目标1…T−1，只保留第T完整回合。来源是原始user/assistant副本，保留卡内记忆，无人物字段硬编码。
- format4独立运行绑定：包含原作者/模型绑定、M1基础、摘要指令、包装及新增源码hash。B0批准的摘要指令逐字匹配；不迁移旧会话。全局contextPolicy明确只是默认描述，逐会话返回实际mode与ready/reason。
- 摘要版本链保留覆盖范围、原历史hash、前一版本、模型/参数、请求与输出hash、模型原文及有效文本。人工编辑追加版本，同覆盖历史修订可恢复；较旧覆盖终点不能冒充当前覆盖。
- 固定10000 Unicode码点上限，空白、截断、超限及不匹配回执不能形成有效版本。失败输出的任务证据存储由B2完成，本批不声称已捕获Provider原文。
- 独立摘要模型配置仅从受控目录的available项解析，模型参数沿用现有控制面，包括Luna例外。未选择模型时状态明确不可就绪；B1没有UI授权流程或实际目录资格认证。
- 配置与修订复用会话锁和宿主提交队列。先保存意图，再原子发布；失败保留旧数据和待核对操作，同ID恢复，不换ID重放。撤权再启用也不能复活原操作。
- 有效策略M2 > 授权M1 > 完整历史。M2停用/卸载不清除M1设置；未确认保存即使停用M2也不能报告可发送。

## 文件与小组

第一组5文件：plugins/rolling-summary.mjs、rolling-summary.manifest.json、rolling-summary.test.mjs、catalog.mjs、host.mjs。
第二组4文件：studio/summary-state.mjs、summary-runtime.mjs、summary-session-store.mjs、summary-store.test.mjs。
文档/证据登记单独收尾；没有修改旧RPG合同、作者资源、旧测试、Studio布局或HTTP入口。

## 实际验证

Node v24.18.0。回归命令为node --test，精确文件清单保存在本工作区 .rpg04-work/rolling-summary-b1/regression.json；范围为plugins/*.test.mjs、studio/*.test.mjs及card-replica/tests/*.test.mjs。

149项：148通过、0失败、1跳过；其中16项为本轮新增。跳过项是旧model-legacy-copy.test.mjs依赖的RPG_B3_LEGACY_COPY真实历史副本，本轮未提供该私有fixture，不推断为通过。

本批覆盖首次及两次滚动版本、人工编辑/恢复、10000个非BMP字符、缺口与未来覆盖拒绝、原文不变、M1恢复、版本/权限不匹配、并发、撤权再启用、失败落盘、丢失响应及重启恢复。摘要均为合成数据，版本模型回执为合成回执，不是模型质量证据。

历史失败保留：新增授权守卫使一条本轮断言仍期待旧错误码，修正为更早的SUMMARY_AUTHORIZATION_CHANGED后同组22项通过；首次全回归缺ajv导致6个测试文件无法加载，按原锁文件离线补依赖后重跑同套通过；初次日志终端输出遇GBK编码错误，日志已保存，后续使用UTF-8。没有以删测试或改旧常量获取通过。

## 未接通与下一批边界

SummarySessionStore未挂HTTP服务。format4的send、剧情模型切换、分支和请求装配入口明确拒绝，避免落入旧宿主路径；不会从本批测试自动生成故事。模型派发/预算/双时机调度/任务取消与结果恢复属于B2；真实请求和分支冻结属于B3。前端/生产构建属于B4，本批未运行。B5额度仍未授权。

## 回退

当前生产入口未切换，无需部署回退。可撤回本轮源码扩展但保留测试证据和B0原型；不处理主工作区、M1验收服务或用户会话。将来停用M2优先，版本和历史保留。旧宿主读取含M2注册数据的兼容性须在副本验证，不能把本批旧会话只读测试称为完整降级验收。
