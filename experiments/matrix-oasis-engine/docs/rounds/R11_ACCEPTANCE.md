# R11验收记录

状态：R11.1已验证，等待本地提交

固定基线：`da2a914a2ff131507750a0afb8d8881180530f62`

## 批次

- [x] R11.1 治理与空间环境边界（本批提交；SHA在R11.2记录）
- [ ] R11.2 SPZ转换与Spatial Environment合同
- [ ] R11.3 Godot gdgs Compute渲染
- [ ] R11.4 米制空间自动组装
- [ ] R11.5 一键空间预览
- [ ] R11.6 standalone、性能与人工验收收口

## R11.1证据

- `origin/main@da2a914a2ff131507750a0afb8d8881180530f62`包含PR #184合并提交；从该BASE创建独立分支`codex/matrix-oasis-r11-spatial-environment`和worktree。本批16个模块内路径，父仓零差异。
- schema v11、active R11、固定BASE、新workspace/Godot/gdgs前缀和精确文件allowlist已同步；R1–R10验收、ADR、packages、examples、Creator/Godot既有实现继续fail-closed。
- 定向`node --test tests/round-scope.test.mjs tests/boundary.test.mjs`为151/151；`check:round-scope`、`check:parent-scope`、`check:boundary`均通过，分别checked=16、16和962/tracked=958；`git diff --check`通过。
- `npm.cmd ci --offline --ignore-scripts --no-audit --no-fund`安装112个锁定包；`npm.cmd prefix`、`npm.cmd ls --all --depth=0`退出0。
- 注入既有仓外Godot 4.6.3后，最新树完整`npm.cmd run verify`为16/16步骤，Node 663/663、Godot R4–R10、Creator 248 modules build与HTTP smoke全绿。
- 本批未调用模型、Meshy或Marble，未读取供应商凭据，未复制SPZ/collider/转换物入仓，未修改Creator或Godot功能，未启动父服务、Docker或共享栈。

## 最终人工门

- full-resolution 1.92M splat在960×540预热后300帧中位数不少于30 FPS；
- 第一人称平移具有真实视差，panorama未可见渲染；
- collider、Meshy prop/静态人物、Action终端、ending/reset对齐且可用；
- 640×540关键UI无遮挡，控制台零错误，网络仅same-origin loopback。

## 回退

六批均可逆序`git revert`。Git回退不删除仓外转换缓存、run、截图或供应商资产。
