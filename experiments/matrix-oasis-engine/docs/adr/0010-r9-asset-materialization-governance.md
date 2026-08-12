# ADR-0010：R9 Meshy优先资产物化治理

状态：接受

R9从冻结的R8 Scene Blueprint生成一个真实道具和一个静态人物占位GLB，使用成熟glTF工具链离线规范化，并以私有Prototype Asset Bundle承接到R10。环境继续复用冻结Kenney模板。

Marble因公共API不稳定提供HQ visual GLB而延后；R9不以collider GLB冒充视觉资产，也不引入SPZ/Splat或网页人工导出。真实Meshy操作分create、poll、download逐阶段审批，普通verify不产生费用。

该决策不修改冻结Runtime/Scene/Godot合同。R10才负责通用Blueprint布局和一键预览；逆序revert R9提交即可恢复完整R8代码，仓外供应商任务和资产按资格清单另行处理。
