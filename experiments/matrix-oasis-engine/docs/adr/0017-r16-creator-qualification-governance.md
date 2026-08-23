# ADR-0017：Creator以完整资格作为ready语义

- 状态：Accepted
- 日期：2026-08-23

## 背景

R14证明Solution可通过Godot物理复验，R15证明两类真实缓存可经实际InputMap走完可观察玩法；现有Creator仍把旧组装run直接标记为`ready`，无法复现相同保证。

## 决策

新增`matrix-oasis.creator-solved-evidence/1`资格层。Creator只在source、Solution、Verification、Replay Plan、Evidence及媒体身份全部闭合后标记`ready`。每个唯一Solution首次完整取证；后续缓存仅重新验证，不读取凭据或联网。

旧缓存保持可读但不构成成功。R16编排复用R13–R15，不修改其算法。默认入口与MVP声明只在双真实案例人工通过后切换。

## 后果

Creator首次资格会增加本地分析、求解、Godot取证时间和仓外媒体占用，但`ready`从“组装完成”升级为“实际可玩证据通过”。旧显式预览仍可回退。
