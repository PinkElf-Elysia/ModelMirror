# @matrix-oasis/prototype-spatial-assembler

R11私有、离线的米制空间组合器。它不修改冻结Scene Pack，而是把R10 assembly report、Scene Pack和R11 Spatial Environment Bundle绑定成canonical Spatial Assembly。环境源原点按`entry-player-xz-v1`确定性对齐Runtime入口节点的Scene Pack玩家出生点X/Z；Y轴以已校准collider地面为零面，避免在collider已经落地后再次叠加供应商ground offset。原始ground offset仍进入canonical source bundle和assembly report，最终root变换和对齐证据不依赖Godot隐藏偏移。

组合结果显式记录根平移/旋转、splat稳健中心补偿、独立的splat/collider尺度和panoramaVisible=false。Godot只能消费这些字段，不得以隐藏常量、人工试摆或panorama fallback覆盖校准。

默认v1组合行为保持不变。R12新增可选`matrix-oasis.prototype-spatial-assembly/2`：它只接受R10 assembly v2，从环境 collider 的全局 AABB 和近地水平三角面生成一米固定网格，排除靠近垂直障碍的候选点，再按原声明锚点顺序确定最多六个非环境 placement；容量不足时静态拒绝，不使用案例坐标或人工试摆。外部安全边界使用 `collider-global-aabb-floor-grid-v1`，槽位及其地面高度进入 canonical assembly/report，Godot 据此放置并继续按 mesh 全局 AABB 落地。

本包不读取文件系统、环境变量或网络；调用方负责从已验证的仓外run中提供文本和字节。
