// Presentation only: installation and session permissions are read from each host.
export const worlds = [
  {id:'slime',name:'关于我转生成史莱姆这件事',description:'选择时间线、角色与能力，从朱拉大森林启程，写下自己的转生故事。',genre:'奇幻 · 转生',mark:'史',art:'/rpg-app/slime/background.jpg'},
  {id:'modern-life',name:'现代超真实人生',description:'从现代现实或自定世界出发，写下主角和相遇的人，让工作、金钱与关系随你的选择而变化。',genre:'现代 · 开放世界',mark:'生',art:'/rpg-app/modern-life/cover.jpg'},
  {id:'doupo',name:'斗破苍穹！',description:'从乌坦城到大千世界，在斗气大陆走出自己的传奇。',genre:'玄幻 · 斗气大陆',mark:'斗',art:'/rpg-app/doupo/cover.avif'},
  {id:'naruto',name:'火影大世界',description:'从忍者学校到忍界大战，选择篇章，写下自己的忍道。',genre:'忍界 · 羁绊',mark:'忍',art:'/rpg-app/naruto/world.jpg'},
  {id:'elderland',name:'西幻大世界',description:'艾尔德兰——剑与魔法的命运织机。它不关心你是谁，只关心你能成为谁。',genre:'西幻 · 剑与魔法',mark:'幻',art:'/rpg-app/elderland/background.jpg'},
  {id:'medieval',name:'中世纪真实人生',description:'公元四至十五世纪，选择时代、出身与技艺，开启属于自己的此生。',genre:'历史 · 中世纪',mark:'纪',art:'/rpg-app/medieval/background.jpg'},
  {id:'harry-potter',name:'哈利波特大世界（霍格沃兹）',description:'一封迟来的入学信，一段属于你的魔法人生。从选定年代到挥动魔杖，开启霍格沃茨的故事。',genre:'魔法 · 校园',mark:'魔',art:'/rpg-app/harry-potter/background.jpg'},
  {id:'jinyong',name:'金庸大世界',description:'群侠并世，江湖未定。选择一部传奇，或踏入群侠交汇的江湖。',genre:'武侠 · 群像',mark:'侠',art:'/rpg-app/jinyong/background.jpg'},
  {id:'ai-girls',name:'完蛋！我被AI娘包围了！',description:'八位 AI 娘搬进同一个屋檐下。从一份主角档案，开始热闹的日常。',genre:'都市 · AI 群像',mark:'AI',art:'/rpg-app/ai-girls/assets/xn1f6k.png'},
  {id:'douluo',name:'斗罗大陆',description:'觉醒武魂，装配魂环，在斗罗大陆写下自己的篇章。',genre:'玄幻 · 魂师',mark:'魂',art:''},
  {id:'dragon-raja',name:'龙族大世界',description:'从卡塞尔的来信开始，在龙与人的命运之间作出选择。',genre:'龙族 · 青春',mark:'龙',art:'/rpg-app/dragon-raja/background.jpg'},
  {id:'blue-fish',name:'蓝色大肥鱼',description:'和吃白饭的蓝色大肥鱼一起写作，在独立编辑器里整理角色、世界与灵感。',genre:'创作 · Prompt 编辑器',mark:'鱼',art:'/rpg-app/blue-fish/logo.png'},
  {id:'lord-of-mysteries',name:'诡秘之主',description:'推开迷雾中的门，沿着非凡途径写下你的名字。',genre:'蒸汽 · 神秘',mark:'诡',art:'/rpg-app/lord-of-mysteries/fog-background.jpg'},
  {id:'ancient',name:'古代人生',description:'从市井到庙堂，在时代的潮水里作出自己的选择。',genre:'历史 · 人生',mark:'古',art:'/rpg-app/ancient/landscape.jpg'},
  {id:'earth',name:'地球 OL',description:'选择出身、天赋与命运，从一个新的起点出发。',genre:'都市 · 人生',mark:'世',art:''},
  {id:'god',name:'神临凡尘',description:'写下天书，成为凡尘中一个不平凡的存在。',genre:'神话 · 沙盒',mark:'神',art:''},
  {id:'cultivation',name:'超级修仙',description:'择一身根骨，游历三界，走出自己的长生路。',genre:'仙侠 · 修行',mark:'仙',art:''},
  {id:'rpg05',name:'行间',description:'换一种身份，走进下一场冒险。你的选择，写在行间。',genre:'多世界 · 冒险',mark:'行',art:''},
  {id:'daomu',name:'盗墓笔记大世界',description:'长沙雨夜，盘口的煤油灯还亮着。选定篇章、身份与手艺，写下你的卷宗。',genre:'悬疑 · 自由探索',mark:'墓',art:'/rpg-app/daomu/cover.avif'},
] as const;
export type WorldId = typeof worlds[number]['id'];
export const worldId=(id:string)=>id==='xingjian'?'rpg05':id;
export const internalCard=(id:string)=>id==='rpg05'?'xingjian':id;
export const cardRoot=(id:string)=>'/rpg-app/'+internalCard(id)+(['ancient','cultivation','lord-of-mysteries','blue-fish','douluo','dragon-raja','jinyong','ai-girls','slime','modern-life','doupo','naruto','daomu','medieval','elderland','harry-potter'].includes(id)?'/chat/':'/');
export const registryRoot=(id:string)=>['earth','god'].includes(id)?'/rpg-app/api/plugins':cardRoot(id)+'api/plugins';
export const validSession=(id:unknown):id is string=>typeof id==='string'&&/^(?:[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}|(?:branch|rewrite|import)-[a-f0-9]{64})$/i.test(id);
export const pluginInfo:Record<string,{name:string;group:string;icon:string;description:string;cost:string;detail:string}>={
 'rpg.speech':{name:'沉浸语音',group:'创作',icon:'声',description:'选择音色，手动朗读一段故事。',cost:'点击生成后按语音模型计费',detail:'在已完成的回复旁点击朗读，可编辑提取文字、保存音色并查看音频历史。停用后已生成的音频仍可播放。'},
 'rpg.generation-parameters':{name:'生成参数',group:'对话',icon:'调',description:'分别调整剧情、摘要和记忆的生成参数。',cost:'保存设置不调用模型',detail:'仅影响当前会话的后续请求。更换剧情模型不会修改摘要或记忆配置。'},
 'rpg.model-comparison':{name:'多模型对比',group:'对话',icon:'比',description:'为本轮生成多份候选，采用一份继续故事。',cost:'每份候选分别计费',detail:'候选使用同一份上下文；只有采用的回复进入正式剧情。未采用的结果与调用记录保留。'},
 'rpg.model-selector':{name:'模型选择',group:'对话',icon:'M',description:'为下一条消息换一个模型。',cost:'选择不调用模型',detail:'选择保存在当前会话。停用后保留已选模型，后续发送按该模型计费。'},
 'rpg.branch-save':{name:'分支存档',group:'对话',icon:'⑂',description:'从某个回合，试试另一种选择。',cost:'创建分支不调用模型',detail:'保留原对话，在新对话继续。新分支的插件可以单独启用。'},
 'rpg.message-tools':{name:'消息工具',group:'对话',icon:'↻',description:'复制回复、编辑输入，或重新生成。',cost:'重新生成会调用模型',detail:'编辑与重生成在新分支完成，原剧情保留。支持查看生成中的文字。'},
 'rpg.history-window':{name:'历史窗口',group:'对话',icon:'◷',description:'选择续写时携带的近期回合。',cost:'设置不调用模型',detail:'完整记录仍然保留。启用自动总结时，使用总结的上下文策略。'},
 'rpg.rolling-summary':{name:'自动总结 2.0',group:'记忆',icon:'摘',description:'每轮记下小结，定期汇成大总结。',cost:'每轮小结 1 次，合并时另 1 次',detail:'每积满 20 条小总结合并一次。可选择模型、编辑内容，完整原文保留。'},
 'rpg.memory-palace':{name:'记忆宫殿',group:'记忆',icon:'忆',description:'记住人物、地点与走过的路。',cost:'回复后额外 1 次记忆调用',detail:'按关键词找回相关经历。可编辑和保护条目，不裁剪聊天记录。'},
 'rpg.illustrations':{name:'场景插画',group:'创作',icon:'画',description:'把这一幕，留成一张画。',cost:'主动改写或生成时计费',detail:'启用后在消息旁显示插画入口。可编辑画面描述；改写提示词和生成图片分别计费。'},
 'rpg.transcript':{name:'聊天记录',group:'创作',icon:'文',description:'导出旅程，或接着导入的故事聊。',cost:'导入导出不调用模型',detail:'导入会创建独立会话，不覆盖现有对话。续写时才调用模型。'},
};
export const pluginGroups=['对话','记忆','创作'];
