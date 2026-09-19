# 模型选择插件 PR 交付

用户明确「授权提交pr」，本次允许本轮文件提交、推送并创建PR；未授权合并、共享部署或新增Provider调用。工作区C:/tmp/modelmirror-rpg-model-selector，分支codex/rpg-model-selector，目标main。刷新origin后最新基线仍为1864ed8446346df1b77eb3da53f94d9e56b7c53f，开工ahead0/behind0，无交集或重放已验收代码的需要。

B0-B6为多个≤5文件的独立批次，最终合并为一个可审阅功能提交：双插件权限隔离、专用受控桥接、按回合保存模型选择及分支继承、已批准极简UI、帮助与分批证据。数量超过5的是累计交付，不是一次实现批次。B6人工验收/源码冻结保持历史；当前发布新增记录见PR-DELIVERY.json。

发布前使用相同最新基线的独立工作区与全新18450/18451合成预览、空浏览器origin和独立数据，按帮助步骤完整重放安装/授权/搜索/草稿/选择/假发送/停用。实际证据见docs/help-center/evidence/rpg-model-selector-1864ed84.md。本次不调用真实Provider；B5已有3次认证+3份输出且最终用户通过，额度均耗尽。

仅发布源码、测试、正式帮助、原型/截图与脱敏工程记录。原件、node_modules、dist、.rpg04-work、服务配置、凭据、原始对话请求/输出、账本与容器数据不纳入Git。工程报告只引用本地证据hash与路径，不提交其正文。作者prompt源码沿用基线不改。

验证：B5Node110/110、bridge27/27；B4前端77/77及地球卡构建；B6帮助54/54及client构建，均保留具体批次而非声称PR前全部重跑。PR前diff和源码/构建/调用证据hash检查通过。帮助图片命令实际失败3项已证明基线未引用JPEG，新图片通过；全仓测试与远端CI未获通过结论。新PR不得据此自动合并。

运行边界与回退见B6.md：共享栈未部署，隔离控制面资格仅内存，持续部署及回退仍需另外核验；停用插件保留核心模型选择、会话、分支和预算，不删除占位重发。原人工入口18449保留。本文记录发布前状态，实际commit/PR URL以Git/GitHub及本地publication-receipt.json为准。

提交前暂存检查补充：core.autocrlf=false，新文件保留Windows CRLF，直接git diff --cached --check把CR报告为尾随空白；使用git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --cached --check通过。保留空白检测并正确识别CRLF，不重写已验收字节及运行绑定。84个暂存路径无运行数据/凭据文件；已知凭据前缀扫描无命中，不代表所有秘密格式均可检测。
