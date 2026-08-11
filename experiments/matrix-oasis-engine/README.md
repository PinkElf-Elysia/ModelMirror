# 矩阵绿洲 AI 原生 3D 游戏引擎（实验模块）

这是模镜仓库中可拆分的独立实验模块。R1–R7 已建立严格数据合同、Compiler、确定性 Runtime、Godot 可玩层和离线 Scene Pack；R8 只增加纯文本原型生成入口。

## R8 当前目标

```text
纯自然语言
→ Generation Proposal
→ Authoring Game Pack + 私有 Scene Blueprint
→ Runtime Pack + Receipt
→ Runtime 初始会话验证
```

- 固定基线：`21cbbb8b943b6f9d9799f014c44a6349e6124a63`。
- 模块版本：`0.8.0-r8`，private/UNLICENSED。
- R1–R7、Creator、Godot、examples 和第三方资产全部冻结。
- R8 不生成 3D 资产、不启动 Godot、不调用 Marble/Meshy。
- 真实模型资格验证不进入普通 `verify`，必须逐次人工批准。

R8.4 已提供：

- `npm.cmd run plan:prototype-call -- --prompt-file <C:\tmp文件>`：只读检查上传范围并显示主机、模型、请求上限和字节数，不发出请求；
- `npm.cmd run generate:prototype -- --prompt-file <C:\tmp文件> --output <C:\tmp新目录> --acknowledge-external-upload`：最多三次模型请求，严格验证后事务发布五个 canonical JSON；
- `npm.cmd run verify:prototype-generation`：只使用 loopback 假 Provider，零外部费用。

完整自动验证继续使用 `npm.cmd run verify`。真实资格命令与证据留给 R8.5；任何父仓修改、共享栈操作或真实供应商调用都需要单独授权。
