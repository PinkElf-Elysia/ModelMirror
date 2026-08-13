# R12真实调用审批模板

每次批准仅覆盖所列批次；未填项、内容/hash变化或超出预算均须停止并重新批准。

## 模型生成

- 服务商/endpoint：OpenAI官方endpoint
- 模型：`gpt-5.6-luna`
- 上传：完整资格自然语言提示；不含JSON、Schema、密钥或本机资料
- 上限：3次请求、1美元
- 仓外目录：待资格前记录
- 保留/清理：仅脱敏报告与canonical artifacts；失败临时目录安全时清理

## 环境与资产

- Marble：`marble-1.1`，1次create、最多180次/10秒poll、1次Get World、panorama/SPZ/collider各下载一次；上限1600 credits / 1.50美元
- Meshy：`meshy-6`，6次preview、6次refine；最多12个任务、180 credits，每任务最多120次/5秒poll，最终GLB各下载一次
- 上传：完整环境提示和六个asset brief，资格前逐字披露
- 远程保留：默认保留；删除需另行批准
- 仓外目录：待资格前记录
- 日志：不记录密钥、远程ID、URL、原始响应或供应商异常正文
