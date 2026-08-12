# R9 Prototype Asset 威胁模型

## 资产与信任边界

R8 输出、Meshy 响应、下载 URL 和 GLB 都是不可信输入。供应商成功状态不等于内容安全；Receipt、SHA-256和canonical manifest只证明本地完整性，不证明来源真实性或版权。

## 必须失败关闭

- 路径越界、symlink/junction、读写换身、已有目标或并发同名发布；
- redirect、非HTTPS、非官方下载host、SSRF、超时、429、畸形或超限响应；
- GLB外部URI、脚本行为、动画、skin、camera、light、required/未知执行扩展；
- 文件/总量、节点、mesh、surface、三角、纹理和bounds超限；
- Blueprint/Runtime身份不匹配、brief缺失/重复或输出引用不完整；
- 任意密钥、任务ID、下载URL、原始响应、绝对路径或底层异常进入公开诊断/报告。

普通verify只使用loopback和离线夹具。真实Meshy操作必须逐任务、逐阶段取得人工批准。Marble保持完全禁用。

## 原子发布

所有brief成功并经离线复验后，才在同父临时目录生成固定文件，通过身份绑定的FileHandle回读并单次目录rename发布。任何失败都不得替换旧bundle或留下可被误认为成功的半成品。
