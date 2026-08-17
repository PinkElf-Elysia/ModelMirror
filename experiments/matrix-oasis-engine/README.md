# 矩阵绿洲独立实验模块

这是模镜仓库中可拆分的AI原生3D引擎实验模块。R1–R11已建立严格Pack、确定性Runtime、真实资产、Marble空间环境和Godot可玩层；R12负责最后的初版声明硬门。

## R12目标

```text
纯自然语言 → Generation Proposal → Authoring/Runtime
→ Marble空间环境 + 3名静态人物 + 3件关键道具
→ Scene/Spatial组装 → Godot三结局、循环与重置
```

- 固定基线：`6a88c648f3db2afc39574a57066a14c341c161f9`。
- 冻结末班地铁JSON仅作语义oracle，生成提示不包含JSON、冻结ID或Schema片段。
- 既有中性真实缓存必须通过同一R12链路，源码不得加入题材分支。
- 普通验证不联网、不读取密钥、不产生费用；真实资格调用必须分模型和环境/资产两次取得当次批准。
- R12自动、真实资格与人工验收全部通过前，不得宣称“自然语言到可玩3D初版闭环完成”。

当前状态由`docs/MVP_STATUS.json`和`npm.cmd run check:mvp-claim`机器化约束。完整回归仍使用`npm.cmd run verify`。
