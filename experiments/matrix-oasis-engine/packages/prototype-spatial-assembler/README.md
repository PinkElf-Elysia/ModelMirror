# @matrix-oasis/prototype-spatial-assembler

R11私有、离线的米制空间组合器。它不修改冻结Scene Pack，而是把R10 assembly report、Scene Pack和R11 Spatial Environment Bundle绑定成canonical Spatial Assembly。环境源原点按`entry-player-xz-v1`确定性对齐Runtime入口节点的Scene Pack玩家出生点X/Z；Y轴只应用Spatial Environment Bundle中已签署的平移与地面偏移。最终root变换和对齐证据均进入canonical assembly/report，不在Godot中加入隐藏偏移。

组合结果显式记录根平移/旋转、splat稳健中心补偿、独立的splat/collider尺度和panoramaVisible=false。Godot只能消费这些字段，不得以隐藏常量、人工试摆或panorama fallback覆盖校准。

本包不读取文件系统、环境变量或网络；调用方负责从已验证的仓外run中提供文本和字节。
