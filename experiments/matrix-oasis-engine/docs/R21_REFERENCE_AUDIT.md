# R21记忆与关系候选二次核查

R21重新核查R18的记忆赛道，并将结论收窄到本轮最小语义。仓库只保存来源身份和原创适配判断；不复制候选源码、依赖、二进制、模型或运行日志。

## 生产结论

R21采用`internal-canonical-reducers-only`：从R19 World Event Ledger确定性派生记忆和关系产物，不新增第三方production/runtime依赖。现有workspace的canonical JSON、合同验证和Ledger重放能力继续复用，不建立第二个权威状态源。

这个决定不是以“自研”为目标。R21的有界输入最多为10,000条Ledger、派生产物最多16 MiB；最小能力只需要：

- 把已接受Action映射为带revision和entry hash来源的情节事实；
- 按精确Runtime entity引用计算整数关系聚合；
- 将版本化人格seed与从Ledger派生的状态严格分开；
- 删除派生产物后可从同一Ledger完整重建；
- 将缓存清除与持久忘记/纠正区分为不同语义。

第三方候选没有解决“忘记/纠正必须进入同一权威因果链”这一前置问题。引入模型抽取、向量数据库、图服务或Agent自有状态，反而会扩大权威、确定性、删除和隔离表面。R21也没有已确认的模糊搜索或语义召回产品门，因此MiniSearch与Orama暂不进入运行链。

## 固定来源与原创适配判断

| 候选 | 固定来源 | 直接许可证 | R21判断 |
| --- | --- | --- | --- |
| Cognee | `v1.5.3 / 25200a54…` | Apache-2.0 | 仅参考dataset与node-set生命周期。服务、LLM、图/向量/关系存储表面及scope/delete风险不适合本轮。 |
| Graphiti | `v0.29.3 / 021d3a57…` | Apache-2.0 | 仅参考双时态事实、episode来源和group scope。LLM、embedding、图数据库及删除/身份问题不进入R21。 |
| LangMem | 无稳定tag；固定`29cbe41e…`、源码版本`0.0.30` | MIT | 仅参考profile/collection与namespace。模型驱动写入、随机ID、发布漂移和LangGraph依赖没有为最小reducer提供净收益。 |
| Letta Code | `v0.31.8 / 385aca8f…` | Apache-2.0 | 仅参考版本化Agent memory filesystem。Agent自有状态会与Ledger单一权威冲突。R18锁定的旧Letta v1已退役，只保留历史证据。 |
| Mem0 | `ts-v3.1.7 / dc82354e…` | Apache-2.0 | 延后到R22语义召回再评估。当前模型/embedding/vector store、随机ID和`infer:false` scope风险不满足R21。 |
| MiniSearch | `v7.2.0 / 3d239d1c…` | MIT | 仅在实测证明原生查询门失败或确需CJK模糊检索时资格；不得成为权威产物或canonical hash来源。 |
| Orama | `v3.2.0 / 4e7cbe0d…` | Apache-2.0 | MiniSearch之后的触发式备选；只有明确需要hybrid retrieval时再评估。 |

上述许可证结论只覆盖锁定commit的顶层直接许可证文件。Mem0、Letta Code、Graphiti、LangMem、Cognee和MiniSearch由GitHub许可证元数据识别；Orama的GitHub元数据为`NOASSERTION`，本锁依据固定`LICENSE.md`文本记录Apache-2.0。任何未来代码复用或依赖安装都必须重新固定archive、完整许可证文本和传递依赖闭包；当前不得表述为生产许可证闭包已通过。

## 重新评估触发条件

- 只有内部projection在固定10,000条/16 MiB夹具上未达到明确的查询、启动或内存门，才重新评估本地检索依赖。
- 只有R22接受中文模糊检索或语义召回为真实产品要求，才重新评估MiniSearch、Orama或Mem0。
- 只有R23接受双时态图遍历为真实产品要求，并且Graphiti的身份、scope和删除阻断已有可执行修复证据，才重新评估Graphiti。
- LangMem必须先有可锁定稳定发布，并证明相对直接使用底层Store的独立价值。
- Cognee必须先证明严格跨dataset隔离、删除正确性、服务隔离和传递许可证闭包。
- Letta只在产品明确批准“Agent拥有独立状态权威”的架构变化时重审；普通功能增长不能触发。

触发重新评估也不等于采用。届时仍需完成20次字节/结果一致、CJK排序、删除后不复活、跨timeline/actor隔离、崩溃恢复、零隐式网络、安装脚本/原生二进制和许可证闭包证据。

## 证据边界

本轮确认的是来源身份、顶层直接许可证和适用/否决边界。没有执行候选代码，也没有证明任何候选的传递许可证、性能、删除或多租户正确性。R21生产正确性只能由内部reducer针对真实Ledger的确定性、重建、删除和隔离测试证明。
