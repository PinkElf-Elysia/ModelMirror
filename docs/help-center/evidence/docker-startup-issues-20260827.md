# Docker 全栈启动帮助中心：问题记录与当前状态

- 记录日期：`2026-08-27`
- 仓库：`E:\ModelMirror\ModelMirror-new`（基线 `58f1bcd3`，PR #309）
- 目标：用 `docker compose up -d --build` 启动完整栈（server + client + browser + sandbox + mcp-*），访问 `http://localhost:5173/help`。
- 当前结论：**Docker 全栈未能启动成功**，卡在 `mcp-files` 容器构建；帮助中心可用**前端 dev server 方式**先行查看。

---

## 一、已成功的部分

| 项 | 结果 |
| --- | --- |
| Docker 本体 | 运行正常（Docker Desktop 29.6.2，引擎可达） |
| 阿里云 server 基础镜像拉取 | ✅ `registry.cn-hangzhou.aliyuncs.com/metad/xpert-api:latest` 已拉取成功（首次 EOF/403，重试后成功） |
| 部分容器构建缓存 | ✅ `client`、`mcp-token`、`mcp-database`、`mcp-saas`、`mcp-registry`、`mcp-hub-egress` 等早期步骤已产出缓存（apt-get / npm ci / pip install 部分命中缓存） |
| 前端依赖 | ✅ `client/node_modules` 已就绪（294 项），node v24.16.0 / npm 11.13.0 |
| 帮助中心内容源 | ✅ 全在前端内置（`client/src/content/help-center/`），不依赖后端即可查看 `/help` |

---

## 二、遇到的问题（按时间顺序）

### 1. 阿里云镜像拉取中断（EOF / 403，`aliregistry.oss-cn-hangzhou`）

- 症状：`server` 构建时拉 `registry.cn-hangzhou.aliyuncs.com/metad/xpert-api` 中途断连：
  `failed to copy: ... aliregistry.oss-cn-hangzhou.aliyuncs.com:80: connectex: failed to respond`。
- 结论：**网络瞬断**。`ping registry.cn-hangzhou.aliyuncs.com` 通、`curl -I /v2/` 返回 401（正常未认证响应）。
- 解决：`docker pull` 重试后成功。

### 2. 容器内 DNS 解析大面积失败

- 症状（多容器、多轮）：
  - `Could not resolve 'deb.debian.org'` → `E: Unable to locate package git/curl/ripgrep`
  - `registry.npmjs.org` 返回 `ENOTFOUND` / `ETIMEDOUT`
  - `mcr.microsoft.com` 返回 `failed to resolve source metadata`
- 根因：**本机代理软件（Steam++）把国外域名 DNS 劫持到 `127.0.0.1`**，Docker 容器解析这些域名全部指向本机 → 下载失败。并非 Docker 或网络本身问题。
- 尝试：给 Docker Engine 配置 `"dns": ["114.114.114.114","223.5.5.5","8.8.8.8"]` —— **无效**（因为劫持在 hosts/DNS 上层，不是 DNS 服务器选择问题）。

### 3. Steam++ 代理对 Docker 无效（CONNECT tunnel failed）

- 定位：`ping github.com` 返回 `127.0.0.1`；`netstat` 确认 `Steam++.Accelerator.exe`（PID 36440）监听 `0.0.0.0:80/443`。
- 尝试：Docker Engine 配置 `"proxies": { "http-proxy": "http://127.0.0.1:443", ... }`，让 Docker 走 Steam++。
- 结果：**失败**。`curl -x http://127.0.0.1:443 ...` 返回 `CONNECT tunnel failed, response 302` —— Steam++ 对 GitHub release 大文件下载不建立隧道，直接 302。
- 结论：**Steam++ 无法作为 Docker 访问国外源的代理**。

### 4. 退出 Steam++ 后宿主机恢复正常，但构建仍卡 `mcp-files`

