# Prototype Environment Bundle 0.1.0

R10私有环境Bundle绑定Scene Blueprint身份、`marble-1.1`环境提示SHA、panorama PNG与collider GLB的相对路径、字节数、SHA-256和离线指标。

Bundle不进入Runtime Pack、Scene Pack或存档格式，不保存原始prompt、operation/world ID、下载URL、密钥或原始响应。panorama仅作为360°天空，没有视差；collider只提供原型级行走碰撞，不宣称与全景像素严格配准。

固定上限为panorama 64 MiB、16384×8192且2:1，collider 32 MiB、provider JSON 1 MiB。SPZ、data URI、redirect、私网/loopback/IP字面量下载、符号链接和模块外路径均拒绝。

## R10.2实现

`@matrix-oasis/prototype-environment-pipeline@0.1.0-r10`公开四个稳定入口：环境计划、Marble provider创建、环境物化和Bundle复验。计划只接受canonical R8 Scene Blueprint并把prompt保存在不透明句柄内；公开计划只含Scene/Blueprint身份和prompt SHA-256。

Provider使用Node 24原生Fetch与`WLT-Api-Key`，自身不读取环境变量。正式端点固定为`/marble/v1/worlds:generate`、`/operations/{id}`和`/worlds/{id}`；只允许一次create、180次poll、一次world get和两次下载。正式资产host必须属于代码内固定候选，资格调用出现新host时应停止并另行审查，不能把任意调用方host直接加入信任范围。

物化前必须提交与Blueprint SHA绑定的精确审批摘要。成功输出仅有canonical Bundle、脱敏report和两个内存文件；发布到磁盘、run目录与`current.json`事务属于R10.3-R10.4宿主边界。普通测试仅使用loopback，不读取凭据或产生费用。

## R10.3自动组装与缓存导入

`@matrix-oasis/prototype-assembler@0.1.0-r10`离线调用冻结的R8 Generation、R9 Asset Bundle、R10 Environment Bundle、R3 Runtime与R7 Scene Pack验证器。固定profile最多四个zone、两个非环境brief、32个逻辑placement且每zone最多八个；30×30m空间按zone声明顺序使用固定4×2槽位。成功Scene Pack只使用Marble collider与Meshy visual/collider，R9中的Kenney环境物化仅作为历史资格输入复验，不复制到run。

`import:prototype-cache`只接受`C:\tmp`内真实目录和绝对参数。所有输入经fatal UTF-8、canonical、FileHandle、bigint dev/ino及size/mtime/ctime稳定性检查；Assembler引用的两类环境文件和Meshy文件在同父staging中写入、sync、回读并单次rename。`current.json`最后替换，失败不改变既有current；歧义路径不递归清理。cache key绑定prompt SHA、模型、Blueprint、Asset/Environment Bundle和assembler版本，命中缓存不读取供应商凭据。

run只保存canonical artifacts、重建的脱敏Asset report、已验证Environment report、Scene Pack、assembly/run report和被引用资产；原始prompt、Kenney GLB、供应商任务/世界ID、URL、响应与凭据均不写入。
