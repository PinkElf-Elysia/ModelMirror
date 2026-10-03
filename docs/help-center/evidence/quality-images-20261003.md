# 历史截图格式修复记录

本次仅在独立质量修复分支处理历史截图格式，不是新功能验收，不更新原截图基线或真实模型验证日期。

## 原件与公开副本

原件逐字节保存在 `screenshots/quality-20261003/0de311ec/`。文件名沿用历史 `.png`，原始编码实际为 JPEG。

| 文件 | 原件 SHA-256 | 750px 真彩 PNG SHA-256 |
| --- | --- | --- |
| openrouter-image-model.png | 5B8453ED2E8248D0A31EB63ED36FC0712E6E7134425C5FBF0DD026F6410AB4C3 | 8DA4B37DD5CCBF4CAC714A752E9E530ABD215EED21F5D4973623B7025F2072E7 |
| openrouter-specialized-models.png | 44A2B596CF89659DC0F2CA9296878D6C323DAB32C38A229E5F2A7368341C5DAD | 6A7E8BC115CB56B1A09BE277F4C91F7C5603737E26E490ADFA8B7C74194AC72E |

首轮使用 System.Drawing 等比缩小，PNG 重编码；没有 AI 重绘、裁剪或替换文字。首轮副本分别为 318784 和 592173 字节，超过 250KB 门禁。

首轮 WPF 索引色编码出现明显噪点，未采用。用户后续授权压缩后，从归档原件重新等比缩至 900px，使用 Pillow 11.3.0、LANCZOS、MEDIANCUT 256 色、无抖动和 PNG optimize。存在颜色量化，不是无损压缩；没有裁剪、补画或替换文字。两张最终副本已逐张查看，文字与页面信息仍可辨认，原件不变。

| 最终公开副本 | 尺寸 | 字节数 | SHA-256 |
| --- | --- | --- | --- |
| openrouter-image-model.png | 900×989 | 134526 | A2CE7CCF389D079919FB51A7F4A5DF620F288F4C4AEC48C22B2083FEBAED09E0 |
| openrouter-specialized-models.png | 900×1478 | 237113 | F54CB561BDC749B04A94BEFF1B160680A674B7FADE7BFC78BCBC74A3E1464E72 |

`npm.cmd run verify:help-images`：23 篇已注册文章全部通过，退出码 0。此结果仅证明资产门禁，不替代完整浏览器验收或其他质量门禁。

## 已确认归档

用户确认后，以下文件从 `client/public/help-center/` 原样移至 `screenshots/quality-20261003/`，逐项校验归档前后 SHA-256 一致。三个证据文档中的截图引用已同步更新；历史报告中的旧路径及当时失败结论未改写。

| 相对路径 | SHA-256 |
| --- | --- |
| 6413a273/openrouter-image-model.png | 5B8453ED2E8248D0A31EB63ED36FC0712E6E7134425C5FBF0DD026F6410AB4C3 |
| 6413a273/openrouter-specialized-models.png | 44A2B596CF89659DC0F2CA9296878D6C323DAB32C38A229E5F2A7368341C5DAD |
| b266b870/studio-beta-headings.jpg | 705E85290319BCD148BFEEBE09ECA1B22D339E2B90F7EA1CF592E132C0A2DAF7 |
| eeb5bbd2/creator-cache.jpg | FCD4F31BC47BCFD96D509FA3490479D56BC83016FD7B6BD294B96C923CA1530C |
| eeb5bbd2/studio-layout.jpg | D56A06347AF9A1F9B9512F83B30AF4A813671FCBB7DC2EE68A68CF18EA9DAB89 |
