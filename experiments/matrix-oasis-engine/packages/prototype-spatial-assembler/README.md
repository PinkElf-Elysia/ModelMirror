# @matrix-oasis/prototype-spatial-assembler

R11私有、离线的米制空间组合器。它不修改冻结Scene Pack，而是把R10 assembly report、Scene Pack和R11 Spatial Environment Bundle绑定成canonical Spatial Assembly。

组合结果显式记录共同根平移/旋转、splat中心补偿、splat/collider尺度和panoramaVisible=false。Godot只能消费这些字段，不得以隐藏常量、人工试摆或panorama fallback覆盖校准。

本包不读取文件系统、环境变量或网络；调用方负责从已验证的仓外run中提供文本和字节。
