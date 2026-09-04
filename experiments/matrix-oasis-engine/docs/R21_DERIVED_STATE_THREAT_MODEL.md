# R21派生状态威胁模型

- **权威倒置**：persona、memory和relationship均为只读派生状态，任何API不得写Runtime、Ledger、R19 policy或R20调度。
- **伪造投影证据**：R19 manifest只绑定artifact身份；R21必须先由锁定reducer生成artifact，再创建manifest并独立重放复验，不能信任调用者自带字节。
- **实体换身**：persona、policy scope和manifest scope必须与绑定Runtime实体目录重新核对；不存在、重复或跨actor引用一律fail closed。
- **拒绝事件污染**：memory只接纳actor自身accepted Action；relationship默认只聚合accepted Action。拒绝、损坏和陈旧Intent不能贡献派生状态。
- **隐式关系推断**：不得从文案、Action `entityIds`、persona或任意自由文本推断关系；只接受显式精确Action映射。
- **数值与顺序漂移**：relationship delta必须为有界安全整数并按连续Ledger revision折叠；溢出、重排、缺失或哈希漂移整体失败。
- **跨时间线污染**：R21只接受一个精确timeline及其完整Ledger；跨reset、跨timeline聚合或迁移一律拒绝。
- **虚假删除**：删除必须移除全部派生artifact/index；随后从同一Ledger重建并证明canonical字节一致。只删除manifest、缓存指针或局部条目不算通过。
- **Persona漂移**：persona是可信静态seed；Ledger、模型、运行期观察或旧投影均不得修改它。
- **外部服务与隐私面**：核心workspace不得读取环境变量、文件、网络或启动进程；不接外部索引、数据库、embedding或模型，不持久化自由文本。
- **范围漂移**：R19/R20实现、Creator、Godot、动态任务、事件生成和R22认知循环保持冻结。
