# MCP Wave 26B：Token 只读数据适配

## 结论

Wave 26B 首个验收单元只实现两个默认关闭的 staged 兼容契约：

- `chanmeng666-server-google-news`：固定 SerpAPI Google News 搜索；
- `isnow890-naver-search-mcp`：固定 Naver Developer Center 的网页、新闻和博客搜索。

两项仍为 `planned`，不进入生产 `MCP_TOKEN_ALLOWED_ADAPTERS`。在真实只读账号完成
预检、代表调用、限流与超时验收之前，不得晋级 `ready`。

## 冻结的上游身份

| 目录 ID | 上游版本与提交 | 许可证 | 固定出口 |
|---|---|---|---|
| `chanmeng666-server-google-news` | 1.0.0 / `5ed14341ff6ef290e13bafa08abc12157bbe23a3` | MIT | `serpapi.com:443` |
| `isnow890-naver-search-mcp` | 1.0.50 / `d7c7c58cab0de2692336b710727f1ee123270e6c` | MIT | `openapi.naver.com:443` |

本地 sidecar 不安装或执行上述仓库包，只实现经审阅的产品身份子集。工具 Schema 摘要、
凭据槽和固定 Host 保存在私有 sidecar 契约中，客户端不能提交 Host、Header、环境变量或
动态 endpoint。

## 允许与拒绝的能力

Google News 仅开放 `google_news_search`，接受有长度限制的查询、国家、语言和结果数。
上游的 topic、publication、story、section token 均不开放。SerpAPI Key 仅由加密槽映射
到短生命周期环境变量，不出现在结果或日志中。

Naver 仅开放 `search_webkr`、`search_news`、`search_blog`，分页最多 20 条、起点最多
1000。只支持 classic Developer Center 凭据和 `openapi.naver.com`；Naver API Hub、
DataLab、购物、图片、Local、知识社区、Cafe 和分类工具均不开放。

两项输出都去除供应商 HTML 标记、限制字段和大小、拒绝包含 credential-like query 的
结果链接。Provider 429 不自动重发。

## 本批不实现的候选

- BigQuery：真实查询会产生云资源读取和计费，等待项目/数据集 Scope 与费用上限；
- Nutrient DWS：核心能力涉及云端上传、转换、签名或 OCR，不属于 Token 只读数据；
- Massive/Polygon：当前产品身份依赖动态 endpoint 搜索、通用 `call_api` 和本地 SQL
  控制面，无法在保持身份的同时收窄为固定小工具面。

## 验收和回退

当前可执行验收仅包括离线 initialize、tools/list、Schema、固定请求投影、429、Secret
脱敏、额外参数/工具拒绝、默认 allowlist 拒绝、断开与清理。供应商代表调用尚未执行，
这是保持 `planned` 的明确边界。

回退只需移除这两个 staged 私有契约及测试；生产 allowlist、共享栈和持久数据均未变化。
