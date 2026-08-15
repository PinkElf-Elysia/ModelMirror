# R12初版闭环威胁模型

## 模型候选

- 输入仅为最大32 KiB良构UTF-8纯文本；冻结JSON、Schema片段、本机文件和密钥不进入提示。
- acceptance profile只含通用计数、类型、引用和图可达性约束；诊断静态且不回显候选值。
- 总请求上限3；耗尽即失败，不进入供应商资产阶段。

## 供应商与资产

- 模型和环境/资产分别审批；哈希或上传内容变化使批准失效。
- Marble仅允许一次world链和固定下载；Meshy最多6组preview/refine及最终GLB。禁止隐藏重试、redirect、SSRF和无限轮询。
- SPZ、panorama、collider和GLB都执行尺寸、hash、结构、identity和换身检查；远程ID、URL、prompt、凭据及原始响应不得进入bundle或诊断。

## 发布与运行

- 沿用同父staging、FileHandle/realpath/bigint身份、单次rename和current最后替换；失败保留上一份可运行结果。
- 空间尺度只接受官方metric scale和ground offset；缺失即失败。摆放使用通用walkable区域、固定槽位、全局AABB落地和避碰，不允许案例坐标。
- Godot继续无网络、无环境变量、无进程执行和无写入；panorama/Raster不得作为成功空间环境回退。

## 声明门

- `MATRIX_OASIS_R12_MVP_READY`仅在完整真实资格和人工验收后允许进入通过记录。
- `check:mvp-claim`阻止R12完成前的初版完成叙事；文档通过不能替代自动、真实供应商或人工证据。
