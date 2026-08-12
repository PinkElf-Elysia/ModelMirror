# Prototype Environment Bundle 0.1.0

R10私有环境Bundle绑定Scene Blueprint身份、`marble-1.1`环境提示SHA、panorama PNG与collider GLB的相对路径、字节数、SHA-256和离线指标。

Bundle不进入Runtime Pack、Scene Pack或存档格式，不保存原始prompt、operation/world ID、下载URL、密钥或原始响应。panorama仅作为360°天空，没有视差；collider只提供原型级行走碰撞，不宣称与全景像素严格配准。

固定上限为panorama 64 MiB、16384×8192且2:1，collider 32 MiB、provider JSON 1 MiB。SPZ、data URI、redirect、私网/loopback/IP字面量下载、符号链接和模块外路径均拒绝。

## R10.2实现

`@matrix-oasis/prototype-environment-pipeline@0.1.0-r10`公开四个稳定入口：环境计划、Marble provider创建、环境物化和Bundle复验。计划只接受canonical R8 Scene Blueprint并把prompt保存在不透明句柄内；公开计划只含Scene/Blueprint身份和prompt SHA-256。

Provider使用Node 24原生Fetch与`WLT-Api-Key`，自身不读取环境变量。正式端点固定为`/marble/v1/worlds:generate`、`/operations/{id}`和`/worlds/{id}`；只允许一次create、180次poll、一次world get和两次下载。正式资产host必须属于代码内固定候选，资格调用出现新host时应停止并另行审查，不能把任意调用方host直接加入信任范围。

物化前必须提交与Blueprint SHA绑定的精确审批摘要。成功输出仅有canonical Bundle、脱敏report和两个内存文件；发布到磁盘、run目录与`current.json`事务属于R10.3-R10.4宿主边界。普通测试仅使用loopback，不读取凭据或产生费用。

## R10.3自动组装与缓存导入

`@matrix-oasis/prototype-assembler@0.1.0-r10`离线调用冻结的R8 Generation、R9 Asset Bundle、R10 Environment Bundle、R3 Runtime与R7 Scene Pack验证器。固定profile最多四个zone、两个非环境brief、32个逻辑placement且每zone最多八个；30×30m空间按zone声明顺序使用固定4×2槽位。成功Scene Pack只引用Marble collider与Meshy visual/collider；R9中的Kenney环境物化只随Asset Bundle作为离线复验证据保留，不进入Scene Pack，也不构成环境回退。

`import:prototype-cache`只接受`C:\tmp`内真实目录和绝对参数。所有输入经fatal UTF-8、canonical、FileHandle、bigint dev/ino及size/mtime/ctime稳定性检查；Assembler引用的两类环境文件和Meshy文件在同父staging中写入、sync、回读并单次rename。`current.json`最后替换，失败不改变既有current；歧义路径不递归清理。cache key绑定prompt SHA、模型、Blueprint、Asset/Environment Bundle和assembler版本，命中缓存不读取供应商凭据。

run只保存canonical artifacts、重建的脱敏Asset report、已验证Environment report、Scene Pack、assembly/run report，以及复验这些合同所需的规范化GLB；原始prompt、供应商任务/世界ID、URL、响应与凭据均不写入。

## R10.4本地宿主与审批状态机

`preview:prototype -- --run-root <C:\tmp直接子目录>`只绑定`127.0.0.1:43110`。API要求exact same-origin、JSON content type和HttpOnly/SameSite=Strict会话cookie，无CORS；body最大64 KiB，prompt最大32 KiB且只驻留内存。一个宿主同时只允许一个非终态run和一个launch操作。

固定状态为`awaiting_model_approval → generating → awaiting_asset_approval → acquiring → normalizing → assembling → ready|failed`。第一道审批绑定当前prompt SHA、模型、最多三次请求与1美元上限；第二道审批绑定Blueprint SHA、Marble环境prompt、最多一次create/180次poll/两次下载，以及按声明顺序的Meshy brief、最多四个任务和60 credits。重复、过期或内容不匹配的审批均不执行外部操作。

宿主启动和缓存命中不读取API Key。只有模型审批后才读取`MATRIX_OASIS_MODEL_*`，只有资产审批后才读取`MATRIX_OASIS_MARBLE_API_KEY`和`MATRIX_OASIS_MESHY_API_KEY`。既有run在命中或重启恢复前会重新检查全部canonical文本、身份、hash、GLB、Scene Pack和report；失败run不会替换`current.json`。

## R10.5 Creator与Godot wrapper

Creator只通过固定same-origin相对API访问loopback宿主，浏览器不接触供应商凭据。构建资源与API共享`127.0.0.1:43110`；CSP除冻结Ajv Validator所需的同源`unsafe-eval`外保持闭合，禁止外部脚本与连接。

R10 wrapper从同一已复验run读取Runtime Pack、Receipt、Scene Pack和Environment Bundle。panorama通过`Image.load_png_from_buffer`与`PanoramaSkyMaterial`进入天空；环境GLB继续由冻结R7 loader构建静态collider，wrapper只隐藏其Visual节点。任一输入、身份、hash、图片或场景组合失败都在Godot启动前静态拒绝，不修改持久run或宿主current。
