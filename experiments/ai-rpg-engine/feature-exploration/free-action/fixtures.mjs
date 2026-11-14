// Original adult-neutral fixtures. These snapshots never receive proposal writes.
export const BASELINE = 'fddf2877408477e7cb1a21f92a0eb2df8a2fe3c7';
const fixtures = {
  workshop: {
    id:'workshop', title:'雨前的临时工作场', version:1, session:'e1-workshop', branch:'fixture',
    description:'午后的云层渐厚。木架下放着书箱，入口朝向院子。你可以整理现有材料，先拟定一个遮雨或遮挡的方案。',
    actor:'成年旅人', history:[],
    objects:[
      {id:'canvas',name:'帆布',kind:'material',available:1,unit:'张',description:'可用于遮雨或遮挡。'},
      {id:'rope',name:'绳索',kind:'material',available:3,unit:'份',description:'可将帆布固定在支撑处。'},
      {id:'frame',name:'木架',kind:'support',description:'可以支撑临时遮盖。'},
      {id:'books',name:'书箱',kind:'target',description:'放在木架下，需要避雨。'},
      {id:'entrance',name:'入口',kind:'target',description:'可以设置临时遮挡。'}
    ],
    samples:[
      {label:'为书箱遮雨',text:'用帆布和绳子在木架上搭个挡雨棚，先遮住书箱。',action:'cover',selection:['canvas','rope','frame','books']},
      {label:'遮挡入口',text:'用帆布和绳子在入口设置遮挡。',action:'screen',selection:['canvas','rope','entrance']},
      {label:'试试不明确的目标',text:'用这些材料挡一下。',action:null,selection:['canvas','rope']},
      {label:'试试超出能力的要求',text:'搭一个永久抵御所有攻击的棚子。',action:null,selection:[]}
    ]
  },
  courtyard: {
    id:'courtyard',title:'庭院里的讨论',version:1,session:'e1-courtyard',branch:'fixture',
    description:'庭院可供短暂交谈。林舟和许宁是同行的成年旅人；是否应邀、何时到场，都还没有确定。',
    actor:'成年旅人',history:[],
    objects:[
      {id:'court',name:'庭院',kind:'place',description:'拟议讨论地点，尚未安排活动。'},
      {id:'lin',name:'林舟',kind:'person',description:'可提出邀请，未表示接受。',accepted:false},
      {id:'xu',name:'许宁',kind:'person',description:'可提出邀请，未表示接受。',accepted:false}
    ],
    samples:[{label:'拟定讨论与邀请',text:'把讨论安排在庭院，先发出邀请，不替任何人答应。',action:'invite',selection:['court','lin','xu']}]
  }
};
export const sceneIds = Object.keys(fixtures);
export function snapshot(id) {
  if (!Object.hasOwn(fixtures,id)) throw Object.assign(new Error('未知场景'),{code:'SCENE_UNKNOWN'});
  return structuredClone(fixtures[id]);
}
export const rules = Object.freeze({
  cover:{label:'搭设遮盖',goal:'先让书箱避雨',required:['canvas','rope','frame','books'],canvas:1,rope:2},
  screen:{label:'设置遮挡',goal:'遮挡入口',required:['canvas','rope','entrance'],canvas:1,rope:1},
  invite:{label:'安排讨论并提出邀请',goal:'在庭院讨论，先邀请再等待回应',required:['court'],canvas:0,rope:0}
});

