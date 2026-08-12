# R9 Meshy 单次操作审批记录

每个真实任务的 create、poll、download 是三个独立外部操作，必须分别在当前交互中取得一次性批准。批准不能跨brief、跨阶段或跨轮次复用。

执行前披露：

- brief ID、kind和将上传的脱敏文本；
- endpoint、模型、preview/refine参数与本阶段请求上限；
- 最新官方credits/金额估算、账户输出许可证和远程保留策略；
- 仓外输出目录、轮询次数/间隔或下载字节上限；
- 日志脱敏、失败清理和本地回退方法。

固定四任务为 prop preview/refine、character preview/refine。轮询最多120次、每5秒一次；每个任务最多一次GLB下载。API key只由资格CLI从 `MATRIX_OASIS_MESHY_API_KEY` 读取，仓内模板和日志不得含值。
