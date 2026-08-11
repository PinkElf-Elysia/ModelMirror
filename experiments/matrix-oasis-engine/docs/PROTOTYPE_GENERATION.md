# R8原型生成接口

R8接受纯文本，生成严格Generation Proposal。Proposal由冻结Authoring Game Pack和私有Scene Blueprint组成；成功后继续生成Runtime Pack、Receipt和脱敏generation report。

Scene Blueprint只表达环境提示、资产需求、逻辑区域、摆放意图和node可见关系。它不包含文件路径、哈希、供应商任务、3D坐标、凭据或原始用户提示，也不承诺成为长期公共格式。

普通验证只使用loopback假Provider。真实模型资格必须使用模型调用审批模板，并且不能自动延续到下一轮或资产供应商。
