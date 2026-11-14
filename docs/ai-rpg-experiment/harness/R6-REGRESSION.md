# R6 收尾：旧桥接回执失败归因

结论：此前 `rpg_receipt_pending` 不是第六轮引入。Windows 本机测试路径过长，回执 outbox 的临时文件不能创建。保留真实失败记录，使用任务专属短 basetemp 完成验证，没有放宽安全检查、关闭断言或修改公共桥接。

## 原因链与对照

`rpg_bridge._completion` → `ProviderChatStableService.complete` → `stage_chat_control_completion` → `temporary.open("xb")`。

异常为 `FileNotFoundError`，包装成 `provider_chat_completion_outbox_write_failed`，桥接将无法确认为已落盘的结果保守标成 `rpg_receipt_pending`。这条保守处理正确，不能把上游 HTTP 200 直接当成最终提交成功。

失败样例位于任务的长测试目录，完整文件名由 64 字符摘要、32 字符随机后缀及扩展名组成。诊断路径长 **264 字符**；父目录存在，同一路径普通写入失败，加 Windows 扩展路径前缀后写入成功。合成诊断文件已删除；没有改系统长路径配置。

| 源码 | 临时路径 | 同一旧用例结果 |
|---|---|---|
| R6 当前工作树 | .rpg03-work/r6-validation/trace-pytest-v1 下的长路径 | 失败，完整异常链定位 outbox 写入 |
| 冻结基线 38266775（隔离 C:/tmp/mm-r6-base-check） | 同等长路径 base-long-proof | 同样 502 / rpg_receipt_pending |
| R6 当前工作树 | C:/tmp/r6-py-short-a | 通过 |
| 冻结基线 38266775 | C:/tmp/r6-py-base-a | 通过 |

用例：`server/tests/test_rpg_bridge.py::test_god_long_profile_is_bound_and_default_unchanged[google/gemini-3.8-flash]`。全部使用已有 MockTransport，不产生真实供应商调用。父项目 server 相对基线无改动；对照基线工作树无源码改动。

## 收尾处理

本轮只更正验证环境和文档，不扩展为通用 Windows 存储路径补丁。系统默认 pytest 目录另有权限问题，因此显式指定新的、任务专属短路径：

```powershell
python -m pytest server/tests/test_rpg_bridge.py server/tests/test_rpg_local_state_tools.py experiments/ai-rpg-engine/agent-core/actions/real/test_protocol.py experiments/ai-rpg-engine/agent-core/actors/real/test_protocol.py --import-mode=importlib --basetemp=C:/tmp/r6-py-suite-a -q --tb=short
```

结果：**88 passed，1 failed**。唯一剩余项 `test_parent_app_registers_rpg_routes_and_default_disabled` 因本机缺少 `duckdb`，在导入父应用时失败；与 outbox 无关，未减少断言。复验时使用新的未占用 basetemp 名称，避免 pytest 清理其他任务目录。

该依赖已在 server/requirements.txt 声明，无需改依赖文件或在共享环境安装。单独利用已有后端镜像，在 `--network none --rm` 临时容器中验证父应用注册；源码只读挂载后复制到容器临时目录，未挂载共享数据、未启用服务端口，不属于部署。该项最终结果见 R6-CLOSEOUT。

诊断证据位于忽略目录 `.rpg03-work/r6-closeout/`，包括异常追踪辅助脚本和 path-diagnostic.json。不将临时日志或合成路由库纳入 PR。

## 后续边界

对任意长路径的存储支持若需产品化，应另立适配任务，不在本次原型引入系统设置变更或削弱持久化确认。此次真实三次请求均已取得成功回执，临时凭据清理及原始证据不受此测试路径问题影响。
