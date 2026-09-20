# M1 B4 第2回合：等待人工审阅

用户“通过，继续”已记录为首份输出通过并继续第2回合，不外推为后续输出认可。独立额度已用3/4：必要认证1、RPG输出2，剩余1次。本次只派发1次，未重试、未再认证或刷新模型选择。

模型google/gemini-3.8-flash，参数temperature=0.7、top_p=0.8、max_tokens=16384；窗口1，开局资料补入关闭，沿用同一角色/路由/运行版本。未改产品源码、作者文本、世界书、模型参数或角色资料。
输入：我应声答应周芸，先把推车上最后三本文献逐一核对索书号、放回对应书架，再弯腰检查底层靠墙的板子，确认受潮的位置。

实际宿主请求与second-input-freeze逐字一致：同一system → 首轮完整user/assistant → 本轮前置词+输入+后置词。首轮保存的user没有重新包裹；当前前后置词各一次，原始历史和第1回合输出未改变。4条消息；selectedTurns=[1]，initialSnapshotAdded=false。因为发送前只有1个完整历史回合，本次仍未发生裁剪。

HTTP200，回执complete，retries=0，实际模型同名。输出22590字节、14784个UTF-16单位；Provider回报输入25866、输出7087 token。本回执不提供费用，未推算结算金额。
原文：experiments/ai-rpg-engine/.rpg04-work/history-window-b4/data/dispatches/earth/slot-2/response.txt
原文SHA256：1b1e98ea37d624edf34de3df5b57ead8e1ddd6ced30b55b5a0f91e878b119f5a
实际请求SHA256：5f72ef3e18ae4608e3b93c6c6dd6a52c456a29597fdf14141f77b4679af206d0
浏览器恢复记录后展示第2回合，打开其查看原文得到同一hash。两轮持久历史均逐字核对，没有修改或补写模型回复。内容质量和记忆衔接由用户审阅；尚不能判定窗口越界通过。

准备脚本node --check通过；运行绑定验证、冻结请求断言、实际wire逐字比较、原始history逐字比较、浏览器原文hash核对均通过。产品源码未改，不重复运行B3全套回归。B3既有帮助图片3项基线失败、真实旧数据副本测试跳过的边界继续保留。

上一份64个交付文件修改前已逐hash验证并复制到 experiments/ai-rpg-engine/.rpg04-work/history-window-b4/first-snapshot；B4-FIRST-DELIVERY、首份原始回执和B3证据未重写。当前文件登记B4-SECOND-DELIVERY。

验收入口 http://127.0.0.1:18461/rpg/earth，许澄第2回合。下一次必须先获本份人工认可；最后1次拟验证仅携带第2回合、排除第1回合后的续玩。当前停止派发，保留全部会话与账本。不Commit/Push/PR/共享部署。
