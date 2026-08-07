# R1 验收夹具

本目录只保存 Authoring Game Pack 0.1.0 的测试与未来只读可视化输入，不保存成品剧情或资产。

- `mechanics-conformance.authoring-game-pack.json` 是题材中性的机制权威夹具，覆盖 R1 冻结的变量、condition、effect、Cue 与 typed target 词汇。
- `last-train-r1.authoring-game-pack.json` 是“末班地铁：回声十三站”薄型集成夹具，只验证同一通用合同能承载可理解的结构化叙事图。

验证器、Schema 和公共诊断不得出现地铁、角色、记忆或调查专用字段。更换第二个文件的题材不应修改任何核心包；若样例润色与引擎交付发生冲突，以合同、验证器、可拆分性和确定性门禁为先。

从模块根运行：

```powershell
npm.cmd run validate:examples
npm.cmd run test:examples
```

R1 不执行这些 Pack，不提供编辑器、Runtime Pack、3D、音频或 Godot 绑定。
