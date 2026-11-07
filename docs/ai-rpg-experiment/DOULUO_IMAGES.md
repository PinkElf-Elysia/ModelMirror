# 斗罗配图资源核对

核对日期：2026-11-07。本轮只改展示投影，不修改作者提示词、模型回复或存档，不调用模型。

## 已验证结果

| 来源 | 数量 | 当前原站状态 | 本地资源 | 聊天展示 |
| --- | ---: | --- | --- | --- |
| 原始开局 HTML / assets.json | 46 | 逐一 HEAD，均 HTTP 200 | 46/46 SHA-256 与作者资源清单一致 | 精确 URL 映射至同源原图，支持 HTML 与 Markdown |
| 原始提示词吐槽头像清单 | 12 | 逐一 GET，均 HTTP 404 | 仓库未找到对应缓存记录 | 显示失效原因，保留吐槽开关 |

46 张原图的完整地址、文件及哈希位于 `experiments/ai-rpg-engine/douluo-card/resources/assets.json` 的 image 条目。运行时不访问远程图片，也不猜测旧地址与其他角色图片的对应关系。已有开局资源无需重复复制。非清单地址仍显示“配图未收录”；其他卡片过滤范围不变。

## 失效原图清单

以下均为作者提示词中给定的精确路径，HTTP 404 为本次观测结果，不能证明未来永远不可恢复。机器可读记录见 `douluo-card/resources/unavailable-images.json`。

| 图片代号 | 原地址 | 状态 |
| --- | --- | --- |
| `0380009923dd4c8b52a03ec865fe5fb1` | [原图](https://img.wjwj.top/2025/06/16/0380009923dd4c8b52a03ec865fe5fb1.png) | HTTP 404 |
| `1385dfa721288c0438be79472cb48429` | [原图](https://img.wjwj.top/2025/06/16/1385dfa721288c0438be79472cb48429.png) | HTTP 404 |
| `7f77f7f0f74e4b8bdcca858e183e4e23` | [原图](https://img.wjwj.top/2025/06/16/7f77f7f0f74e4b8bdcca858e183e4e23.png) | HTTP 404 |
| `44864f54970ec18c2a531079a8410928` | [原图](https://img.wjwj.top/2025/06/16/44864f54970ec18c2a531079a8410928.png) | HTTP 404 |
| `d9f7067e7783abfe6c030433cb1a79f9` | [原图](https://img.wjwj.top/2025/06/16/d9f7067e7783abfe6c030433cb1a79f9.png) | HTTP 404 |
| `b192e0ac7b0f32e9db6753e779b1da38` | [原图](https://img.wjwj.top/2025/06/16/b192e0ac7b0f32e9db6753e779b1da38.png) | HTTP 404 |
| `07678d12468208f444d3d8a7f9d02f92` | [原图](https://img.wjwj.top/2025/06/16/07678d12468208f444d3d8a7f9d02f92.png) | HTTP 404 |
| `ebd05cd5cf971215b43f9f32edec641e` | [原图](https://img.wjwj.top/2025/06/16/ebd05cd5cf971215b43f9f32edec641e.png) | HTTP 404 |
| `5d7df8bab2c1c8d1ab263f4e634c584e` | [原图](https://img.wjwj.top/2025/06/16/5d7df8bab2c1c8d1ab263f4e634c584e.png) | HTTP 404 |
| `5908d2b94efd129d20e3d72170df0cd3` | [原图](https://img.wjwj.top/2025/06/16/5908d2b94efd129d20e3d72170df0cd3.png) | HTTP 404 |
| `6b4afc6949ff89e66b29d31d2a4f4ab5` | [原图](https://img.wjwj.top/2025/06/16/6b4afc6949ff89e66b29d31d2a4f4ab5.png) | HTTP 404 |
| `f554cd457a8fc597ab439f7ddec8d7fb` | [原图](https://img.wjwj.top/2025/06/16/f554cd457a8fc597ab439f7ddec8d7fb.png) | HTTP 404 |

## 验收及边界

- `npm.cmd test --prefix card-replica`：75/75 通过，含 46 张原图文件哈希、精确映射、12 个提示词 ID 完整性、恶意/未知 URL 拒绝、部分流式图片标签验证。
- `npm.cmd run build --prefix card-replica`：通过。
- 浏览器：桌面 1440px、移动端 390px，46/46 图片解码成功，无外部图片请求；生产 ChatView + Worker 对已有完整回复进行只读 API 回放，失效原因和吐槽切换正常，零写请求。
- 原测试回复未修改，引用的头像 `0380009923dd4c8b52a03ec865fe5fb1` 本次返回 404，因此该回复不能展示真实头像。图库是独立资源检查，不能当作已恢复此头像的证据。
- 本地证据：`.rpg04-work/douluo-image-browser-result.json`、`douluo-image-probe.json`、`douluo-opening-image-probe.json` 及对应截图。真实回复不纳入版本控制。
- 共享栈未部署。恢复这 12 个头像还需要有效原图或可核实的新地址；不以其他图片自动替换。

回退只需撤回本轮展示映射及失效清单引用，不涉及数据迁移。原资源、原回复和存档保持不变。

## 统一托管与停用策略

统一展示清单位于 `card-replica/resources/image-library.json`：63 张本地图（上帝模拟 17 张、斗罗 46 张），记录来源、卡片、用途、本地文件、字节数、SHA-256 和启用状态；另外记录斗罗 12 个无本地副本的停用项。`lib/image-library.mjs` 为两张卡提供精确映射及按用途过滤的展示候选。斗罗吐槽头像候选为空；保留文字吐槽，不使用开局肖像替代。

现有原图继续存放在各卡已受控的公开目录，不重复复制；原图网站下线不影响本地副本。原始作者提示词、来源清单和旧回复不改写；即使旧回复含随机失效地址，展示层也不加载它。失效吐槽占位改为“随机头像已停用”。

新增资源须先保存获授权的原图、核对类型和 SHA-256，再登记精确 URL、本地路径及用途并运行 `node --test card-replica/tests/image-library.test.mjs`。不能把任意远程 URL 当作备用图片地址；无法取得原图时登记停用。此批未新增图片上传、删除接口或管理后台。
