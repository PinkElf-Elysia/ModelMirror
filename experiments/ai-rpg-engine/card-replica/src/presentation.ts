// Presentation only: installation and session permissions are read from each host.
export const worlds = [
  {id:'douluo',name:'斗罗大陆',description:'觉醒武魂，装配魂环，在斗罗大陆写下自己的篇章。',genre:'玄幻 · 魂师',mark:'魂',art:''},
  {id:'blue-fish',name:'蓝色大肥鱼',description:'和吃白饭的蓝色大肥鱼一起写作，在独立编辑器里整理角色、世界与灵感。',genre:'创作 · Prompt 编辑器',mark:'鱼',art:'/rpg-app/blue-fish/logo.png'},
  {id:'lord-of-mysteries',name:'诡秘之主',description:'推开迷雾中的门，沿着非凡途径写下你的名字。',genre:'蒸汽 · 神秘',mark:'诡',art:'/rpg-app/lord-of-mysteries/fog-background.jpg'},
  {id:'ancient',name:'古代人生',description:'从市井到庙堂，在时代的潮水里作出自己的选择。',genre:'历史 · 人生',mark:'古',art:'/rpg-app/ancient/landscape.jpg'},
  {id:'earth',name:'地球 OL',description:'选择出身、天赋与命运，从一个新的起点出发。',genre:'都市 · 人生',mark:'世',art:''},
  {id:'god',name:'神临凡尘',description:'写下天书，成为凡尘中一个不平凡的存在。',genre:'神话 · 沙盒',mark:'神',art:''},
  {id:'cultivation',name:'超级修仙',description:'择一身根骨，游历三界，走出自己的长生路。',genre:'仙侠 · 修行',mark:'仙',art:''},
  {id:'rpg05',name:'行间',description:'换一种身份，走进下一场冒险。你的选择，写在行间。',genre:'多世界 · 冒险',mark:'行',art:''},
] as const;
export type WorldId = typeof worlds[number]['id'];
export const worldId=(id:string)=>id==='xingjian'?'rpg05':id;
export const internalCard=(id:string)=>id==='rpg05'?'xingjian':id;
export const cardRoot=(id:string)=>'/rpg-app/'+internalCard(id)+(['ancient','cultivation','lord-of-mysteries','blue-fish','douluo'].includes(id)?'/chat/':'/');
export const registryRoot=(id:string)=>['earth','god'].includes(id)?'/rpg-app/api/plugins':cardRoot(id)+'api/plugins';
export const validSession=(id:unknown):id is string=>typeof id==='string'&&/^(?:[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}|(?:branch|rewrite|import)-[a-f0-9]{64})$/i.test(id);
export const pluginInfo:Record<string,{name:string;group:string;icon:string;description:string;cost:string;detail:string}>={
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
