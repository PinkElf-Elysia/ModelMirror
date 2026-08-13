# R11验收记录

状态：R11.3已验证，等待本地提交

固定基线：`da2a914a2ff131507750a0afb8d8881180530f62`

## 批次

- [x] R11.1 治理与空间环境边界（`e9b88dd3`）
- [x] R11.2 SPZ转换与Spatial Environment合同（`07e80872`）
- [x] R11.3 Godot gdgs Compute导入底座（本批提交；SHA在R11.4记录）
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

## R11.3证据

- 本批87个模块内路径，其中原样vendoring `apps/runtime-godot/addons/gdgs/**` 73个文件、429,070 bytes；固定上游annotated tag object `70996511607a886dac9fdd5fc59a0445308eb3db`、peeled commit `d9de8db86a63e8bf9067c869dcdbd0614922fd1e`、Git tree `06d1bb2a71e8fc0abf5a2bca8f2cd7effdbaed17`、确定性供应链tree SHA-256 `9b50fbd348408d9d9acce99d4a189fe468ee09a46921c73df4436fe3a7afbd82`和MIT许可证SHA-256 `5f6105df7c9d6af2a32867c350781b500d378c9b3e8966bba900c1ed5d40f6cc`。供应链锁声明上游字节未修改且无符号链接。
- `verify:vendor`已纳入gdgs精确文件、字节、许可证和未知addon检查；Godot一方源码边界只排除两个受独立供应链哈希约束的精确根`addons/gdUnit4`与`addons/gdgs`，负向fixture证明其他addon仍被拒绝。
- 新增一方Compute守卫与一次性headless import probe。`npm.cmd run verify:godot:splat`为5/5，使用Godot 4.6.3在仓外临时工程启用gdgs、配置`gdgs/rendering/backend="Compute"`，成功导入固定3-splat compressed PLY并输出`GODOT_SPLAT_OK version=4.6.3 configured=Compute points=3`；临时工程按身份安全清理。
- 最新树完整`npm.cmd run verify`为17/17步骤，Node 675/675，Godot R4–R10与R11 gdgs导入、Creator 248 modules build和HTTP 200 smoke全绿；`check:round-scope`、`check:parent-scope`、`check:boundary`及`git diff --check`均通过。
- 本批headless证据只证明精确Compute配置、插件加载、导入器和资源读取链路；headless环境没有证明GPU Compute实际渲染、full-resolution 1.92M splat画面、视差或FPS，这些继续作为R11.5/R11.6人工硬门，不把配置证据表述为图形验收。
- R1–R10冻结实现与验收、Creator既有实现、Godot既有场景、examples及父仓相对固定BASE均零差异；未读取凭据、未调用模型/Meshy/Marble、未写入真实SPZ转换物，也未启动父服务、Docker或共享栈。本批可单独revert，回退不会删除仓外供应商资产。

## 最终人工门

- full-resolution 1.92M splat在960×540预热后300帧中位数不少于30 FPS；
- 第一人称平移具有真实视差，panorama未可见渲染；
- collider、Meshy prop/静态人物、Action终端、ending/reset对齐且可用；
- 640×540关键UI无遮挡，控制台零错误，网络仅same-origin loopback。

## 回退

六批均可逆序`git revert`。Git回退不删除仓外转换缓存、run、截图或供应商资产。
