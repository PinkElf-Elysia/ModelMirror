# 独立依赖登记（安装前）

| 依赖 | 版本 | 许可 | 用途 |
|---|---|---|---|
| React / React DOM | 19.2.7 | MIT | 独立界面 |
| react-markdown | 10.1.0 | MIT | Markdown 展示 |
| parse5 | 8.0.1 | MIT | 模型 HTML 白名单解析，不执行源码 |
| Vite | 7.3.5 | MIT | 独立静态构建 |
| TypeScript | 5.8.3 | Apache-2.0 | 类型检查 |
| @types/react | 19.2.17 | MIT | 类型定义 |
| @types/react-dom | 19.2.3 | MIT | 类型定义 |
| esbuild (Vite override) | 0.28.1 | MIT | 构建工具 |

沿用已登记版本；本目录独立 package/lock/node_modules，不导入旧模块或父仓源码。传递依赖及 integrity 由独立 lock 登记。回退移除本目录依赖即可。
作者资源授权依据：用户确认书面授权并允许完整界面复刻。原件保留本地；用户现已授权本轮封装并提交PR；公开提交包含提取文本及本地复刻实现，不包含原始DOCX或截图包。这不替作者资源声明新的开源许可，模块保持UNLICENSED。

真实调用恢复工具使用既有本机受控transport和Python环境；这些忽略目录内的副本及凭据不随源码封装。干净安装只保证离线宿主及测试/构建可运行，不自动激活真实Provider。