- 操作：退出 Steam++ → `ipconfig /flushdns` → `ping github.com` 恢复真实 IP `20.205.243.166` 且连通。
- Docker Engine 移除 `"proxies"`，保留 registry-mirrors + dns，重启后重建。
- 结果：构建推进到 `mcp-files`，但卡在下载 gograph：
  `curl --fail --location ... https://github.com/ozgurcd/gograph/releases/download/v1.5.6/gograph_Linux_x86_64.tar.gz` → **exit code 22**。

### 5. 宿主机直接下载 gograph 返回 404（当前关键问题）

- 用宿主 curl 下载同一地址：**`curl: (22) The requested URL returned error: 404`**。
- 含义：**不是网络、不是 Docker、不是代理、不是登录问题** —— 是**下载地址本身返回 404**，即 `Dockerfile` 里引用的 gograph release 资产地址已失效（可能是版本号 `v1.5.6` 不对，或文件名从 `x86_64` 改名，或上游项目改版）。
- 定位：这是**项目代码层面**需要修复的点（`server/` 相关 Dockerfile 中的 gograph 下载 URL），需确认实际 release 版本后修改。
- 待办：用 `curl -s https://api.github.com/repos/ozgurcd/gograph/releases/latest | findstr tag_name` 查真实版本，修 Dockerfile。

---

## 三、当前环境事实（已确认）

| 项 | 值 |
| --- | --- |
| 网络 | 退出 Steam++ 后 `github.com`、阿里云均可直连 |
| 代理软件 | Steam++（Watt Toolkit），对 Docker 无有效代理作用 |
| Docker 镜像加速器 | `docker.m.daocloud.io` / `docker.nju.edu.cn` / `docker.mirrors.ustc.edu.cn`（仅覆盖 Docker Hub，不覆盖阿里云/微软 mcr） |
| Docker Engine 自定义项 | `dns`（国内三 DNS）、`registry-mirrors`（三加速器） |
| node / npm | v24.16.0 / 11.13.0 |
| 前端依赖 | 已安装 |

---

## 四、当前可用的替代启动方式（帮助中心）

**前端 dev server（推荐，立即可用）**：

```cmd
cd E:\ModelMirror\ModelMirror-new\client
npm run dev
```

- 访问：`http://localhost:5173/help`
- 说明：帮助中心正文、目录、搜索全在前端内置（`client/src/content/help-center/index.ts`），**不需要后端即可完整查看 `/help`**，适合排查阅读/导航/搜索/移动端等纯前端体验。
- 限制：模型市场、聊天、RAG、MCP 等真实交互依赖后端 API（`vite.config.ts` 将 `/api` 代理到 `http://localhost:8000`），前端单独启动时这些页面会缺数据。真实操作门禁仍需 Docker 栈或后端直跑。

**后端（未验证，依赖多）**：`server/main.py` 存在，但直接 `python` 启动依赖 MCP sidecar、数据库、密钥环境变量等，暂不作为首选。

---

## 五、Docker 全栈修复建议（后续）

1. **修复 gograph 下载 404**（阻塞项）：查 `https://api.github.com/repos/ozgurcd/gograph/releases/latest` 的真实 tag 与资产名，修正 `server/` 对应 Dockerfile 中的 URL（含 SHA256 校验值同步更新）。
2. **确认无需 Steam++ 时能否直连**：Docker 直连（当前配置）下，`deb.debian.org`、`mcr.microsoft.com`、`github.com` 均可能受网络影响；若重试仍不稳定，需真正可用的 HTTP 代理（非 Steam++）或调整镜像。
3. **修复后重跑**：`docker compose up -d --build`，失败点会因缓存继续推进。

---

## 六、结论

- Docker 全栈启动**尚未成功**，阻塞点为项目 Dockerfile 中 gograph 下载地址 404（项目代码问题），叠加此前本机 Steam++ 代理干扰（已排除）。
- 帮助中心**可立即用前端 `npm run dev` 启动查看**，满足纯前端体验排查。
- 真实操作门禁（隔离预览、实操重放、截图）仍需 Docker 全栈或后端就绪后开展。
