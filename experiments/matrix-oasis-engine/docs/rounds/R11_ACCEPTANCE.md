# R11验收记录

状态：R11.2已验证，等待本地提交

固定基线：`da2a914a2ff131507750a0afb8d8881180530f62`

## 批次

- [x] R11.1 治理与空间环境边界（`e9b88dd3`）
- [x] R11.2 SPZ转换与Spatial Environment合同（本批提交；SHA在R11.3记录）
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

## R11.2证据

- 本批17个模块内路径：新增私有`@matrix-oasis/prototype-spatial-environment@0.1.0-r11`及其测试，根manifest/lock、verify编排、依赖许可证和本验收记录同步更新；R1–R10冻结路径、Creator、Godot、examples及父仓相对固定BASE均零差异。
- 包公开面固定为6个导出；输入为冻结R10 Environment Bundle、SPZ字节和显式整数校准，输出为canonical Spatial Environment Bundle与report。Bundle绑定SPZ、deterministic compressed PLY、collider、场景身份、整数毫米统计和gdgs中心补偿，不保存prompt、供应商ID、URL、凭据或本机路径。
- 锁定`@adobe/spz@0.2.2`与`@playcanvas/splat-transform@3.3.0`，继续复用Ajv及冻结内部合同；直接与传递依赖许可证均已记录，没有运行安装脚本或新增运行期网络面。
- `npm.cmd run test:spatial-environment`为8/8；覆盖成功物化、20次字节确定性、约束/身份/文件漂移、canonical输入、孤立代理项、descriptor安全、输入不变与topic-independent离线源码。
- 最新树完整`npm.cmd run verify`为17/17步骤，Node 669/669，Godot R4–R10、Creator 248 modules build与HTTP smoke全绿；`check:round-scope`、`check:parent-scope`、`check:boundary`及`git diff --check`均通过。
- 对仓外真实Marble SPZ只做内存只读探针：输入27,924,930 bytes、1,920,000 splats，输出compressed PLY 31,260,660 bytes，SHA-256为`52d18832b00148dd37cde028b07ed59fa8abf2ccec157eb7e3f155283b359c4f`；默认校准Bundle SHA-256为`52c149c77b11e5727a34f478cf845076be4d0771b3a9bdce892b397a83a696a3`。没有写出转换资产，也没有外部调用或费用。
- 本批回退为单独revert R11.2提交；它不修改或删除仓外SPZ、R10资格缓存和其他worktree。

## 最终人工门

- full-resolution 1.92M splat在960×540预热后300帧中位数不少于30 FPS；
- 第一人称平移具有真实视差，panorama未可见渲染；
- collider、Meshy prop/静态人物、Action终端、ending/reset对齐且可用；
- 640×540关键UI无遮挡，控制台零错误，网络仅same-origin loopback。

## 回退

六批均可逆序`git revert`。Git回退不删除仓外转换缓存、run、截图或供应商资产。
