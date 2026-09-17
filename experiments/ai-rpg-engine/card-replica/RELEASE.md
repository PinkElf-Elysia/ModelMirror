# 地球 OL 本轮冻结与封装

## 已批准边界

用户于2026-09-17确认：“达到本轮门禁，可以冻结封装，收尾后提交PR”。这批准本轮成果冻结、Commit、Push与PR；不包含Merge、部署到正式服务或继续付费调用。

初始工作区基线52f78f5edb6e59b38ae952ac836c23d273caf2e6；发布目标基线aa90da03f107fc44c07c732faffc4f2374dda682。独立分支codex/rpg-card-replica快进最新main前后，已验收的53项hash一致。当前PR只新增本模块，不修改父仓源码、旧RPG合同、旧卡片、共享服务或RPG05证据。

本轮是独立React/TypeScript/Vite卡片、loopback宿主与受控调用实验的冻结；不是重启旧RPG05，不推进延期的竖切和批处理。

## 冻结内容

- 普通/深层角色创建、预览、复制、参数、离线会话、取消和历史恢复。
- 私密特征控件与角色装配字段已移除。
- 原件177份的登记、4份机械提取文本与资源覆盖表；原DOCX和截图包留在本地，不放进PR。
- runtime system只删除“【开启深层世界】当前世界为深层世界”一句。原冻结system hash13041342ea3f2cafc8dfff9140e65868dc8261fbf69b0ffd3d833f2f4b72d5b7；运行system hash4f632fff9b567183bc5fae281131320a4cf27035a0f39956b1f53ea5a09ef9ee。
- 首轮system + 角色及输入；续轮同一system + 原始历史 + 前置词/本轮输入/后置词，无重复包裹。
- 新运行max_tokens16384，其余参数不变；世界书默认关闭，不逆推触发元数据。
- 原批4/4、新授权复测1/1，累计5次Provider派发，剩余0。两次认证、三次生成，其中旧Gemini一份length截断；所有历史保留。
- 最近一次Gemini原文表世界、stop完成，用户本轮验收通过。单次接受不外推为全部场景或续轮通过。

详细原因与验证历史：WORLD_MODE_AUDIT.md、RETEST_RESULT.md、INDEPENDENT_ACCEPTANCE.md。旧报告“待审阅/无PR授权”等是该阶段事实；当前人工结论以STATUS.roundGate及本文件为准。

## 封装与可复现范围

源码封装包含本模块Git跟踪文件。排除.local、node_modules、dist、coverage、原始DOCX/截图、凭据、会话和完整模型输出。依赖锁定不升级，许可证见DEPENDENCIES.md。作者提取文本不因为本PR获得新的开源许可证，模块保持UNLICENSED。

干净源码安装：
```powershell
npm.cmd ci
npm.cmd run verify
node tools/delivery.mjs --check --source-only
npm.cmd start -- --port 18411
```

源码本身可运行离线宿主，不依赖原件目录、父仓导入或本机会话。真实调用恢复脚本依赖既有本机受控transport、冻结请求和管理员服务配置；这些不在源码包中，干净安装不会自动激活Provider。既有授权账本随源码保留为已耗尽，不能把复制包当成新授权。

resources/DELIVERY.json记录源文件及构建hash；普通--check检查当前构建，--source-only只验证封装源码，--check-git另验实际提交blob。DELIVERY不包含自身hash；外层Git提交及源码ZIP hash提供整体身份。本地封装ZIP、安装/核验日志和PR回执保存在.local/publication/，不进入Git。

## 帮助与验证

用户帮助位于USER_GUIDE.md及应用“帮助”弹窗。本轮显式只允许card-replica目录，未修改父仓client/src/content/help-center；PR的Help Center Impact声明独立模块影响和此范围边界，不写None。

检查状态以实际记录补充：源码测试/TypeScript/构建、干净安装、提交blob、秘密扫描和教程重放。没有新增Provider调用。原素材复核曾受3个Word临时文件干扰；取消首次未赶上；单句删除初次2项适配器校验失败后修正重跑——这些历史保留，不改写成从未失败。

## 保留缺口与回退

完整词库、概率、世界书元数据、169界面完整覆盖、所有长文样式、完整键盘操作、精确动效及真实续轮仍未完成。用户接受的是本轮门禁。

本轮入口保留：18411离线、18413旧GPT、18414旧Gemini截断、18415最新只读审阅。不要例行关闭它们。包装检查可用另一个自有端口与空会话目录；结束只停止经PID/命令验证的本轮包装检查实例。

源码回退可撤销本PR模块代码；不删除.local，不恢复调用额度，不重放请求。合并必须由用户审核。若CI失败，区分本PR问题、main基线问题与缺失独立依赖，不以本地通过代替CI。

## 本次封装实测记录

- 通过：原工作区npm.cmd run verify，26项测试、TypeScript、Vite生产构建。
- 通过：仅复制54个源码文件到空目录，npm.cmd ci安装104个包；npm报告0项漏洞。esbuild安装脚本提示未扩大allowScripts授权，实际构建通过。
- 通过：封装副本npm.cmd run verify，26测试、TypeScript、构建，源文件与构建hash一致。
- 通过：独立离线18416空草稿/空会话，按教程创建普通男性合成角色至命运卡，预览/复制、参数16384、离线发送1回合、刷新保留草稿、历史恢复1回合且无重发。
- 发现并修正：教程入口应为“自定义”；创建会话弹窗仍留有旧GPT4次待验收文案，已更新为本轮结束、5次额度用完。仅封装说明性文字改变，模型输入与逻辑未变。
- 截图仅浏览器内联检查，未交付独立PNG；不声称覆盖完整UI。
- 新增Provider调用：0。提交blob与远程PR/CI结果以.local/publication回执及GitHub当前状态为准。
