# Authoring Game Pack 0.1.0

Authoring Game Pack 是作者层的单文件、单语言、案例无关数据合同。R1 只冻结结构与确定性验证语义，不把它编译或执行为游戏。

权威 Schema：`packages/game-pack-contracts/schemas/0.1.0/authoring-game-pack.schema.json`。

## 数据面

- 根元数据固定格式、格式版本、内容版本、语言、标题与入口节点。
- `entities` 是不透明的可引用对象，不预设人物、地点、物品或题材分类。
- `variables` 只允许 boolean、JS 安全 integer，以及带显式 `allowedValues` 的 enum。
- `cues` 只声明 visual、audio 或 ui 渠道的抽象意图，不携带资源路径、坐标、时长、Godot 类型或播放参数。
- `nodes` 提供文字、实体引用、入口 Cue 与玩家 actions；`endings` 是独立、可引用的终止集合。
- action 可带纯条件 AST、按数组顺序声明的 effects，以及带 `kind` 的 node/ending 目标。

## 条件与效果

条件只允许 `all`、`any`、`not`、`eq`、`ne`、`lt`、`lte`、`gt`、`gte`。效果只允许 `set`、`add`、`emitCue`。它们是 JSON AST，不接受字符串 DSL、脚本、模板、函数、变量路径或自定义 opcode。

R1 书面语义固定为：`when` 读取 action 前状态；effects 按数组顺序；最后发生目标跳转。R1 不提供求值器、状态机或运行时，因此这些声明不会被本轮执行。

## Schema 与语义验证边界

Schema 负责必填字段、精确 tag、类型、闭合对象、ID 字面格式、安全整数和结构预算。验证器负责 entities、variables、cues、nodes、endings 之间的顶层 ID 全局唯一、节点内 action ID 唯一、引用存在性、变量类型一致性、enum 初值、条件深度与图可达性；验证器不得填默认值、强制类型或删除字段。

condition 根深度记为 1，最大允许 16。静态图允许显式循环，但入口必须存在、所有节点必须可达，且每个入口可达节点都必须在忽略条件时存在通往某个 ending 的路径。R1 不证明条件可满足性或游戏必然终止。

严格 JSON 或 Schema 任一诊断会阻断全部语义分析。图分析只在 node/ending ID 无冲突、入口存在且所有 typed target 有效后运行；不可达节点不会再叠加“无 ending 路径”诊断，以控制级联噪声。

## 验证接口

`@matrix-oasis/game-pack-validator` 提供内存值与严格 JSON 文本两个只读入口。二者返回 `{ reportVersion: 1, valid, diagnostics }`，不返回、修改或补全输入 Pack。诊断按 parse、schema、semantic 阶段稳定排序，使用安全 JSON Pointer 定位，并只包含静态消息；未知键名、输入值、Ajv 参数、本机路径与异常细节不会进入报告。

模块根 CLI `npm.cmd run --silent validate:pack -- <relative.json> --json` 只接受模块内真实 `.json` 文件，大小上限 1 MiB。退出码 0 表示合法、1 表示内容被确定性拒绝、2 表示工具或路径故障；它不执行 Pack 中的条件或效果。

## 明确排除

R1 不定义 Compiler、Runtime Pack、Domain Patch、存档、回放、计时、随机、并发、AI、NPC 认知、RAG、MCP、多文件 include、本地化资源、资产绑定、Godot 协议或 Creator 编辑能力。

测试样例只是合同与诊断夹具。更换题材不得要求 Schema、验证器或公共错误码增加专属概念。

R1.4 用 `mechanics-conformance` 覆盖全部冻结操作符并作为通用回归权威；`last-train-r1` 只证明同一合同可表达一段薄型叙事图并服务未来可视化验收。核心合同与验证器测试不得导入后者，替换其题材只能影响样例专属验收。
