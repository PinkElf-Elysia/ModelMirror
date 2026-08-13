# @matrix-oasis/prototype-spatial-environment

R11私有、离线的Marble SPZ空间环境物化包。它先复验冻结的R10 Environment Bundle，再把受限SPZ确定性转换为compressed PLY，并绑定已有collider GLB与显式米制校准。

公开面只有合同常量、`materializePrototypeSpatialEnvironment`、`validatePrototypeSpatialEnvironmentBundleJson`和固定OperationalError。包不读取文件、环境变量或网络，不接触供应商凭据，也不持久化输入。

`rendererCenterCompensationMm`记录gdgs导入器减去高斯均值后需要恢复的平移。它与metric scale、ground offset、Godot平移/旋转一同进入canonical bundle和report；后续Godot组合器不得隐藏覆盖这些值。
