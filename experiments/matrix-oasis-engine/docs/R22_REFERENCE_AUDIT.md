# R22候选与Provider二次核查

R22没有直接继承R18的推荐结论。本轮重新固定与“单次受限认知”直接相关的来源身份、顶层直接许可证和适用边界；没有执行候选代码，也没有证明传递许可证闭包。

## 生产取舍

生产实现固定为`internal-bounded-cognition-native-control`：内部有界上下文与事务适配器、Godot原生`Control`、R20移动链和R19裁决。新增第三方运行依赖为零。

| 候选 | 固定来源 | 顶层直接许可证 | R22结论 |
| --- | --- | --- | --- |
| Dialogue Manager | `v3.10.4 / 5487c524…`与`v4.0.3 / ffc0011a…` | MIT | 仅作为以后可信作者对白的表现层备选。v3适配Godot 4.6但表达式/场景成员访问面不能承接模型文本；v4面向Godot 4.7。 |
| LangGraph | `v1.0.5 / 84023451…` | MIT | 只参考持久化与人工中断。interrupt恢复会从节点开头重跑，不能包裹本轮付费单次调用。 |
| AutoGen | `python-v0.7.5 / 83afbf58…` | MIT（代码） | 拒绝作为R22生产依赖；多Agent、工具与状态服务面超出本轮，且官方已将项目置于maintenance mode。 |
| CAMEL | `v0.2.90`注释tag对象`a3a21ef…`，目标commit`deb286f…` | Apache-2.0 | 延后参考；工具、记忆、存储和多Agent表面没有为单轮认知提供净收益。 |

Dialogue Manager的安全问题、LangGraph interrupt语义、OpenAI模型/Structured Outputs与数据控制说明只作为公开文档证据，不作为可执行依赖。`store:false`不得描述为ZDR；官方默认abuse monitoring可能保留内容最多30天，且可能使用prompt caching。

## 重新评估条件

- 只有可信作者对白确需可视化脚本表现，且严格关闭表达式、mutation、动态资源和场景成员访问后，才重新资格Dialogue Manager。
- 只有R23之后出现跨多个本地纯函数节点的持久编排需求，并有付费outbox幂等证明，才重新评估LangGraph。
- AutoGen/CAMEL只有在产品明确批准多Agent或工具编排范围后才能重审，普通对白功能增长不能触发。

## 证据边界

`third-party/npc-cognition-references/reference.lock.json`锁定commit、tree和直接许可证字节。它不证明候选可运行、传递依赖许可证、安全配置或生产适用性；R22正确性只能由内部实现的离线证伪、loopback资格和一次另行批准的真实调用证明。
