# PR 主线整合验证

用户明确授权“授权提交PR”。当前发布工作区C:/tmp/modelmirror-rpg-memory-palace-pr，分支codex/rpg-memory-palace-pr，最新主线基线08dba5f6ae5adab2043ba966c3de7fc2ffc41f49。原C:/tmp/modelmirror-rpg-memory-palace及18497真实入口、所有会话/预算/资格保持原状。

从PRE-PR-DELIVERY登记逐项验证并转入53份候选文件，没有同文件上游交集，没有复制凭据、会话或派发账本。所有client/experiments实现文件与原已验收候选逐字一致。只新增本发布记录、登记及更新当前状态/PR正文；旧报告仍描述各自历史时点。

## 本次重新执行

- node --test experiments/ai-rpg-engine/studio/*.test.mjs experiments/ai-rpg-engine/plugins/*.test.mjs：250通过，1既有私有fixture条件跳过，0失败。
- card-replica下npm.cmd test：26通过；npm.cmd run build -- --base=/rpg-app/earth/：TypeScript和Vite通过。
- client下node node_modules/vitest/vitest.mjs run --configLoader runner --maxWorkers=1 --fileParallelism=false，传入RpgMemoryPalace、RpgRollingSummary、RpgPluginsPage、RpgModelSelector、RpgHistoryWindow、RpgChatState、HelpCenterPage和components/StudioBetaPanels实际测试路径：8文件99通过。
- client下npm.cmd run build通过；既有大chunk警告。
- client下npm.cmd run verify:help-images：3项既有孤立JPG失败，精确路径同B4报告；M3两图通过。本次未更改其他资产。

日志位于本工作区experiments/ai-rpg-engine/.rpg04-work/memory-palace-pr/，不进版本控制。未宣称远端CI或全量仓库测试通过。

## 最新基线可见验证

新独立离线入口http://127.0.0.1:18501/rpg/earth，宿主18502、额度0、Provider关闭。全部使用新测试目录；用HTTP创建空虚构会话作为前置fixture，不计作角色创建UI验收。

正式内置浏览器按现有帮助回放：市场安装→会话默认未启用→明确授权→选择独立离线模型→人工新增条目→保存后受保护→离线发送→剧情落盘→后台整理至第1回合→受保护条目正文不变。正式浏览器内联截图已目视核对；新截图未归档PNG，PR内两张截图仍是明确标注的B4真实截图。界面源码逐字未变，手机和更多恢复操作沿用B4证据，未冒充本次重跑。

## 真实证据和剩余边界

原两组Gemini剧情/记忆各2份已人工确认有效。累计生成4/4、认证1/1，未新增额度或调用。当前可证明首次整理、关键词注入与条目更新；最低4组仅完成2组，早期历史退出窗口后的召回承接、M2+M3联合真实调用仍未运行。“准备做”被概括为“开始做”等偏差保留，不称无损记忆。

## 范围、回退和发布

提交只含审核源码、测试、帮助截图和脱敏文档/hash；不含node_modules、dist、.rpg04-work、.env、日志、原始会话或账本。默认RPG_MEMORY_PALACE_ENABLED关闭，新会话授权独立；作者提示词、模型参数、Studio布局及旧RPG05不改。

首选回退为停用M3，保留数据，恢复原M1/M2组合；旧宿主降级仅在副本验证。新离线预览可在核对PID和路径后单独停止，原18497和共享栈不操作。仅Commit/Push/PR获授权，Merge和共享Deploy未授权。

暂存检查补记：发布文档、PRE-PR历史登记的CRLF以及memory-http.mjs末行CRLF触发Git尾随空白检查；分两批仅规范成LF，未改逻辑或历史JSON数据。原工作区字节仍保留，历史hash不重写；当前发布登记使用规范后的文件hash。首次定位脚本默认GBK解码失败，改用UTF-8后确认范围；初始5文件防护拒绝第6份文件，未执行修改，随后单独处理历史登记。
