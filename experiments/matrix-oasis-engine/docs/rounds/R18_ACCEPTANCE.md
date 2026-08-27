# R18第二版全景选型验收记录

状态：自动门已通过；待人工验收

## 固定基线

- `R18_BASE_SHA=4bfef53c4b32f3fa8044122553c7a8f42bd08908`
- 分支：`codex/matrix-oasis-r18-v2-landscape`
- 版本：`0.18.0-r18`

## 已形成的证据

- 候选覆盖：62个唯一候选、96个赛道条目、8个赛道。
- 实际启动或审计：13个唯一候选；全部如实保持`evidence-gap`。
- 可执行赛道：每赛道2–3个排名短名单；正式集成推荐为0。
- R19–R25：目标、依赖、进入/退出门、禁止跨轮实现项及回退路径已固定。
- 供应商、外部模型和商业产品请求数均为0；未读取凭据，未启动容器。

## 身份哈希

- source lock：`f5e27479985a6de2a04055c4d5f97a99b687847484fd7e668a508ccd618b985e`
- catalog：`e47791fd90ba0776bf90c907fc52ed57f7bf47595bb362c858152255be157222`
- evidence set：`3d448f7760a08d63c0073bf37fa3269300757c658c81c97f37ef8ab9b483cbd0`
- decision landscape：`65ed29270ec77aa2e64401f591e5f7fb58e93acb65456c4bf141e42195813a00`
- roadmap：`8ecf5d2a5b2e4f5fea3ac64960949ce56b7a095651bcaba96bef42a4b927b428`

## 自动验证

- `npm.cmd ci`：通过；安装166个包。
- `npm.cmd prefix` / `npm.cmd ls --all`：通过；模块根和workspace依赖解析正确。
- `doctor:godot`：Node 24.18.0、npm 11.16.0、Git 2.51.0、Godot 4.6.3全部就绪。
- `verify:r18`：10个专用阶段全部通过。
- `verify`：27个主链阶段通过；常规套件927项全部通过；Creator build与smoke通过。
- 父`client` clean：119个测试文件、703项测试通过；production build通过。
- `check:round-scope`：82项检查、66项变更，通过。
- `check:parent-scope`：82项检查、66项变更，父仓零越界。
- `git diff --check`：通过。
- `verify:extraction`：在clean source上通过；standalone共1351个文件，临时产物已安全清理。

`npm ci`审计仍报告模块1个high/1个low、父`client`2个high/2个moderate/1个low的已知依赖漏洞。R18没有执行越界的`npm audit fix`或依赖升级。

## 待人工验收

- 八个赛道覆盖、来源多样性和新候选配额。
- 分层短名单、分类资格、证伪证据和切换条件。
- R19–R25路线及V2声明门。
- standalone、父范围、R16回归和仓外证据身份。

人工验收需确认每项结论的证据层、证据缺口、可行动路径与切换条件均如实可追溯。`claimAllowed`保持`false`，未经用户明确验收不push、不创建PR。
