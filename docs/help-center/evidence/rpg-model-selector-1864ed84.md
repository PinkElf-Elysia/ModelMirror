# RPG 模型选择帮助验证

日期2026-09-19，基线1864ed8446346df1b77eb3da53f94d9e56b7c53f，B4候选。

文章：`client/src/content/help-center/articles/choose-rpg-model.md`。正式浏览器使用本地18443：市场安装、进入历史、会话授权、打开列表、品牌/搜索、点击选择、停用等指南步骤已操作。发送步骤在此前同一候选的合成 UI 链路实测，指南复走未重复发送。合成A首轮、父B续轮、分支A续轮三次；真实 Provider 0。指南和两张图片在浏览器加载正常。

截图源为正式浏览器原始JPEG，保存在 `docs/ai-rpg-experiment/model-selector/b4-{desktop,mobile}.jpg`。桌面原1280×720，手机原390×844；帮助副本只机械等比例转PNG：1000×562 / 750×1623，93717 / 127716字节；没有绘制或补画。截图为实际 UI 假控制面，不证明真实模型就绪。

原JPEG SHA256：
- desktop: 8a466a7a10de0d600f79c927740d78b10dba4df26da7aa58619db43456f3a1c2
- mobile: 7f50b13eb297e5f5840eab1f1a50800558288ccdccdab9f4303121e242a05d9f

帮助PNG SHA256：
- rpg-model-selector.png: 929647af34388709c92c4973dfc0d55b72d4804d38093e6ac2c6cef34b5a0677
- rpg-model-selector-mobile.png: 3612f4d912e911fe280d8e2530380af59294088381780ca48b5a6ea982009519

`npm.cmd run verify:help-images` 初次新增图片格式失败已修复。最终新增文章通过所有图片规则，仓库命令仍exit1：3个原有未引用资产b266b870/studio-beta-headings.jpg、eeb5bbd2/creator-cache.jpg、eeb5bbd2/studio-layout.jpg。HEAD原始副本执行相同脚本也只有这3项，基线18篇、候选19篇。导出的是git对象字节，不使用当前文件伪装基线；输出保存在 `.rpg04-work/model-selector-b4/help-baseline/baseline-result.txt`。未改检查器或删除旧图片。

受影响前端77项及父/卡片生产构建通过；完整 B4 结果、实际命令与人工待办见 `docs/ai-rpg-experiment/model-selector/B4.md`。


## PR前最新基线重放（2026-09-19）

用户已授权提交PR。git fetch后origin/main仍为1864ed8446346df1b77eb3da53f94d9e56b7c53f，与当前独立工作区HEAD完全一致（ahead0/behind0），因此当前独立工作区仍来自最新基线。重新启动18450前端/18451宿主、独立空数据目录model-selector-pr/preview；没有复用18443旧运行绑定或18449真实会话。启动工具仅改忽略副本的端口/数据路径，使用明确假Provider，无真实凭据/网络。

新origin浏览器状态为空，按现有教程可见操作完成：市场安装→地球卡历史打开林遥→安装未授权时模型按钮禁用→会话授权启用→填写草稿→搜索Claude→点击选择→草稿保留、首轮Gemini标签不变、余额未动→主动发送合成续轮→第二回合Claude→停用→选择按钮禁用且两回合/已选Claude保留。新增真实Provider0；合成预置1次+手动1次，余额20→18。关闭临时检查页，原真实验收页保留。UI与原同基线B4截图对应，不生成或伪造新截图。

PR前重新执行npm.cmd run verify:help-images，仍且仅有上述3个既有未引用JPEG失败；新文章两张PNG各项通过。B6帮助测试54/54和client生产构建已通过。当前运行代码与B6冻结一致，只有发布记录/帮助证据更新。该重放是UI合成证据，独立于B5三模型真实Provider及用户最终验收。
