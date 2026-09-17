# Studio Beta 控制面板入口

日期：2026-09-17。基线：`b266b870` 加本补充提交。主分支 `a7d99925`，发布前刷新后无新增分歧。

## 用户操作与实际结果

1. 打开 `/studio`，RPG 卡片下方显示 Science、矩阵绿洲两个 Beta 卡片。
2. 点击 Science 的“打开控制面板”：新标签页进入现有 A7 Research Console，实际可见“继续你的文献研究”、研究项目、工程夹具、系统；页面仍提示部分文献能力“需要配置”。没有创建研究或调用 Provider。
3. 点击矩阵绿洲的“打开控制面板”：目标为独立 Creator Host，而非 `/matrix-oasis` 预告。现场默认端口 43110 未监听，连接被拒绝；浏览器工具拒绝读取其 data: 错误页，未绕过。**Creator 内容与生成能力未验收**。
4. 返回 Studio，点击矩阵绿洲“入口说明”：实际进入 `/help/modules/experimental/matrix-oasis`，说明独立服务须先启动；“打开当前产品入口”返回 Studio。Science 说明位于同级 science 页面。
5. “查看预告”保留原 `/matrix-oasis` 路由，与控制面板分开。
6. 390×844 视口验证：两个卡片纵向排列，DOM clientWidth=scrollWidth=380，无横向溢出。随后恢复桌面视口。

![Studio 中 Science 与矩阵绿洲的实际 Beta 标识（标题区域裁剪）](../../../client/public/help-center/b266b870/studio-beta-headings.jpg)

截图由正式浏览器工具采集，981×65 JPEG，仅标题区域，不是完整面板截图。SHA-256：`705e85290319bcd148bfeebe09eca1b22d339e2b90f7ea1cf592e132c0a2daf7`。通过本任务工具原始结果提取字节，并校验一致；没有重绘或图像生成。

## 入口配置与运行边界

- 客户端服务公开运行配置：`SCIENCE_CONSOLE_URL`、`MATRIX_OASIS_CONSOLE_URL`；由 `/runtime-config.json` 返回，仅允许 HTTP(S)，拒绝用户名、密码、查询参数及片段。
- 本地回退地址分别为 `http://127.0.0.1:8900/`（现有 A7）及 `http://127.0.0.1:43110/`（Creator 默认端口）。AI Research 标准 compose 默认 8790 不等于此次已运行的 A7 面板；其他部署应显式配置其真实地址。
- 管理员在 **client 容器**环境中设置这两个公开 URL 并重建该容器即可覆盖；没有新增密钥或读取后端凭据。远程访问时需配置浏览器实际可达的地址，loopback 仅指浏览器本机。
- 入口使用新标签页，保留独立服务的 Host/Origin、会话、鉴权及运行资格边界；无 iframe 代理、后台探针或自动启动。
- 矩阵绿洲须先按独立模块 README 启动 Creator Host；本补充范围为前端入口，未修改其运行环境、Godot、模型资格或已有证据。
- Beta 不代表完整科学研究资格或 V2 世界体验通过。本轮 Provider 调用为 0；RPG 历史预算不变。

## 验证与部署

- 受影响 Vitest：3 文件 54 项通过；帮助元数据更新后 14 项复核通过。
- `npm.cmd run build` 与前端 Docker 镜像构建通过；保留原有 bundle size 警告。
- 共享 Studio 的两个 Beta 入口已可见。现有部署脚本在内存中保留配置，检查 server/client 环境变更键为空、挂载与网络一致；server 镜像未改变。
- 初次测试脚本因工作目录错误未写入文件，纠正后重跑；不将先前仅 51 项结果当作新入口测试。
- 本轮不执行科学研究、世界生成或 RPG Provider 调用。矩阵绿洲服务未启动是明确运行限制，不计为控制台验收通过。

## 回退

代码回退只撤回本补充提交；保留上一提交的双 RPG 集成。前端镜像回退标签 `modelmirror-client:pre-beta-20260917`。沿现有部署配置只恢复 client 镜像，保留环境、挂载、网络与所有独立模块数据；不停止或清理研究、RPG 服务。

## PR CI 边界

本补充发布前，上一提交 `b266b870` 的 Quality run `35216327734` 已失败：前端 2 failed / 1003 passed（ModelCard 与 tokenPricing 的时段定价）；后端 7 failed / 6752 passed / 32 skipped（音频目录、旧 RPG05 structured compiler）。Windows Project Host 通过。这是上一提交的实际 CI 结果，不是已证实的 main 基线归因；本补充未修改这些路径，未将局部验证替代整条 PR 的 CI。
