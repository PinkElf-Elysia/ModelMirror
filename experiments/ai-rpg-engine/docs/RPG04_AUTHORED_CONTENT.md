# RPG-04 自主主持与代表内容

## 04B2a 边界

Sol 完成预审后请求 Astra 接手局部实现；Astra 将原五文件构建器方案继续缩小为内容与测试子批。本批仅修改 context/host-template.mjs、fixtures/rpg04/authored-resources.json、tests/rpg04-content.test.mjs、本说明和 RPG04_STATUS.json 五文件。生产来源合并工具、profile/hash 绑定仍留到 04B2b，不以测试内合并替代生产工具。

自主主持文本明确玩家决定权、按 input.kind 区分输入、query 不推进、五部分回合输出、状态仅提案、不可信资料边界和未知表达。这是 authored 设计合同，不是原卡权威提示词。世界书探针仍有矛盾；用户补充的按原顺序复述再以新会话/模型/问法逐段交叉核对方式，将用于后续有界补充，未把前三次参考当成完整提示词调查。

## 内容与来源

沿用冻结 RPG-02 已验证两世界、身份、物资和天赋；原资源及来源不改。新增两个开场、两个文风、八条世界书（三条公开、一条 host 反例每世界）、一个声明式信息模块。陆禾、石豆、杂务院与河岸场景均明确为本实验自主虚构，不冒充原作角色或已提取原文。开场不自动授予物资；身份、天赋激活和玩家文本仍由玩家配置决定。

增补封装格式 modelmirror.ai-rpg.rpg04-authored-resources/0.1.0；资源数组与 defaults 的校验直接复用冻结 CARD_PACKAGE_SCHEMA 定义。合并后仍必须通过 validateCardPackage。每项新增资源只引用 source.authored-rpg04；其来源将由独立工具登记，测试合并已核对来源链。文件 UTF-8 字节 SHA-256：b395447f2cb1ce80763fb470b49b5801df110cfc8462d16e09678f9b8d4f8692。此 hash 证明本次自主内容版本，不是网站源文件 hash。

HOST_TEMPLATE 是受信代码导出的不可变 id/version/content 对象，卡片不能用来源声明替换系统指令。后继绑定约定为 canonicalJson(HOST_TEMPLATE) 的 UTF-8 SHA-256，不能与仅 content 文本 hash 混用；该生产绑定本批未实现。

## 验证与限制

模块目录实际运行 node --test tests/rpg04-content.test.mjs：8/8。连同 context-schema、context-contracts、contracts、rpg04-boundary 共68/68。首次测试命令误在仓库根执行，未发现测试文件；切换正确模块目录重跑通过，没有修改断言。完整边界门禁返回 RPG04_BOUNDARY_OK mode=complete frozen=baseline-blobs；五文件文本与敏感扫描、git diff --check 通过。

测试验证资源闭包、冻结资源不变、来源、五天赋空权限、封装拒绝未知字段、host 反例不在公开开场引用及不可变性。字符串检查只说明主持文本包含约束，不能证明实际模型遵从；host 防泄漏的运行匹配、上下文编译与真实叙事质量尚未验收。

网站请求仍3/30，Provider派发0/8；未提交或发布。RPG-04整体未完成。回退仅移除本批新文件并恢复本批状态标记，不操作旧资源或共享服务。
