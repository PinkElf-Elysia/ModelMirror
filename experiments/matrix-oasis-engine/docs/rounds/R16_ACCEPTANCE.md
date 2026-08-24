# R16 Creator迁移验收记录

状态：实施中；MVP声明门保持关闭

## 固定基线

- `R16_BASE_SHA=7c837fe3908a4a5b60551778313624f53bcd0d1b`
- 分支：`codex/matrix-oasis-r16-creator-mvp`
- 版本：`0.16.0-r16`

## 批次记录

| 批次 | 本地提交 | 结果 |
|---|---|---|
| R16.1 治理与声明门 | `1500aa13` | 通过 |
| R16.2 资格合同与事务缓存 | `764a2dcc` | 通过 |
| R16.3 本地资格流水线 | `3fe31cf9` | 通过 |
| R16.4 R16宿主profile | `fe65677b` | 四级缓存、资格子阶段、失败保留与重启恢复自动验收通过 |
| R16.5 Creator与预览 | `06017679` | Creator资格摘要、精确Evidence预览和旧缓存待资格状态通过 |
| R16.6 零网络资格与泛化 | 本批提交 | 两份真实缓存与合成双空间自动门通过；等待人工验收 |
| R16.7 默认切换与收口 | 人工验收后 | 未授权 |

## R16.6零网络资格证据

- 中性缓存发布R16 qualification `60b63d9a3bd8d36592314ad6c444e8873edd189071a6f5e13664881e8f6c96ad`，绑定Solution `sha256:842617274fa6ba4efa0c9d4b01c9bae348a7e933da5bae904e893b1f36576088`与R15 Evidence `04359c15960e8904f866e0f34ea7983ed1299a86867d5f6840ca52d0ca09ad20`；强引用复验后由Creator same-origin API恢复并启动，3条重放、8张截图、1段录像、300帧中位约67.0 FPS。
- 末班地铁历史根包含多个source run，默认current不是人工通过的run。R16资格CLI新增可选精确`--source-run-id`，不改变单source默认行为；目标run重新通过冻结R14分析、求解和物理复验，得到与人工通过相同的Solution `sha256:15ea379be2c0492ee3175992a0f62a1e8381a8f31ca16a792f0b71cd0b2f199e`，并首次生成新的R15 Evidence `48e8af980a716ffc769d644746e5185ab6b561bec0fd07fe940b190911990016`与R16 qualification `fda3dc97079ec02a40f1c0e5df48897e07996b60a8e9a10d57c363d40570c572`。Creator API恢复并启动成功，9条重放、27张截图、1段录像、300帧中位约64.9 FPS。
- `preview:r16`默认仍从一个`run-root`派生五根；仅当运维者同时提供全部四个历史缓存根时才允许挂载迁移缓存，缺一、重复、非`C:\tmp`直接子目录或身份不一致均fail closed。
- 实际Creator launch首次暴露R16 mock与冻结R15 loader不一致：测试伪造了顶层`solutionSha256`，真实值位于已验证Evidence identity。修复只读取canonical Evidence identity并补真实返回形状回归；冻结R15实现未改。
- 合成双空间以一面带门洞的隔断形成连通L形路径，使用同一R14求解器和Godot物理复验器通过2个zone、3个navigation polygon及307个安全floor anchor；资产、capsule、terminal和approach验证均未放宽阈值。
- 两份真实资格与合成验证均未读取模型/Marble/Meshy凭据，供应商请求数为零；详细媒体、资格目录和日志仍只保存在仓外`C:\tmp`。

## 待人工验收

R16.6只完成自动资格与Creator API启动证明。R16.7、`preview:prototype`默认切换和MVP声明解除仍未授权；还需用户在Creator中验收中性与末班地铁的实际交互、桌面/窄屏、重启缓存和失败保留。

## 声明门

R16.7前保持`pending-creator-migration / blockingRound=R16 / claimAllowed=false`。自动绿测、旧Evidence导入或单一案例均不能替代Creator双真实案例人工验收。
