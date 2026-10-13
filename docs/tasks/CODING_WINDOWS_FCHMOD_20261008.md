# Coding Windows 临时文件权限兼容

## 范围与基线

- 用户已授权独立定位修复；不提交、不部署、不混入 R8。
- 干净基线：e3ac455553c2d3065bcd8768d78b849c8477392a。
- 分支：codex/coding-windows-fchmod-20261008。
- 修改前工作树干净；允许修改 engine.py、权限回归测试及本任务卡，最多四个文件。
- 不修改 Runtime、R8、依赖、公开接口、权限授权或已有业务数据。
- 风险：本地临时文件权限、句柄生命周期和原子替换；测试仅使用合成临时文件。

## 已复现

干净基线运行 test_coding_applier_engine.py：第一个测试失败。
_prepare_temp_file 无条件调用 os.fchmod；当前 Windows Python 无该接口。
该失败独立于 R8 变更，不能据此归因其他全量失败。

## 修复约束

- 有原生 fchmod 时原样使用，错误不降级。
- Windows 旧 Python 只通过持有的文件描述符取得系统句柄，再设置只读属性。
- 禁止按临时文件路径 chmod，避免引入路径替换窗口；不修改 ACL 或宣称支持 POSIX 全部权限位。
- 保留 fdopen、flush、fsync、关闭句柄、失败清理及 os.replace 顺序。
- 不支持的平台明确失败。
- Windows 只读目标在创建临时文件前拒绝；不解除目标只读属性、不遗留只读临时文件。
  已通过故障用例复现：若直接支持 fchmod 而不前置拒绝，替换失败会遗留临时文件。

## 验收

1. 现有 engine、security、tempfile 三组测试。
2. 新增真实 Windows 句柄权限测试：只读/可写、其他属性保留、失败不降级、描述符关闭和清理。
3. Coding 受影响回归；AST、Diff 和敏感信息扫描。
4. Linux 原生路径若未实际执行则明确标为未运行，不以模拟代替。

命令使用 Python -B -m pytest、-p no:cacheprovider 与独立 basetemp。
全量 R8 在原工作树继续运行，本批代码不改变该运行快照。

## 回退

仅撤回本批 helper、单点调用和新增测试；无数据迁移、无活动指针修改。
回退会恢复旧 Windows 失败，不能宣称仍兼容。当前未提交、推送或部署。

## 参考

- 本机 Python 3.12.14 没有 os.fchmod；Python 3.13 才增加 Windows 支持。
  Windows chmod 仅支持只读位，本批不扩大 ACL 权限。
- https://github.com/python/cpython/blob/v3.11.14/Doc/library/os.rst
- https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_basic_info

## 结果

### 已运行结果

- 干净基线：test_coding_applier_engine.py -q -x，1 failed，1.84 秒；
  失败为原生 os.fchmod 缺失，不是 R8 引入。
- 第一次扩大检查：engine/security/tempfile/windows_permissions 四文件，
  32 passed / 3 failed / 2 skipped，9.69 秒。
  三项失败均为 Windows 缺少 asyncio Unix socket 接口。
- 在 Coding 文件未修改的 R8 原树单跑 security 文件：
  1 passed / 3 failed，4.62 秒，复现相同 start_unix_server/open_unix_connection 失败。
  不修改通信协议、不跳过失败测试，Linux socket 回归仍为未运行。
- 只读目标证伪：前置拒绝前 1 failed，0.77 秒，证实替换失败遗留只读临时文件。
- 最终权限及恢复组：engine、tempfile、windows_permissions，以及
  recovery_reconcile 中三个 applier 恢复/幂等/冲突用例：
  **35 passed / 2 skipped，7.79 秒**。两项跳过为当前 Windows 主机不能创建符号链接。
- 仓库 Windows Project Host CI 同组本地执行：
  test_coding_project_host_windows.py，加 test_coding_project_host.py 中
  test_host_commit_windows_native_apply_commit_and_undo、
  test_host_commit_windows_private_object_cleanup_rejects_replacement：
  **87 passed / 1 skipped，130.72 秒**；跳过为目录符号链接权限。
  这是本地测试结果，不是 GitHub CI 结果。
- Ubuntu Python 3.14.4 无 pytest/pydantic/httpx，未安装依赖；
  只提取当前生产文件的三个权限/临时文件 helper 做原生文件冒烟，
  0600、0640、0444 权限保持与 fsync 故障清理均通过。
  该局部冒烟不替代完整模块导入、Linux pytest 或 Unix socket 验证。
- 两个修改/新增 Python 文件 AST 通过；git diff --check 通过；
  三个变更路径的限定凭据特征与运行产物路径扫描零命中，暂存为空。

### 当前结论与剩余边界

独立小批已修复 Windows 缺少 fchmod 导致普通 apply 失败；
原生权限失败仍不降级，Windows 只读目标保持原值并明确拒绝。
原子替换、回滚、幂等与重启对账相关原断言未放宽。
Unix socket、符号链接权限及完整 Linux 回归未关闭，不能宣称 Coding 全量兼容。
未集成到 R8 工作树，未提交、推送、创建 PR 或部署。

## 补充验证（2026-10-08）

用户要求重新完成未完成的验证。本次不修改生产代码或测试断言，保留以上历史结果。
采用同一 e3ac455553c2d3065bcd8768d78b849c8477392a 基线加本批三个文件的冻结快照，
归档 SHA-256 为 ebbffdba9ed658e6df8d3527d3a8cfe86bf2e5567b0fa91aa4f14299002edd5f。
运行前后核对覆盖文件哈希一致；以下任务卡补记不改变已验证代码。

- 使用已有本地 Linux 验证镜像，Python 3.12.14，逐项核对服务端固定依赖，无不匹配。
  独立临时容器关闭网络、非 root、只读根文件系统、无共享服务或凭据挂载。
- 完整执行 engine、security、tempfile、windows_permissions、recovery_reconcile 五个测试文件：
  `python -B -m pytest ... -q -ra -p no:cacheprovider --tb=short`。
  **39 passed / 7 skipped，0.98 秒，退出 0**。
  原 Windows 无法执行的 Unix socket 与 Linux 符号链接用例已在 Linux 实际通过。
  七个跳过项是 Windows 专用用例；其真实 Windows 结果仍以上节为准。
- engine.py 与新增权限测试的 `py_compile` 通过，缓存写入仓库外独立目录。
  `git diff --check` 通过，未修改依赖清单或锁文件。
- 完整日志、JUnit、环境与结果回执位于
  `C:\tmp\modelmirror-r8-verification-20261008\coding-evidence\coding-linux-*`；
  冻结源与哈希清单位于同目录上级的 `coding-source`。

本次关闭该修复的 Linux 模块、原生权限与 Unix socket 回归缺口，
不等于完成整个 Coding 系统全量或真实 Provider 验收。
Windows 主机缺少符号链接权限的跳过项仍未补跑，不能以 Linux 结果代替。
仍未集成 R8、提交、推送、创建 PR 或部署。
