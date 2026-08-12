# MCP 第 19B 批：图与向量数据库只读适配

## 当前结论

本批已在现有 `mcp-database` sidecar 中实现 Milvus、Neo4j 与 ArcadeDB 三个固定原生
REST facade，并完成独立镜像、真实服务、原生只读账号、代表调用、429、超时、拒写、
断开、重启与清理验收。Wave 23 又在 `origin/main@3546185d` 上使用 fresh sidecar 镜像和真实
Milvus 2.5.21、Neo4j Enterprise 5.26.12、ArcadeDB 26.8.1 完整复验。用户已基于本次证据明确
批准晋级；三项现为 `ready`，并进入 `mcp-database` 精确默认 allowlist。

| 目录 ID | 审阅上游 | 许可证 | 固定兼容边界 |
|---|---|---|---|
| `zilliztech-mcp-server-milvus` | 0.1.1 / `a7e624f3057a0d739528bca3ed92504943224ceb` | Apache-2.0 | 绑定一个 database、collection、vector field 与输出字段 |
| `neo4j-contrib-mcp-neo4j` | `mcp-neo4j-cypher-v0.6.0` / `dbc01ba78f171851f2d57dcd125b028c29912fd1` | MIT | Query API v2、原生 `reader` 角色、固定 database |
| `arcadedata-arcadedb` | 26.8.1 / `87bdc67f1f0331fa2d07e932a550064c118eae70` | Apache-2.0 | HTTP Query API、原生 `readonly` 组、固定 database |

上游项目只用于确认产品身份、版本、许可证、工具意图和副作用。运行镜像不复制或执行
上述 MCP Server；ModelMirror 只调用供应商公开的原生只读 API。

## 固定工具与 Schema

- Milvus：`list_collections`、`describe_collection`、`get_entities`、
  `search_vectors`；Schema SHA-256
  `4ab513a696a50d5f215e165ea3c3d7eaab67684e4a2140b55b09fb7d4935516b`。
- Neo4j：`get_schema`、`read_cypher`；Schema SHA-256
  `8ce58031d3cc32f4ef2a1b868280a62f3b8f913c00fb13d97864c8e341edc967`。
- ArcadeDB：`list_types`、`describe_type`、`read_query`；Schema SHA-256
  `1a1176ef633813c4b75a597f32f95ab4c0becc626d941e47749e5c62e3b1ca05`。

所有工具均标记为只读、幂等、非破坏且非开放世界。客户端不能提供 URL、DSN、Header、
环境变量、命令、工作目录、API path、procedure 或 collection/database 覆盖值；写工具
不在 `tools/list` 中，调用时由网关返回 `-32601`。

## 配置、查询与权限边界

- 三项只接受结构化 `host`、`port`、`tls_mode=verify-full`、`database`、`username`
  与服务端加密的 `password`。Milvus 额外绑定 `collection`、`vector_field` 和最多 32 个
  `output_fields`。IP 字面量、回环/link-local/metadata/保留地址、重定向与 DNS 漂移均拒绝；
  RFC1918 只允许管理员精确列出的主机。明文模式仅存在于隔离验收开关，生产 Compose
  未设置。
- Milvus 不接受 filter 或动态输出字段；实体 ID 最多 100 个、向量搜索最多 100 条，原生
  角色仅具 `Query`、`Search`、`DescribeCollection` 与 `ShowCollections`。
- Neo4j 只允许一个以 `MATCH`、`OPTIONAL MATCH`、`WITH`、`UNWIND` 或 `RETURN` 开始且
  含 `RETURN` 的 Cypher；写子句、procedure、扩展函数、`LOAD`、管理和多语句全部拒绝。
  facade 再包裹固定 `LIMIT`，Query API 返回非只读 `queryType` 时 fail closed。
- ArcadeDB 只允许一个 `SELECT`、`MATCH` 或 `TRAVERSE`；写入、DDL、管理、profile、
  import/export、脚本和外部 URL/文件函数全部拒绝。仅使用 `/api/v1/query/{database}`，
  原生账号属于 `readonly` 组。
- 查询最多 8 KiB，参数最多 32 KiB、深度 6，结果最多 100 行；原始响应上限 256 KiB，
  供应商请求 12 秒超时，sidecar 15 秒硬截止。错误内容经过网关脱敏。

## 隔离验收证据

- Wave 23 fresh 镜像：`modelmirror-mcp-database:wave23-stock`，manifest list
  `sha256:ac69da3ec78c98d1394339ab9a191e5db00551f6c5cbbf52b839a123a2606166`。
- Wave 23 复验再次通过三项真实代表读取、Milvus/Neo4j/ArcadeDB 原生 reader 拒写、固定 429
  脱敏、Neo4j 15 秒超时、断开后仅 PID1、sidecar 重启后代表调用恢复；验收资源最终为
  `containers=0 volumes=0 networks=0`，未触碰共享栈。
- staged 镜像：`modelmirror-mcp-database:wave19b-staged`，本地镜像 ID
  `sha256:47b5d11dd67e11f86f6c5935b649e5f95e4cc0ca97aa63b121259324d2fd269c`。
- 真实服务：Milvus 2.5.21
  `sha256:48d43985fa01806ea804da5508753a70a2fa90ae98343d4ba8889371e1577121`、
  Neo4j Enterprise 5.26.12
  `sha256:4cc18477966874259c85575b8f881312a143f95b5b0863284c1ebebb97068d41`、
  ArcadeDB 26.8.1
  `sha256:49036720b1678b9c7a6dbf22fc34a812c8d7bed15508c22cbb02c0dddc0ca16a`。
- 三项均由 `--network none` 的临时客户端通过私有 UDS 完成 `initialize`、`tools/list`、
  冻结 Schema 和全部代表读取；任意连接字段/写查询以 `-32602` 拒绝，写工具以
  `-32601` 拒绝。
- 三套供应商原生只读账号均已证明读取成功、写入失败：Milvus 拒绝 insert，Neo4j
  `reader` 拒绝创建节点，ArcadeDB `readonly` 拒绝 command 写入。
- 固定 429 fixture 证明 provider rate limit 被脱敏；Neo4j 固定计算型只读查询在 12 秒
  provider timeout 内失败，随后无 reader 事务残留。sidecar 精确重启后代表调用再次通过。
- 每次 UDS 断开后 sidecar 仅保留 PID1；失败 fixture、failure sidecar 与临时 socket 卷
  已精确删除。未启动、停止或重建共享栈。

## 晋级、回退与剩余门槛

Wave 23 收口后，当前目录为 `71 ready / 27 planned / 102 blocked`，扩充 100 项为
`26 ready / 13 planned / 61 blocked`。本次晋级仅包含：

1. 三个精确 ID 改为 `ready`；
2. 三个 ID 加入远程 database sidecar 的精确默认 allowlist；
3. 保留所有固定 Host、资源、Schema、原生只读账号和输出上限。

回退不需要数据迁移：从 allowlist 移除三个精确 ID、恢复为 `planned` 并断开相关目录
会话即可。供应商数据库、collection、图数据和账号不由 ModelMirror 创建、迁移或删除。
