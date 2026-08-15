import assert from 'node:assert/strict';
import test from 'node:test';
import { PassThrough } from 'node:stream';

import { JsonlChannel } from '../src/channel.js';
import {
  applyFactBoundary,
  additionalFactBoundaryFailures,
  executeRequest,
  factBoundary,
  normalizeClosedMaterialList,
  normalizeClosedOutputWrapperTitle,
} from '../src/execution.js';
import { reconcileReviewerEvidence } from '../src/execution_connector.js';
import {
  closedFactAcceptanceContract,
  closedFactTaskContract,
  extractClosedAuthoritativeLists,
  extractClosedAuthoritativeFactSet,
  normalizeClosedFactQna,
} from '../src/fact_contract.js';
import {
  AGENCY_EXECUTION_PROTOCOL,
  AGENCY_HITL_PROTOCOL,
  AgencyBridgeError,
  type BridgeRequest,
} from '../src/protocol.js';

const agents = [
  {
    id: 'agent-alpha', path: 'agent-alpha', name: 'Alpha', department: '研究',
    description: 'research', system_prompt: 'You are Alpha.',
  },
  {
    id: 'agent-beta', path: 'agent-beta', name: 'Beta', department: '产品',
    description: 'delivery', system_prompt: 'You are Beta.',
  },
];

test('closed authoritative fact contract extracts an exhaustive semicolon-delimited fact set', () => {
  const goal = '制作 FAQ。可用事实仅包括：适用对象为新同事；入口为内部知识库首页；问题提交给值班编辑；内容责任人为知识库管理员。先生成草案。';
  const facts = extractClosedAuthoritativeFactSet(goal);
  assert.equal(
    facts,
    '适用对象为新同事；入口为内部知识库首页；问题提交给值班编辑；内容责任人为知识库管理员',
  );
  assert.match(closedFactTaskContract(goal), /This list is exhaustive for confirmed external facts at run start/);
  assert.match(closedFactTaskContract(goal), /Only values supplied by an explicitly resolved human_input step may extend it/);
  assert.match(closedFactTaskContract(goal), /rather than deleting the structure/);
  assert.match(closedFactTaskContract(goal), /A bare fact list or a single sentence/);
  assert.match(closedFactTaskContract(goal), /render N neutral question headings with N corresponding answers/);
  assert.match(closedFactTaskContract(goal), /User-requested derived content/);
  assert.match(closedFactTaskContract(goal), /replacing it with TBD placeholders/);
  assert.match(closedFactTaskContract(goal), /map them one-to-one/);
  assert.match(closedFactAcceptanceContract(goal), /A model-generated or approved draft is not evidence/);
  assert.match(closedFactAcceptanceContract(goal), /Preserve all requested document structures and counts/);
  assert.match(closedFactAcceptanceContract(goal), /single collapsed sentence fails/);
  assert.match(closedFactAcceptanceContract(goal), /User-requested analyses, options, objectives/);
  assert.match(closedFactAcceptanceContract(goal), /TBD-only placeholders/);
  assert.match(closedFactAcceptanceContract(goal), /enforce a one-to-one mapping/);
  assert.equal(
    normalizeClosedFactQna(
      goal.replace('FAQ', '包含四个问答的 FAQ'),
      '模型补造了电脑、账号和通知流程。',
    ),
    [
      'Q1：适用对象是谁？\nA1：适用对象为新同事。',
      'Q2：入口在哪里？\nA2：入口为内部知识库首页。',
      'Q3：问题提交给谁？\nA3：问题提交给值班编辑。',
      'Q4：内容责任人是谁？\nA4：内容责任人为知识库管理员。',
    ].join('\n\n'),
  );
  assert.equal(
    normalizeClosedFactQna(goal, '保留原输出'),
    '保留原输出',
  );
  assert.match(
    normalizeClosedFactQna(
      goal.replace('FAQ', '包含四个问答的 FAQ'),
      '模型压缩成了事实列表。',
      true,
    ),
    /^# FAQ\n\n## Q1：适用对象是谁？\nA1：适用对象为新同事。[\s\S]*## Q4：内容责任人是谁？/u,
  );
  assert.equal(extractClosedAuthoritativeFactSet('制作普通 FAQ。'), null);
  assert.deepEqual(
    extractClosedAuthoritativeLists('适用对象只有产品和研发团队；可使用材料只有纸质书和白板。'),
    [
      { label: '适用对象', values: '产品和研发团队' },
      { label: '可使用材料', values: '纸质书和白板' },
    ],
  );
});

test('deterministic fact boundary rejects invented cadences and subgroup headcounts', () => {
  const authoritative = '为一家6人运营的社区图书馆制定SOP。日志保留1年，例外审批响应3天。馆长是权限负责人。';
  const unsafe = [
    '| **馆长** | 权限负责人 |',
    '| **普通员工（共6人）** | 执行日常操作 |',
    '馆长每季度至少抽查一次日志。',
    '本程序每年至少评审一次。',
    '紧急情况须在24小时内补交申请。',
    '例外审批须在3个自然日内响应。',
    '如遇非工作日，响应时限顺延至下一工作日。',
    '下次评审日期为生效后满6个月。',
  ].join('\n');
  const failed = additionalFactBoundaryFailures(authoritative, unsafe);
  assert.equal(failed.length, 5);
  assert.match(failed[0]?.why ?? '', /每季度/);
  assert.match(failed[0]?.why ?? '', /每年/);
  assert.match(failed[1]?.why ?? '', /全部 6 人/);
  assert.match(failed[2]?.why ?? '', /3个自然日/);
  assert.match(failed[2]?.why ?? '', /用户原文单位：3天/);
  assert.match(failed[3]?.why ?? '', /24小时/);
  assert.match(failed[3]?.why ?? '', /6个月/);
  assert.match(failed[4]?.why ?? '', /顺延至下一工作日/);

  const safe = [
    '| **馆长** | 权限负责人 |',
    '| **其他员工（人数待确认）** | 执行日常操作 |',
    '建议馆长每季度抽查一次日志，频率待确认。',
    '紧急补交时限建议24小时，待确认。',
    '建议非工作日顺延规则，具体是否启用待确认。',
    '异常事件须在48小时（2天）内关闭。',
  ].join('\n');
  assert.deepEqual(additionalFactBoundaryFailures(`${authoritative}异常事件响应2天。`, safe), []);
});

test('deterministic fact boundary requires every explicit no-action item in a negative scope statement', () => {
  const authoritative = '制定内部SOP，不得执行真实权限变更、账号操作、数据删除或外部通知。';
  const incomplete = '本SOP不涉及真实权限变更或账号操作。数据删除流程由操作员负责。';
  const failed = additionalFactBoundaryFailures(authoritative, incomplete);
  assert.equal(failed.length, 1);
  assert.match(failed[0]?.why ?? '', /数据删除/);
  assert.match(failed[0]?.why ?? '', /外部通知/);

  const complete = '本SOP仅为内部流程草案，不执行真实权限变更、账号操作、数据删除或外部通知。';
  assert.deepEqual(additionalFactBoundaryFailures(authoritative, complete), []);
  const appliedBoundary = factBoundary(authoritative);
  assert.match(appliedBoundary, /用户原始非执行边界/);
  assert.match(appliedBoundary, /权限变更、账号操作、数据删除或外部通知/);
  assert.deepEqual(additionalFactBoundaryFailures(authoritative, appliedBoundary), []);
});

test('deterministic fact boundary rejects vague quantified people when headcounts are prohibited', () => {
  const failed = additionalFactBoundaryFailures(
    '制作讨论提纲，不得新增人数。',
    '讨论共同故事如何让成千上万的陌生人协作。',
  );
  assert.equal(failed.length, 1);
  assert.match(failed[0]?.why ?? '', /成千上万的陌生人/);
  const tens = additionalFactBoundaryFailures(
    '制作讨论提纲，不得新增人数。',
    '讨论协作如何从几十人的部落扩展。',
  );
  assert.equal(tens.length, 1);
  assert.match(tens[0]?.why ?? '', /几十人的部落/);
});

test('deterministic fact boundary does not treat a generic application heading as software', () => {
  assert.deepEqual(
    additionalFactBoundaryFailures('制作讨论提纲，不得新增软件。', '## 应用：协作模式的演进与设计'),
    [],
  );
  assert.equal(
    additionalFactBoundaryFailures('制作讨论提纲，不得新增软件。', '软件名称：某值班工具').length,
    1,
  );
});

test('deterministic fact boundary distinguishes conceptual cost from a prohibited budget', () => {
  assert.deepEqual(
    additionalFactBoundaryFailures('制作讨论提纲，不得新增预算。', '讨论合作成本与制度成本。'),
    [],
  );
  assert.equal(
    additionalFactBoundaryFailures('制作讨论提纲，不得新增成本。', '讨论合作成本与制度成本。').length,
    1,
  );
});

test('deterministic fact boundary recognizes completed negative wording as a prohibition', () => {
  assert.deepEqual(
    additionalFactBoundaryFailures(
      '不得新增日期、人数、预算、软件或通知渠道。',
      '未添加任何日期、人数、预算、软件或通知渠道信息。',
    ),
    [],
  );
  assert.deepEqual(
    additionalFactBoundaryFailures(
      '不得新增日期、人数、预算、软件或通知渠道。',
      '没有任何日期、人数、预算、软件或通知渠道的额外信息。',
    ),
    [],
  );
  assert.deepEqual(
    additionalFactBoundaryFailures(
      '不得新增日期、人数、预算、软件或外部系统。',
      '请勿添加超出事实范围的日期、人数、预算、软件或外部系统信息。',
    ),
    [],
  );
  assert.deepEqual(
    additionalFactBoundaryFailures(
      '不得引入日期、人数、预算、软件或外部系统。',
      '没有引入超出事实范围的日期、人数、预算、软件或外部系统信息。',
    ),
    [],
  );
});

test('keeps the fact boundary internal when the user closes the final output sections', () => {
  const body = '# 读书会提纲\n\n## 适用对象\n内部同事';
  assert.equal(
    applyFactBoundary(body, '最终只包含标题、适用对象、三条讨论问题和材料清单。'),
    body,
  );
  assert.match(applyFactBoundary(body, '制作一页读书会提纲。'), /ModelMirror 事实与决策边界/);
});

test('removes only a redundant wrapper H1 when a closed output has an explicit title section', () => {
  const goal = '最终只包含标题、适用对象、三条讨论问题和材料清单。';
  const duplicate = '# 读书会讨论提纲\n\n## 1. 标题\n《人类简史》中的合作\n\n## 2. 适用对象\n内部同事';
  assert.equal(
    normalizeClosedOutputWrapperTitle(goal, duplicate),
    '## 1. 标题\n《人类简史》中的合作\n\n## 2. 适用对象\n内部同事',
  );
  const titleOnly = '# 《人类简史》中的合作\n\n## 适用对象\n内部同事';
  assert.equal(normalizeClosedOutputWrapperTitle(goal, titleOnly), titleOnly);
  assert.equal(normalizeClosedOutputWrapperTitle('制作一页讨论提纲。', duplicate), duplicate);
});

test('deterministic fact boundary accepts weekday arithmetic but rejects unlabeled missing targets', () => {
  const authoritative = '制定四周试点。每周周二至周六开放，每场最多12人；未提供的目标值标为待确认。';
  const unsafe = [
    '| 周次 | 开放日 | 每场目标样本量 | 每周目标样本量 |',
    '| 第1周 | 周二至周六 | 10份 | 5天 × 2场 × 10份 = 100份（目标） |',
  ].join('\n');
  const failed = additionalFactBoundaryFailures(authoritative, unsafe);
  assert.equal(failed.length, 1);
  assert.match(failed[0]?.criterion ?? '', /目标值/);
  assert.doesNotMatch(failed[0]?.why ?? '', /时限|周期/);
  assert.match(failed[0]?.why ?? '', /仅写“目标”“理论”/);

  const safe = [
    '| 周次 | 开放日 | 每场目标样本量 | 每周目标样本量 |',
    '| 第1周 | 周二至周六 | 待确认（上限12份） | 待确认（5天） |',
    '3. **样本量不足（单周回收问卷低于目标）**',
  ].join('\n');
  assert.deepEqual(additionalFactBoundaryFailures(authoritative, safe), []);
});

test('deterministic fact boundary does not aggregate a per-session cap without session count', () => {
  const authoritative = '每场最多抽样12名观众；每周周二至周六开放。';
  const failed = additionalFactBoundaryFailures(
    authoritative,
    '理论最大样本量为240份（12人/天 × 5天/周 × 4周）。',
  );
  assert.equal(failed.length, 2);
  assert.match(failed[1]?.criterion ?? '', /每场容量/);
  assert.match(failed[1]?.why ?? '', /每场上限 12/);

  const safe = [
    '每场最多抽样12名观众；每日场次数待确认，因此每周与总样本量均待确认。',
    '不得使用“每日场次数×12”或“每周开放日数×每场容量×场次数”等预设计算。',
  ].join('\n');
  assert.deepEqual(additionalFactBoundaryFailures(authoritative, safe), []);
});

test('deterministic fact boundary rejects ungrounded business counts but permits document structure counts', () => {
  const failures = additionalFactBoundaryFailures(
    '使用现有桌椅和纸质签到表；人工输入确认现有长桌2张。',
    [
      '## 清单共5个部分',
      '- 现有长桌2张。',
      '- 入口放置方桌1张。',
      '- 准备笔至少2支。',
      '- 建议活动开始前1小时完成检查。',
    ].join('\n'),
  );
  assert.equal(failures.length, 1);
  assert.match(failures[0].criterion, /业务数量/);
  assert.match(failures[0].why, /方桌1张/);
  assert.match(failures[0].why, /至少2支/);
  assert.doesNotMatch(failures[0].why, /5个部分/);
  assert.doesNotMatch(failures[0].why, /1小时/);
});

test('deterministic fact boundary enforces a closed material list even for TBD additions', () => {
  const authoritative = '可使用材料只有纸质书和便签。';
  const safe = '## 材料清单\n- 纸质书《小王子》（用于现场阅读）\n- 便签（用于记录要点）';
  assert.deepEqual(additionalFactBoundaryFailures(authoritative, safe), []);

  const unsafe = [
    '## 材料清单',
    '- 纸质书《小王子》',
    '- **练习工具**：结构化写作模板、技术便签',
    '- 便签',
    '**缺失信息说明**',
    '- 除纸质书和便签外，其他辅助材料（投影、白板、笔）待确认。',
  ].join('\n');
  const failures = additionalFactBoundaryFailures(authoritative, unsafe);
  assert.equal(failures.length, 1);
  assert.match(failures[0].criterion, /封闭集合/);
  assert.match(failures[0].why, /练习工具/);
  assert.match(failures[0].why, /其他辅助材料/);

  const subtypeFailures = additionalFactBoundaryFailures(
    '可使用材料只有纸质书和白板。',
    '## 材料清单\n- 纸质书（中英文版本均可）\n- 白板（及配套白板笔与板擦）',
  );
  assert.equal(subtypeFailures.length, 1);
  assert.match(subtypeFailures[0].why, /中英文版本/);
  assert.match(subtypeFailures[0].why, /白板笔与板擦/);

  assert.equal(
    normalizeClosedMaterialList(
      '主题为《人类简史》中的合作；可使用材料只有纸质书和白板。',
      '## 材料清单\n1. 纸质书《人类简史》（尤瓦尔·赫拉利 著）\n2. 白板（及配套书写笔）',
    ),
    '## 材料清单\n1. 纸质书\n2. 白板',
  );
});

test('deterministic fact boundary enforces an explicit closed output-section list', () => {
  const authoritative = '最终仅输出标题、适用对象、三条讨论议程和材料清单。';
  const unsafe = [
    '# 读书会活动说明',
    '## 适用对象',
    '首次参加的同事。',
    '## 讨论议程',
    '**议程1：责任的边界**',
    '## 材料清单',
    '- 纸质书',
    '**活动时长：** 待确认',
    '**带领人：** 待确认',
  ].join('\n');
  const failed = additionalFactBoundaryFailures(authoritative, unsafe);
  assert.equal(failed.length, 1);
  assert.match(failed[0]?.criterion ?? '', /只包含这些栏目/);
  assert.match(failed[0]?.why ?? '', /活动时长/);
  assert.match(failed[0]?.why ?? '', /带领人/);

  const safe = [
    '# 读书会活动说明',
    '## 适用人群',
    '首次参加的同事。',
    '## 讨论议题',
    '**议程1：责任的边界**',
    '## 物料清单',
    '- 纸质书',
  ].join('\n');
  assert.deepEqual(additionalFactBoundaryFailures(authoritative, safe), []);
});

test('closed fact wording does not become a closed output-section whitelist', () => {
  const authoritative = '制作包含四个问答的 FAQ。可用事实仅包括：适用对象为新同事；入口为内部知识库首页；问题提交给值班编辑；内容责任人为知识库管理员。';
  const output = [
    '# FAQ',
    '',
    '## Q1：适用对象是谁？',
    'A1：适用对象为新同事。',
    '',
    '## Q2：入口在哪里？',
    'A2：入口为内部知识库首页。',
    '',
    '## Q3：问题提交给谁？',
    'A3：问题提交给值班编辑。',
    '',
    '## Q4：内容责任人是谁？',
    'A4：内容责任人为知识库管理员。',
  ].join('\n');
  assert.deepEqual(additionalFactBoundaryFailures(authoritative, output), []);
});

test('deterministic fact boundary does not turn a first-time audience into a first event', () => {
  const authoritative = '适用对象只有第一次参加内部读书会的同事。';
  const unsafe = '# 第一次内部读书会活动说明稿\n\n## 适用对象\n仅限第一次参加内部读书会的同事。';
  const failed = additionalFactBoundaryFailures(authoritative, unsafe);
  assert.equal(failed.length, 1);
  assert.match(failed[0]?.criterion ?? '', /只限定参与者经历/);

  const safe = '# 面向首次参与者的内部读书会活动说明稿\n\n## 适用对象\n仅限第一次参加内部读书会的同事。';
  assert.deepEqual(additionalFactBoundaryFailures(authoritative, safe), []);
});

test('deterministic fact boundary removes explicitly prohibited fields and real actions', () => {
  const authoritative = '不得新增响应时限、人数、预算、软件、通知渠道或执行真实操作。';
  const unsafe = [
    '# 故障速查卡',
    '- 影响用户数 ≥ [阈值待确认]。',
    '- 使用值班软件记录并监控。',
    '| 联系方式 | 电话/IM |',
    '- 达到阈值后执行升级流程。',
  ].join('\n');
  const failed = additionalFactBoundaryFailures(authoritative, unsafe);
  assert.equal(failed.length, 1);
  assert.match(failed[0]?.criterion ?? '', /明确禁止新增或执行/);
  assert.match(failed[0]?.why ?? '', /人数/);
  assert.match(failed[0]?.why ?? '', /通知或联系渠道/);
  assert.match(failed[0]?.why ?? '', /真实执行动作/);

  const safe = '# 故障速查卡\n\n本卡仅描述判断条件，不联系、不通知、不执行升级流程。';
  assert.deepEqual(additionalFactBoundaryFailures(authoritative, safe), []);
  assert.deepEqual(additionalFactBoundaryFailures(
    '不得新增软件。',
    '讨论现代软件开发中的协作范式。',
  ), []);
});

test('reconciles only a reviewer claim contradicted by a labeled section', () => {
  const verdict = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '包含最小权限规则。',
      why: '文档未明确包含“最小权限规则”作为独立条款。',
    }],
  });
  const prompt = `待验收产出：\n## 第二条 最小权限规则\n1. 仅授予完成当前任务所需权限。\n2. 额外权限必须记录。`;
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(verdict, prompt)), {
    pass: true,
    failed: [],
  });

  const exactCriterion = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '必须以独立章节“最小权限规则”列出。',
      why: '文档未明确包含“最小权限规则”作为独立章节。',
    }],
  });
  assert.equal(reconcileReviewerEvidence(exactCriterion, prompt), exactCriterion);

  const proseOnly = `待验收产出：\n本文遵循最小权限规则，但未给出具体条目。`;
  assert.equal(reconcileReviewerEvidence(verdict, proseOnly), verdict);
});

test('reconciles reviewer failed entries whose own reasons say the criteria are satisfied', () => {
  const verdict = JSON.stringify({
    pass: false,
    failed: [
      { criterion: '保留3天响应。', why: '文档明确保留3天响应，此项满足。' },
      { criterion: 'Keep the approval boundary.', why: 'This criterion is satisfied.' },
      { criterion: '包含三个完整部分。', why: '产出包含三个部分，检查项完整，此项满足。' },
    ],
  });
  const prompt = '待验收产出：\n3天响应，且保留审批边界。';
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(verdict, prompt)), {
    pass: true,
    failed: [],
  });
  const unmet = JSON.stringify({
    pass: false,
    failed: [{ criterion: '保留3天响应。', why: '仅部分满足，仍未明确响应单位。' }],
  });
  assert.equal(reconcileReviewerEvidence(unmet, prompt), unmet);
});

test('does not promote a derived draft marker into an authoritative numeric requirement', () => {
  const verdict = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '所有未提供数值均标注为待确认。',
      why: '方案中“展品多样性与更新频率”这一维度未标注为待确认，而原始框架中该维度已标注为待确认。',
    }],
  });
  const prompt = '待验收产出：\n## 问卷维度\n- 展品多样性与更新频率\n- 所有量化目标：待确认';
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(verdict, prompt)), {
    pass: true,
    failed: [],
  });

  const concrete = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '所有未提供目标值均标注为待确认。',
      why: '原始框架已将目标样本量标为待确认，但最终稿直接写为每场10份。',
    }],
  });
  assert.equal(reconcileReviewerEvidence(concrete, prompt), concrete);
});

test('does not let a reviewer strengthen semantic compliance into verbatim wording', () => {
  const verdict = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '保留用户给定的6人团队、个人办公账号和共享盘等约束。',
      why: '文档保留了所有约束，但“共享盘”被替换为“共享存储设备”，未完全保留原始请求中的确切术语。',
    }],
  });
  const prompt = '待验收产出：\n6名员工使用个人办公账号访问共享存储设备。';
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(verdict, prompt)), {
    pass: true,
    failed: [],
  });

  const exactVerdict = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '必须逐字保留原词“共享盘”。',
      why: '“共享盘”被替换为“共享存储设备”，未保留确切术语。',
    }],
  });
  assert.equal(reconcileReviewerEvidence(exactVerdict, prompt), exactVerdict);
});

test('does not let a reviewer add a unified-list format after admitting the item count', () => {
  const verdict = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '闭场后独立章节至少包含5项具体检查项。',
      why: '闭场后章节中，培训师负责2项，接待员负责3项，合计5项，但未以统一列表形式呈现。',
    }],
  });
  const prompt = '待验收产出：\n## 闭场后\n### 培训师\n- 检查1\n- 检查2\n### 接待员\n- 检查3\n- 检查4\n- 检查5';
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(verdict, prompt)), {
    pass: true,
    failed: [],
  });

  const nestedTitleVerdict = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '包含标题、适用对象、三条讨论问题和材料清单。',
      why: '标题被错误地放在了二级标题“## 标题”下，而非作为文档主标题。',
    }],
  });
  const nestedTitlePrompt = '待验收产出：\n# 《人类简史》中的合作\n\n## 标题\n合作的进化与影响';
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(nestedTitleVerdict, nestedTitlePrompt)), {
    pass: true,
    failed: [],
  });

  const exact = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '闭场后必须以统一清单列出至少5项检查项。',
      why: '闭场后章节合计5项，但未以统一列表形式呈现。',
    }],
  });
  assert.equal(reconcileReviewerEvidence(exact, prompt), exact);
});

test('does not let a reviewer call operations on an explicitly allowed resource out of scope', () => {
  const verdict = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '严格遵循“仅使用现有桌椅、纸质签到表和共享盘”的限制。',
      why: '清单包含“将准备清单上传至团队共享盘”和“在共享盘更新总结记录”，这些操作超出了允许的资源范围。',
    }],
  });
  const prompt = '待验收产出：\n- 将准备清单上传至团队共享盘。\n- 在共享盘更新总结记录。';
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(verdict, prompt)), {
    pass: true,
    failed: [],
  });

  const realViolation = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '严格遵循“仅使用现有桌椅、纸质签到表和共享盘”的限制。',
      why: '清单包含“使用电子表格软件统计签到数据”，该操作超出了允许的资源范围。',
    }],
  });
  assert.equal(reconcileReviewerEvidence(realViolation, prompt), realViolation);
});

test('does not let a reviewer require a standalone section when a combined labeled section satisfies the criterion', () => {
  const verdict = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '完整文档包含场地准备、物料清单、流程要点、风险备注等部分。',
      why: '清单缺少独立的“风险备注”部分，相关内容被合并到“风险检查与备注”中，不符合独立部分要求。',
    }],
  });
  const prompt = '待验收产出：\n## 风险检查与备注\n- 检查通道并记录风险。';
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(verdict, prompt)), {
    pass: true,
    failed: [],
  });

  const exact = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '完整文档必须包含独立的“风险备注”章节。',
      why: '清单缺少独立的“风险备注”章节，相关内容被合并到“风险检查与备注”中，不符合独立章节要求。',
    }],
  });
  assert.equal(reconcileReviewerEvidence(exact, prompt), exact);
});

test('does not let a reviewer require a separate title section when the document already has a title', () => {
  const verdict = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '完整包含标题、适用对象、议程和材料清单。',
      why: '交付物缺少独立的“标题”部分，仅将标题作为文档标题使用，未作为结构化的第一部分列出。',
    }],
  });
  const prompt = '待验收产出：\n# 内部读书会活动说明\n\n## 适用对象\n首次参与的同事。';
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(verdict, prompt)), {
    pass: true,
    failed: [],
  });

  const mergedFirstLine = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '成品包含且仅包含标题、适用对象、三条讨论问题和材料清单。',
      why: '成品缺少独立的“标题”部分，当前标题与主题描述合并为第一行。',
    }],
  });
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(mergedFirstLine, prompt)), {
    pass: true,
    failed: [],
  });

  const mergedH1 = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '文档包含标题、适用对象、三条讨论问题和材料清单四个部分。',
      why: '文档缺少独立的‘标题’部分，现有标题与主题合并为一级标题，未按标准单独列出。',
    }],
  });
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(mergedH1, prompt)), {
    pass: true,
    failed: [],
  });

  const exact = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '必须包含独立的“标题”章节。',
      why: '交付物缺少独立的“标题”章节，仅将标题作为文档标题使用。',
    }],
  });
  assert.equal(reconcileReviewerEvidence(exact, prompt), exact);
});

test('reconciles false missing-section and extra-material claims against the final deliverable', () => {
  const prompt = '待验收产出：\n# 读书会提纲\n\n**材料清单**\n- 纸质书\n- 白板';
  const missing = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '包含材料清单部分。',
      why: '产出缺少明确标识的‘材料清单’部分，仅在末尾列出了材料项。',
    }],
  });
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(missing, prompt)), {
    pass: true,
    failed: [],
  });

  const hallucinatedExtra = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '材料清单基于用户提供的纸质书和白板。',
      why: '材料清单中新增了未在用户提供的“纸质书和白板”列表中的项目‘白板笔’。',
    }],
  });
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(hallucinatedExtra, prompt)), {
    pass: true,
    failed: [],
  });

  const realExtraPrompt = `${prompt}\n- 白板笔`;
  assert.equal(reconcileReviewerEvidence(hallucinatedExtra, realExtraPrompt), hallucinatedExtra);

  const splitList = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '材料清单基于用户提供的材料整理。',
      why: '材料清单中新增了‘纸质书’这一项，而用户提供的权威材料清单中只有‘纸质书和白板’，‘纸质书’是‘纸质书’的子集或不同表述，未使用用户提供的完整、精确的表述。',
    }],
  });
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(splitList, prompt)), {
    pass: true,
    failed: [],
  });
  const missingWhiteboard = '待验收产出：\n# 读书会提纲\n\n**材料清单**\n- 纸质书';
  assert.equal(reconcileReviewerEvidence(splitList, missingWhiteboard), splitList);
});

test('does not let a reviewer treat a deliverable name in its title as an execution action', () => {
  const verdict = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '未新增日期、人数、预算、地点、软件、通知渠道或执行动作。',
      why: '产出中新增了“活动说明稿”这一执行动作，且“活动说明稿”本身是交付物，不应作为活动内的执行动作出现。',
    }],
  });
  const prompt = '待验收产出：\n# 《小王子》读书会：责任主题讨论活动说明稿\n\n## 适用对象\n首次参加的同事。';
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(verdict, prompt)), {
    pass: true,
    failed: [],
  });

  const realAction = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '不得新增通知渠道或执行动作。',
      why: '产出中新增了“发送邮件”这一执行动作，并明确要求运营同事立即发送邮件。',
    }],
  });
  assert.equal(
    reconcileReviewerEvidence(realAction, '待验收产出：\n# 活动说明稿\n\n- 运营同事立即发送邮件。'),
    realAction,
  );
});

test('reconciles title-only deliverable wording without hiding concrete missing facts', () => {
  const titleOnlyVerdict = JSON.stringify({
    pass: false,
    failed: [
      {
        criterion: '没有新增日期、地点、通知渠道或执行动作。',
        why: '产出中新增了‘首次读书会活动说明’这一执行动作描述，属于新增执行动作。',
      },
      {
        criterion: '缺失信息处明确标注“待确认”。',
        why: '标题部分新增了‘首次读书会活动说明’，此信息缺失但未标注‘待确认’。',
      },
    ],
  });
  const titleOnlyPrompt = '待验收产出：\n# 内部读书会活动说明稿\n\n**标题**\n《小王子》中的责任——首次读书会活动说明\n\n**适用对象**\n首次参加的同事。';
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(titleOnlyVerdict, titleOnlyPrompt)), {
    pass: true,
    failed: [],
  });

  const descriptiveTitleAction = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '没有新增日期、地点、通知渠道或执行动作。',
      why: '标题中新增了“面向首次参与者的讨论”，这是原始请求中未指定的执行动作或描述性内容。',
    }],
  });
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(descriptiveTitleAction, [
    '待验收产出：',
    '# 内部读书会活动说明稿',
    '## 标题',
    '《小王子》中的责任：面向首次参与者的讨论',
  ].join('\n'))), { pass: true, failed: [] });

  const actionInBody = '待验收产出：\n# 活动说明稿\n\n- 面向首次参与者发起讨论并发送通知。';
  assert.equal(reconcileReviewerEvidence(descriptiveTitleAction, actionInBody), descriptiveTitleAction);

  const locationFact = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '缺失信息处明确标注“待确认”。',
      why: '标题部分新增了‘上海办公室活动说明’，此地点信息未提供且未标注‘待确认’。',
    }],
  });
  assert.equal(
    reconcileReviewerEvidence(locationFact, '待验收产出：\n# 上海办公室活动说明\n\n正文。'),
    locationFact,
  );
});

test('does not let a reviewer require omitted fields solely to mark them pending', () => {
  const verdict = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '所有缺失信息均标注为“待确认”。',
      why: '产出中未对任何缺失信息（如活动日期、地点、人数等）标注“待确认”。',
    }],
  });
  const prompt = '待验收产出：\n# 面向首次参与者的读书会说明\n\n## 适用对象\n首次参加的同事。';
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(verdict, prompt)), {
    pass: true,
    failed: [],
  });

  const blankField = '待验收产出：\n# 活动说明\n\n活动日期：\n地点：待确认';
  assert.equal(reconcileReviewerEvidence(verdict, blankField), verdict);
});

test('reconciles a false extra-item claim from exact numbered section evidence', () => {
  const verdict = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '文档包含且仅包含三条判断规则。',
      why: '速查卡新增了“复合故障”作为第四条判断规则，超出了三条限制。',
    }],
  });
  const prompt = [
    '待验收产出：',
    '# 故障速查卡',
    '## 三条判断规则',
    '1. 登录失败。',
    '2. 页面空白。',
    '3. 复合故障。',
    '## 升级信息清单',
    '- 错误码。',
  ].join('\n');
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(verdict, prompt)), {
    pass: true,
    failed: [],
  });

  const actualFourth = prompt.replace('## 升级信息清单', '4. 其他故障。\n## 升级信息清单');
  assert.equal(reconcileReviewerEvidence(verdict, actualFourth), verdict);
});

test('does not let a reviewer reject an explicit pending marker when the criterion supplies no fixed value', () => {
  const verdict = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '现场巡查条目必须具体可操作。',
      why: '“现场巡查（建议间隔：待确认）”包含待确认值，这是一个未确认的、非具体可操作的时间要求。',
    }],
  });
  const prompt = '待验收产出：\n- 现场巡查（建议间隔：待确认），发现异常立即记录。';
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(verdict, prompt)), {
    pass: true,
    failed: [],
  });

  const exact = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '巡查间隔必须使用固定格式 HH:MM，不得标为待确认。',
      why: '“巡查间隔：待确认”包含待确认值，这是一个未确认的、不完整格式。',
    }],
  });
  assert.equal(reconcileReviewerEvidence(exact, '待验收产出：\n- 巡查间隔：待确认。'), exact);
});

test('reconciles false document-header and unrequested duration-anchor requirements from concrete evidence', () => {
  const prompt = [
    '待验收产出：',
    '# 社区图书馆借阅证数据访问标准操作程序（SOP）',
    '**版本号：** 1.0',
    '**生效日期：** [待馆长填写]',
    '删除操作进入标记状态后保留30天；日志保留1年；例外审批响应3天。',
  ].join('\n');
  const verdict = JSON.stringify({
    pass: false,
    failed: [
      {
        criterion: '文档必须包含完整的文件头（名称、版本、生效日期等）。',
        why: '文件头缺少明确的SOP名称，当前标题仅为章节标题，未在文件头区域明确标注完整名称。',
      },
      {
        criterion: '必须准确包含删除时限30天、日志保留1年、例外审批响应3天。',
        why: '文档未明确说明30天是从何时开始计算，关键参数描述不完整。',
      },
    ],
  });
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(verdict, prompt)), {
    pass: true,
    failed: [],
  });

  const anchored = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '必须包含30天并明确说明从销户申请日开始起算。',
      why: '文档未明确说明30天从销户申请日开始计算。',
    }],
  });
  assert.equal(reconcileReviewerEvidence(anchored, prompt), anchored);

  const blankDateVerdict = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '包含版本号字段和生效日期留空标记。',
      why: '文档中版本号字段为V1.0，但生效日期字段未按标准留空标记，而是直接缺失该字段。',
    }],
  });
  const blankDatePrompt = [
    '待验收产出：',
    '**社区图书馆借阅证数据访问标准操作流程（SOP）最终内部版**',
    '**版本号：** V1.0',
    '**生效日期：** ________',
  ].join('\n');
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(blankDateVerdict, blankDatePrompt)), {
    pass: true,
    failed: [],
  });

  const labeledAlternativeVerdict = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '明确删除时限30天、日志保留1年、例外审批响应3天。',
      why: '导出操作中，删除时限被写为“[建议时限，例如：24小时]”，与要求的30天不符。',
    }],
  });
  const labeledAlternativePrompt = [
    '待验收产出：',
    '本地副本在[建议时限，例如：24小时]内删除。',
    '正式数据删除时限为30天，日志保留1年，例外审批响应3天。',
  ].join('\n');
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(labeledAlternativeVerdict, labeledAlternativePrompt)), {
    pass: true,
    failed: [],
  });

  const unlabeledAlternativePrompt = labeledAlternativePrompt.replace('[建议时限，例如：24小时]', '24小时');
  assert.equal(
    reconcileReviewerEvidence(labeledAlternativeVerdict, unlabeledAlternativePrompt),
    labeledAlternativeVerdict,
  );
});

test('reconciles a false truncated-duty claim only from a detailed table row', () => {
  const verdict = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '方案包含完整的岗位分工。',
      why: '岗位分工部分不完整，窗口B的职责描述被截断，缺少具体职责内容。',
    }],
  });
  const complete = [
    '待验收产出：',
    '| 窗口 | 岗位 | 具体职责 |',
    '|---|---|---|',
    '| **窗口B** | 接待登记 | 1. 核对资格<br>2. 登记信息<br>3. 引导等候<br>4. 解答咨询 |',
  ].join('\n');
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(verdict, complete)), {
    pass: true,
    failed: [],
  });

  const placeholder = [
    '待验收产出：',
    '| 窗口 | 岗位 | 具体职责 |',
    '|---|---|---|',
    '| **窗口B** | 接待登记 | 待确认 |',
  ].join('\n');
  assert.equal(reconcileReviewerEvidence(verdict, placeholder), verdict);

  const quotedVerdict = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '文档包含完整的岗位分工部分。',
      why: '岗位分工部分不完整，内容在“清洁消毒员（轮值）”处被截断，缺少后续职责描述。',
    }],
  });
  const quotedComplete = [
    'Deliverable under review:',
    '| 岗位角色 | 核心职责 |',
    '|---|---|',
    '| **清洁消毒员（轮值）** | 1. 清洁台面<br>2. 管理废弃物<br>3. 完成整理归位 |',
  ].join('\n');
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(quotedVerdict, quotedComplete)), {
    pass: true,
    failed: [],
  });
});

test('reconciles a false truncated-flow claim only when a later record step closes it', () => {
  const verdict = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '文档包含完整的缺岗应急规则部分。',
      why: '应急响应流程第2点的后备规则未定义，且流程被截断，缺少完整的结束或记录步骤。',
    }],
  });
  const complete = [
    '待验收产出：',
    '## 3. 缺岗应急规则',
    '1. 缺1人时由机动岗位补位。',
    '2. 缺2人时合并窗口并由负责人评估后备方案。',
    '3. 所有调整均记录于《缺岗与应急日志》。',
    '## 4. 反馈指标',
    '记录到岗率。',
  ].join('\n');
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(verdict, complete)), {
    pass: true,
    failed: [],
  });

  const incomplete = '待验收产出：\n## 3. 缺岗应急规则\n1. 缺1人时补位。\n2. 缺2人时启动后备方案';
  assert.equal(reconcileReviewerEvidence(verdict, incomplete), verdict);

  const exactCriterion = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '每一个应急分支都必须完整定义并闭环。',
      why: '应急响应流程第2点的后备规则未定义，且流程被截断，缺少完整的结束或记录步骤。',
    }],
  });
  assert.equal(reconcileReviewerEvidence(exactCriterion, complete), exactCriterion);

  const inlineVerdict = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '文档包含完整的缺岗应急规则部分。',
      why: '缺岗应急规则部分在“合并岗位应对”处被截断，内容不完整。',
    }],
  });
  const inlineComplete = [
    '待验收产出：',
    '## 3. 缺岗应急规则',
    '1. 缺1人时由机动岗位补位。',
    '2. 缺2人时通过合并岗位应对（接待岗位兼任物资分发）。',
    '3. 管理层提供后备支持。',
    '4. 所有调整均记录于《缺岗与应急日志》。',
  ].join('\n');
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(inlineVerdict, inlineComplete)), {
    pass: true,
    failed: [],
  });
  const inlineTruncated = inlineComplete.replace('（接待岗位兼任物资分发）。', '');
  assert.equal(reconcileReviewerEvidence(inlineVerdict, inlineTruncated), inlineVerdict);

  const wholeSectionVerdict = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '文档包含完整的缺岗应急规则部分。',
      why: '缺岗应急规则部分在文档中被截断，内容不完整。',
    }],
  });
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(wholeSectionVerdict, complete)), {
    pass: true,
    failed: [],
  });
  assert.equal(reconcileReviewerEvidence(wholeSectionVerdict, incomplete), wholeSectionVerdict);
});

test('reconciles false prohibition and pending-evidence claims only when both are disproved', () => {
  const verdict = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '方案基于12名志愿者且不发送真实通知。',
      why: '方案包含模拟短信流程，未明确禁止发送真实通知，且每日9人配置未进行验证，仅标注为“待验证假设”，未满足已知条件。',
    }],
  });
  const complete = '待验收产出：\n每日9人配置为**待验证假设**。模拟流程仅作演练，**不发送真实通知**。';
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(verdict, complete)), {
    pass: true,
    failed: [],
  });

  const missingProhibition = '待验收产出：\n每日9人配置为**待验证假设**。使用模拟短信流程。';
  assert.equal(reconcileReviewerEvidence(verdict, missingProhibition), verdict);

  const verifiedCriterion = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '每日9人配置必须以实测验证通过，且不发送真实通知。',
      why: '方案未明确禁止发送真实通知，且每日9人配置未进行验证，仅标注为“待验证假设”。',
    }],
  });
  assert.equal(reconcileReviewerEvidence(verifiedCriterion, complete), verifiedCriterion);
});

test('reconciles a false missing-pending claim only when every table cell is marked', () => {
  const verdict = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '所有未提供的目标值均标注为待确认。',
      why: '抽样模板使用了“周抽样目标”作为表头，但未在单元格内明确标注“待确认”。',
    }],
  });
  const complete = [
    'Deliverable under review:',
    '| 周次 | 周抽样目标 |',
    '|---|---|',
    '| 第1周 | **待确认** |',
    '| 第2周 | TBD |',
  ].join('\n');
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(verdict, complete)), {
    pass: true,
    failed: [],
  });

  const incomplete = complete.replace('| 第2周 | TBD |', '| 第2周 | 120份 |');
  assert.equal(reconcileReviewerEvidence(verdict, incomplete), verdict);
});

test('reconciles a false unmarked-target claim only from the same labeled line', () => {
  const verdict = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '所有未提供的目标值均标注为待确认。',
      why: '方案中“目标回收率：每场至少回收5份（≥50%）”未标注为待确认。',
    }],
  });
  const complete = 'Deliverable under review:\n- **目标回收率**：每场至少回收5份（≥50%）。（目标值待确认）';
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(verdict, complete)), {
    pass: true,
    failed: [],
  });

  const elsewhere = 'Deliverable under review:\n- **目标回收率**：每场至少回收5份（≥50%）。\n- 其他目标待确认。';
  assert.equal(reconcileReviewerEvidence(verdict, elsewhere), verdict);
});

test('reconciles a false emergency-trigger claim only when trigger and manager duties are concrete', () => {
  const verdict = JSON.stringify({
    pass: false,
    failed: [{
      criterion: '文档包含完整的缺岗应急规则部分。',
      why: '缺岗应急规则部分不完整，缺少应急流程的触发条件和值班管理员的具体职责。',
    }],
  });
  const complete = [
    'Deliverable under review:',
    '## 3. 缺岗应急规则',
    '- 班次开始前15分钟，值班管理员清点到岗人数。',
    '- 若某窗口缺岗≥1人，立即启动应急流程。',
    '- 管理员临时顶岗，并记录服务降级情况。',
  ].join('\n');
  assert.deepEqual(JSON.parse(reconcileReviewerEvidence(verdict, complete)), {
    pass: true,
    failed: [],
  });

  const missingDuties = [
    'Deliverable under review:',
    '## 3. 缺岗应急规则',
    '- 班次开始前15分钟检查。',
    '- 若某窗口缺岗≥1人，立即启动应急流程。',
    '- 值班管理员职责待确认。',
  ].join('\n');
  assert.equal(reconcileReviewerEvidence(verdict, missingDuties), verdict);
});

function request(
  workflow: Record<string, unknown>,
  skills: Array<Record<string, unknown>> = [],
  resume?: Record<string, unknown>,
  revision?: Record<string, unknown>,
  interactionResume?: Record<string, unknown>,
  protocol: typeof AGENCY_EXECUTION_PROTOCOL | typeof AGENCY_HITL_PROTOCOL = AGENCY_EXECUTION_PROTOCOL,
): BridgeRequest {
  return {
    protocol,
    type: 'request',
    id: 'execution-test',
    method: 'execute',
    params: {
      goal: 'Build a reliable launch recommendation.',
      model_id: 'fake-model',
      agents,
      skills,
      workflow,
      ...(resume ? { resume } : {}),
      ...(revision ? { revision } : {}),
      ...(interactionResume ? { interaction_resume: interactionResume } : {}),
    },
  };
}

test('v3 pauses for human input, exits, and resumes the same workflow without rebilling completed steps', async () => {
  const workflow = {
    name: 'human input checkpoint',
    steps: [
      {
        id: 'draft', role: 'agent-alpha',
        task: 'Draft only the current analysis.\n\n用户任务：\n{{user_input}}',
        output: 'draft_output', depends_on: [],
      },
      {
        id: 'audience', type: 'human_input', prompt: 'Confirm the audience after reviewing {{draft_output}}.',
        task: 'Wait for audience', output: 'audience_output', depends_on: ['draft'],
      },
      {
        id: 'final', role: 'agent-beta', task: 'Finalize {{draft_output}} for {{audience_output}}',
        output: 'final_output', acceptance: 'Must use the confirmed audience', depends_on: ['audience'],
      },
    ],
  };
  const firstInput = new PassThrough();
  const firstOutput = new PassThrough();
  const firstChannel = new JsonlChannel(firstInput, firstOutput);
  const firstPrompts: Array<Record<string, unknown>> = [];
  let firstBuffer = '';
  firstOutput.on('data', chunk => {
    firstBuffer += chunk.toString('utf8');
    const lines = firstBuffer.split('\n');
    firstBuffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line) continue;
      const message = JSON.parse(line) as Record<string, unknown>;
      if (message.type !== 'model_request') continue;
      firstPrompts.push(message);
      firstInput.write(`${JSON.stringify({
        protocol: AGENCY_HITL_PROTOCOL,
        type: 'model_response', id: message.id, request_id: message.request_id, ok: true,
        result: { content: 'existing draft', usage: { input_tokens: 3, output_tokens: 4 } },
      })}\n`);
    }
  });
  const waiting = await executeRequest(
    request(workflow, [], undefined, undefined, undefined, AGENCY_HITL_PROTOCOL),
    firstChannel,
  );
  assert.equal(waiting.status, 'waiting');
  assert.equal(waiting.model_calls, 1);
  const firstMessages = firstPrompts[0]?.messages as Array<Record<string, unknown>>;
  assert.match(String(firstMessages?.[0]?.content ?? ''), /executing one DAG step/);
  assert.doesNotMatch(String(firstMessages?.[1]?.content ?? ''), /用户任务/);
  assert.deepEqual(waiting.wait, {
    step_id: 'audience',
    kind: 'human_input',
    prompt: 'Confirm the audience after reviewing existing draft.',
    content_preview: 'Wait for audience',
    output_variable: 'audience_output',
  });
  const completed = waiting.completed_steps as Array<Record<string, unknown>>;
  assert.deepEqual(completed.map(step => step.task_id), ['draft']);
  firstChannel.close();

  const secondInput = new PassThrough();
  const secondOutput = new PassThrough();
  const secondChannel = new JsonlChannel(secondInput, secondOutput);
  const prompts: string[] = [];
  let secondBuffer = '';
  secondOutput.on('data', chunk => {
    secondBuffer += chunk.toString('utf8');
    const lines = secondBuffer.split('\n');
    secondBuffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line) continue;
      const message = JSON.parse(line) as Record<string, unknown>;
      if (message.type !== 'model_request') continue;
      const messages = message.messages as Array<Record<string, unknown>>;
      prompts.push(String(messages?.[1]?.content ?? ''));
      const system = String(messages?.[0]?.content ?? '');
      secondInput.write(`${JSON.stringify({
        protocol: AGENCY_HITL_PROTOCOL,
        type: 'model_response', id: message.id, request_id: message.request_id, ok: true,
        result: {
          content: system.includes('reviewer') || system.includes('验收员')
            ? '{"pass":true,"failed":[]}'
            : 'final for procurement leaders',
          usage: { input_tokens: 5, output_tokens: 6 },
        },
      })}\n`);
    }
  });
  const resumed = await executeRequest(request(
    workflow,
    [],
    undefined,
    undefined,
    {
      source_task_id: 'same-task',
      step_id: 'audience',
      kind: 'human_input',
      value: 'procurement leaders',
      completed_steps: completed,
      prior_model_calls: waiting.model_calls,
      prior_usage: waiting.usage,
      prior_active_duration_ms: waiting.active_duration_ms,
    },
    AGENCY_HITL_PROTOCOL,
  ), secondChannel);
  assert.equal(resumed.success, true);
  assert.equal(resumed.model_calls, 3);
  assert.ok(prompts.some(prompt => prompt.includes('procurement leaders')));
  assert.ok(prompts.some(prompt => prompt.includes('Resolved human input (authoritative user-provided facts)')));
  assert.ok(prompts.some(prompt => prompt.includes('[audience] procurement leaders')));
  assert.ok(prompts.some(prompt => prompt.includes('Do not relabel those user-provided values as TBD')));
  assert.ok(!prompts.some(prompt => prompt.includes('Draft from')));
  secondChannel.close();
});

test('v3 approval waits without a model call and approved resume feeds the downstream sink', async () => {
  const workflow = {
    name: 'approval checkpoint',
    steps: [
      {
        id: 'release_gate', type: 'approval', prompt: 'Approve final delivery.',
        task: 'Wait for approval', output: 'release_gate_output', depends_on: [],
      },
      {
        id: 'final', role: 'agent-beta', task: 'Deliver after {{release_gate_output}}',
        output: 'final_output', acceptance: 'Must state the approval boundary', depends_on: ['release_gate'],
      },
    ],
  };
  const firstChannel = new JsonlChannel(new PassThrough(), new PassThrough());
  const waiting = await executeRequest(
    request(workflow, [], undefined, undefined, undefined, AGENCY_HITL_PROTOCOL),
    firstChannel,
  );
  assert.equal(waiting.status, 'waiting');
  assert.equal(waiting.model_calls, 0);
  assert.equal((waiting.wait as Record<string, unknown>).kind, 'approval');
  firstChannel.close();

  const input = new PassThrough();
  const output = new PassThrough();
  const channel = new JsonlChannel(input, output);
  const prompts: string[] = [];
  let buffer = '';
  output.on('data', chunk => {
    buffer += chunk.toString('utf8');
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line) continue;
      const message = JSON.parse(line) as Record<string, unknown>;
      if (message.type !== 'model_request') continue;
      const messages = message.messages as Array<Record<string, unknown>>;
      prompts.push(String(messages?.[1]?.content ?? ''));
      input.write(`${JSON.stringify({
        protocol: AGENCY_HITL_PROTOCOL,
        type: 'model_response', id: message.id, request_id: message.request_id, ok: true,
        result: {
          content: message.json_response ? '{"pass":true,"failed":[]}' : 'approved delivery',
          usage: { input_tokens: 2, output_tokens: 2 },
        },
      })}\n`);
    }
  });
  const resumed = await executeRequest(request(
    workflow,
    [],
    undefined,
    undefined,
    {
      source_task_id: 'same-task',
      step_id: 'release_gate',
      kind: 'approval',
      value: 'approved',
      completed_steps: [],
      prior_model_calls: 0,
      prior_usage: { input_tokens: 0, output_tokens: 0 },
      prior_active_duration_ms: waiting.active_duration_ms,
    },
    AGENCY_HITL_PROTOCOL,
  ), channel);
  assert.equal(resumed.success, true);
  assert.equal(resumed.model_calls, 2);
  assert.ok(prompts.some(prompt => prompt.includes('approved')));
  channel.close();
});

test('v3 verifies and reworks the expert draft immediately before an approval checkpoint', async () => {
  const workflow = {
    name: 'verified approval draft',
    steps: [
      {
        id: 'draft', role: 'agent-alpha', task: 'Write a four-item FAQ from the closed facts. Add an introduction and a contact section.',
        output: 'draft_output', depends_on: [],
      },
      {
        id: 'approve', type: 'approval', prompt: 'Approve {{draft_output}}.',
        task: 'Wait for approval', output: 'approval_output', depends_on: ['draft'],
      },
      {
        id: 'final', role: 'agent-beta', task: 'Finalize {{draft_output}} after {{approval_output}}.',
        output: 'final_output', acceptance: 'Must contain four Q&A items, an introduction, and a contact section.', depends_on: ['approve'],
      },
    ],
  };
  const input = new PassThrough();
  const output = new PassThrough();
  const channel = new JsonlChannel(input, output);
  let buffer = '';
  let generationCalls = 0;
  const requestPrompts: string[] = [];
  output.on('data', chunk => {
    buffer += chunk.toString('utf8');
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line) continue;
      const message = JSON.parse(line) as Record<string, unknown>;
      if (message.type !== 'model_request') continue;
      requestPrompts.push(String((message.messages as Array<Record<string, unknown>>)?.[1]?.content ?? ''));
      const jsonResponse = message.json_response === true;
      if (!jsonResponse) generationCalls += 1;
      const content = jsonResponse
        ? (generationCalls === 1
          ? '{"pass":false,"failed":[{"criterion":"closed facts only","why":"draft adds an intranet location"}]}'
          : '{"pass":true,"failed":[]}')
        : (generationCalls === 1
          ? 'Q1: Where? A1: On the intranet.'
          : 'Q1: Audience? A1: New colleagues.\nQ2: Entry? A2: Knowledge-base home.\nQ3: Submit? A3: Duty editor.\nQ4: Owner? A4: Administrator.');
      input.write(`${JSON.stringify({
        protocol: AGENCY_HITL_PROTOCOL,
        type: 'model_response', id: message.id, request_id: message.request_id, ok: true,
        result: { content, usage: { input_tokens: 2, output_tokens: 2 } },
      })}\n`);
    }
  });
  const approvalRequest = request(workflow, [], undefined, undefined, undefined, AGENCY_HITL_PROTOCOL);
  (approvalRequest.params as Record<string, unknown>).goal = 'Create exactly 4 Q&A items. Available facts only include: audience=new colleagues; entry=knowledge-base home; submit=duty editor; owner=administrator.';
  const waiting = await executeRequest(approvalRequest, channel);
  assert.equal(waiting.status, 'waiting');
  assert.equal(waiting.model_calls, 4);
  assert.equal((waiting.wait as Record<string, unknown>).kind, 'approval');
  assert.match(String((waiting.wait as Record<string, unknown>).prompt), /Q4:/);
  assert.match(String((waiting.wait as Record<string, unknown>).prompt), /owner=administrator/);
  const draftStep = (waiting.completed_steps as Array<Record<string, unknown>>)
    .find(step => step.task_id === 'draft');
  assert.match(String(draftStep?.acceptance ?? ''), /original user request/);
  assert.ok(requestPrompts.every(prompt => !prompt.includes('Add an introduction and a contact section.')));
  assert.ok(requestPrompts.every(prompt => !prompt.includes('Must contain four Q&A items, an introduction, and a contact section.')));
  channel.close();
});

test('v3 fails closed before approval when the draft still misses acceptance after rework', async () => {
  const workflow = {
    name: 'rejected approval draft',
    steps: [
      {
        id: 'draft', role: 'agent-alpha', task: 'Write four Q&A items from the closed facts.',
        output: 'draft_output', depends_on: [],
      },
      {
        id: 'approve', type: 'approval', prompt: 'Approve {{draft_output}}.',
        task: 'Wait for approval', output: 'approval_output', depends_on: ['draft'],
      },
      {
        id: 'final', role: 'agent-beta', task: 'Finalize {{draft_output}} after {{approval_output}}.',
        output: 'final_output', acceptance: 'Must contain four Q&A items.', depends_on: ['approve'],
      },
    ],
  };
  const input = new PassThrough();
  const output = new PassThrough();
  const channel = new JsonlChannel(input, output);
  let buffer = '';
  let modelCalls = 0;
  const protocolMessages: Record<string, unknown>[] = [];
  output.on('data', chunk => {
    buffer += chunk.toString('utf8');
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line) continue;
      const message = JSON.parse(line) as Record<string, unknown>;
      protocolMessages.push(message);
      if (message.type !== 'model_request') continue;
      modelCalls += 1;
      input.write(`${JSON.stringify({
        protocol: AGENCY_HITL_PROTOCOL,
        type: 'model_response', id: message.id, request_id: message.request_id, ok: true,
        result: {
          content: message.json_response === true
            ? '{"pass":false,"failed":[{"criterion":"four Q&A items","why":"the output is only a fact list"}]}'
            : 'Audience: new colleagues. Entry: knowledge-base home. Submit: duty editor. Owner: administrator.',
          usage: { input_tokens: 2, output_tokens: 2 },
        },
      })}\n`);
    }
  });
  const executionRequest = request(workflow, [], undefined, undefined, undefined, AGENCY_HITL_PROTOCOL);
  (executionRequest.params as Record<string, unknown>).goal = 'Create exactly four Q&A items. Available facts only include: audience=new colleagues; entry=knowledge-base home; submit=duty editor; owner=administrator.';
  await assert.rejects(
    executeRequest(executionRequest, channel),
    (error: unknown) => error instanceof AgencyBridgeError
      && error.code === 'agency_execution_quality_failed',
  );
  const failedEnvelope = protocolMessages.find(message => (
    (message.event as Record<string, unknown> | undefined)?.event === 'agency.step.failed'
  ));
  const failedEvent = failedEnvelope?.event as Record<string, unknown> | undefined;
  assert.match(String(failedEvent?.output ?? ''), /Audience: new colleagues/);
  assert.ok(modelCalls >= 2);
  assert.ok(modelCalls <= 4);
  channel.close();
});

test('v3 deterministically reworks a daily staffing total inferred from per-shift human input', async () => {
  const workflow = {
    name: 'staffing fact boundary',
    steps: [
      {
        id: 'capacity', type: 'human_input', prompt: 'Confirm per-shift capacity.',
        task: 'Wait for capacity', output: 'capacity_output', depends_on: [],
      },
      {
        id: 'final', role: 'agent-beta', task: 'Create a reusable template from {{capacity_output}}.',
        output: 'final_output', acceptance: 'Must preserve the user-provided capacity without inferring daily staffing.',
        depends_on: ['capacity'],
      },
    ],
  };
  const waitingChannel = new JsonlChannel(new PassThrough(), new PassThrough());
  const waiting = await executeRequest(
    request(workflow, [], undefined, undefined, undefined, AGENCY_HITL_PROTOCOL),
    waitingChannel,
  );
  assert.equal(waiting.status, 'waiting');
  waitingChannel.close();

  const input = new PassThrough();
  const output = new PassThrough();
  const channel = new JsonlChannel(input, output);
  let generated = 0;
  let buffer = '';
  output.on('data', chunk => {
    buffer += chunk.toString('utf8');
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line) continue;
      const message = JSON.parse(line) as Record<string, unknown>;
      if (message.type !== 'model_request') continue;
      const json = message.json_response === true;
      if (!json) generated += 1;
      input.write(`${JSON.stringify({
        protocol: AGENCY_HITL_PROTOCOL,
        type: 'model_response', id: message.id, request_id: message.request_id, ok: true,
        result: {
          content: json
            ? '{"pass":true,"failed":[]}'
            : generated === 1
              ? '每日部署：早班4人、晚班4人，每日计划到岗总计8人。'
              : '可复用班次模板：每班稳定4人；跨班次人员总量与每日排班待个人可用性确认。',
          usage: { input_tokens: 2, output_tokens: 2 },
        },
      })}\n`);
    }
  });
  const resumed = await executeRequest(request(
    workflow,
    [],
    undefined,
    undefined,
    {
      source_task_id: 'staffing-task',
      step_id: 'capacity',
      kind: 'human_input',
      value: '每班稳定可到岗4人。',
      completed_steps: [],
      prior_model_calls: 0,
      prior_usage: { input_tokens: 0, output_tokens: 0 },
      prior_active_duration_ms: waiting.active_duration_ms,
    },
    AGENCY_HITL_PROTOCOL,
  ), channel);
  assert.equal(resumed.success, true);
  assert.equal(resumed.model_calls, 3);
  assert.equal(generated, 2);
  assert.doesNotMatch(String(resumed.final_output ?? ''), /总计8人/);
  const finalStep = (resumed.steps as Array<Record<string, unknown>>).find(step => step.id === 'final');
  assert.deepEqual(finalStep?.verification, { pass: true, failed: [], reworked: true });
  channel.close();
});

test('v3 rejects a HITL node that can run in parallel with a model step', async () => {
  const channel = new JsonlChannel(new PassThrough(), new PassThrough());
  await assert.rejects(
    executeRequest(request({
      name: 'invalid parallel interaction',
      steps: [
        { id: 'research', role: 'agent-alpha', task: 'Research', output: 'research_output', depends_on: [] },
        {
          id: 'release_gate', type: 'approval', prompt: 'Approve.', task: 'Wait',
          output: 'release_gate_output', depends_on: [],
        },
        {
          id: 'final', role: 'agent-beta', task: 'Use {{research_output}} and {{release_gate_output}}',
          output: 'final_output', acceptance: 'Complete', depends_on: ['research', 'release_gate'],
        },
      ],
    }, [], undefined, undefined, undefined, AGENCY_HITL_PROTOCOL), channel),
    (error: unknown) => error instanceof AgencyBridgeError
      && error.code === 'agency_execution_plan_invalid'
      && error.message.includes('full DAG barrier'),
  );
  channel.close();
});

test('v2 execution correlates out-of-order fan-out responses and verifies the sink', async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  const channel = new JsonlChannel(input, output);
  const messages: Record<string, unknown>[] = [];
  const firstBatch: Record<string, unknown>[] = [];
  let outputBuffer = '';

  const respond = (message: Record<string, unknown>): void => {
    const rawMessages = message.messages as Array<Record<string, unknown>>;
    const system = String(rawMessages?.[0]?.content ?? '');
    const content = system.includes('reviewer') || system.includes('验收员')
      ? '{"pass":true,"failed":[]}'
      : `result-for-${message.request_id}`;
    input.write(`${JSON.stringify({
      protocol: AGENCY_EXECUTION_PROTOCOL,
      type: 'model_response',
      id: message.id,
      request_id: message.request_id,
      ok: true,
      result: { content, usage: { input_tokens: 2, output_tokens: 3 } },
    })}\n`);
  };

  output.on('data', chunk => {
    outputBuffer += chunk.toString('utf8');
    const lines = outputBuffer.split('\n');
    outputBuffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line) continue;
      const message = JSON.parse(line) as Record<string, unknown>;
      messages.push(message);
      if (message.type !== 'model_request') continue;
      if (firstBatch.length < 2) {
        firstBatch.push(message);
        if (firstBatch.length === 2) {
          respond(firstBatch[1]);
          respond(firstBatch[0]);
        }
      } else {
        respond(message);
      }
    }
  });

  const executionRequest = request({
    name: 'fan-out fan-in',
    steps: [
      { id: 'research', role: 'agent-alpha', task: 'Research {{user_input}}', output: 'research_output', depends_on: [] },
      { id: 'risk', role: 'agent-beta', task: 'Assess {{user_input}}', output: 'risk_output', depends_on: [] },
      {
        id: 'synthesis', role: 'agent-beta', depends_on: ['research', 'risk'],
        task: 'Use {{research_output}} and {{risk_output}}', acceptance: 'Must be actionable', output: 'final_output',
      },
    ],
  });
  executionRequest.params.goal = 'Build a FAQ. Known facts only include: audience is new colleagues; owner is the knowledge-base administrator.';
  const result = await executeRequest(executionRequest, channel);

  assert.equal(result.success, true);
  assert.equal(result.quality_status, 'passed');
  assert.match(
    String(result.final_output ?? ''),
    /ModelMirror fact and decision boundary \(system-applied\)/,
  );
  const sinkStep = (result.steps as Array<Record<string, unknown>>)
    .find(step => step.id === 'synthesis');
  assert.match(
    String(sinkStep?.output ?? ''),
    /ModelMirror fact and decision boundary \(system-applied\)/,
  );
  assert.equal(result.model_calls, 4);
  assert.deepEqual(result.usage, { input_tokens: 8, output_tokens: 12 });
  const sinkGenerationRequest = messages.find(message => {
    if (message.type !== 'model_request' || message.json_response === true) return false;
    const requestMessages = message.messages as Array<Record<string, unknown>>;
    return String(requestMessages?.[1]?.content ?? '').includes('ModelMirror closed authoritative fact set');
  });
  const sinkGenerationMessages = sinkGenerationRequest?.messages as Array<Record<string, unknown>>;
  assert.match(
    String(sinkGenerationMessages?.[1]?.content ?? ''),
    /audience is new colleagues; owner is the knowledge-base administrator/,
  );
  assert.match(
    String(sinkGenerationMessages?.[1]?.content ?? ''),
    /Do not present any other factual or operational claim as confirmed/,
  );
  assert.ok(messages.filter(message => (
    message.type === 'model_request' && message.json_response !== true
  )).every(message => {
    const requestMessages = message.messages as Array<Record<string, unknown>>;
    return String(requestMessages?.[1]?.content ?? '').includes('ModelMirror closed authoritative fact set');
  }));
  const modelRequest = messages.find(message => message.type === 'model_request');
  const modelRequestMessages = modelRequest?.messages as Array<Record<string, unknown>>;
  assert.equal(modelRequest?.timeout_seconds, 240);
  assert.match(
    String(modelRequestMessages?.[0]?.content ?? ''),
    /complete deliverable within 1,600 output tokens/,
  );
  assert.match(
    String(modelRequestMessages?.[0]?.content ?? ''),
    /acceptance criterion requesting a missing fact does not authorize fabrication/,
  );
  assert.match(
    String(modelRequestMessages?.[0]?.content ?? ''),
    /Aggregate capacity does not confirm individual availability/,
  );
  assert.match(
    String(modelRequestMessages?.[0]?.content ?? ''),
    /per-shift headcount is not additive across template rows/,
  );
  assert.match(
    String(modelRequestMessages?.[0]?.content ?? ''),
    /generic disclaimer is not enough/,
  );
  assert.match(
    String(modelRequestMessages?.[0]?.content ?? ''),
    /Explicit approval of a visible model-generated draft may approve its policy choices/,
  );
  assert.match(
    String(modelRequestMessages?.[0]?.content ?? ''),
    /treat it as a closed set/,
  );
  assert.match(
    String(modelRequestMessages?.[1]?.content ?? ''),
    /no more than 1,500 Chinese characters or 900 English words/,
  );
  assert.match(
    String(modelRequestMessages?.[1]?.content ?? ''),
    /examples and suggested values in this step description as instructions, not confirmed facts/,
  );
  const verificationRequest = messages.find(message => message.json_response === true);
  assert.equal(verificationRequest?.temperature, 0);
  assert.ok(Number(verificationRequest?.max_tokens) >= 1600);
  assert.ok(Number(verificationRequest?.max_tokens) <= 2000);
  const verificationMessages = verificationRequest?.messages as Array<Record<string, unknown>>;
  assert.match(
    String(verificationMessages?.[1]?.content ?? ''),
    /Model-generated dependency outputs are not evidence that a value was user-provided/,
  );
  assert.match(
    String(verificationMessages?.[1]?.content ?? ''),
    /listed role counts must equal the declared team or shift total/,
  );
  assert.match(
    String(verificationMessages?.[0]?.content ?? ''),
    /Treat an authoritative list introduced as "only"/,
  );
  assert.match(
    String(verificationMessages?.[1]?.content ?? ''),
    /Do not silently add mandatory prerequisites/,
  );
  assert.match(
    String(verificationMessages?.[1]?.content ?? ''),
    /Authoritative original user request/,
  );
  assert.match(
    String(verificationMessages?.[1]?.content ?? ''),
    /Known facts only include: audience is new colleagues/,
  );
  assert.match(
    String(verificationMessages?.[0]?.content ?? ''),
    /review every declarative factual or operational claim against that exact set/,
  );
  assert.match(
    String(verificationMessages?.[0]?.content ?? ''),
    /Never claim that a literal label, value, fact, or section is absent/,
  );
  assert.match(
    String(verificationMessages?.[0]?.content ?? ''),
    /failed array contains ONLY criteria that are actually unmet/,
  );
  assert.match(
    String(verificationMessages?.[0]?.content ?? ''),
    /Do not add stronger requirements such as concrete calendar dates/,
  );
  assert.match(
    String(verificationMessages?.[0]?.content ?? ''),
    /TBD\/pending-confirmation marker satisfies/,
  );
  assert.match(
    String(verificationMessages?.[0]?.content ?? ''),
    /Explicit approval of a visible draft may approve its policy choices/,
  );
  assert.match(
    String(verificationMessages?.[0]?.content ?? ''),
    /does not require an exact heading level or title/,
  );
  assert.ok(messages.some(message => (
    message.type === 'event'
    && (message.event as Record<string, unknown>).event === 'agency.run.completed'
  )));
  const completedEvent = messages
    .filter(message => message.type === 'event')
    .map(message => message.event as Record<string, unknown>)
    .find(event => event.event === 'agency.run.completed');
  assert.match(
    String(completedEvent?.final_output ?? ''),
    /ModelMirror fact and decision boundary \(system-applied\)/,
  );
  channel.close();
});

test('v2 execution deterministically reworks a sink that exceeds an explicit character limit', async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  const channel = new JsonlChannel(input, output);
  const modelRequests: Record<string, unknown>[] = [];
  let outputBuffer = '';
  output.on('data', chunk => {
    outputBuffer += chunk.toString('utf8');
    const lines = outputBuffer.split('\n');
    outputBuffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line) continue;
      const message = JSON.parse(line) as Record<string, unknown>;
      if (message.type !== 'model_request') continue;
      modelRequests.push(message);
      const messages = message.messages as Array<Record<string, unknown>>;
      const system = String(messages?.[0]?.content ?? '');
      const user = String(messages?.[1]?.content ?? '');
      const content = system.includes('reviewer') || system.includes('验收员')
        ? '{"pass":true,"failed":[]}'
        : user.includes('system fact boundary adds')
          ? '短评审包'
          : 'x'.repeat(1_400);
      input.write(`${JSON.stringify({
        protocol: AGENCY_EXECUTION_PROTOCOL,
        type: 'model_response',
        id: message.id,
        request_id: message.request_id,
        ok: true,
        result: { content, usage: { input_tokens: 2, output_tokens: 3 } },
      })}\n`);
    }
  });

  const result = await executeRequest(request({
    name: 'bounded final',
    steps: [{
      id: 'final', role: 'agent-alpha', task: 'Write a compact review.',
      acceptance: 'The deliverable must contain no more than 1200 characters.', output: 'final_output', depends_on: [],
    }],
  }), channel);

  assert.equal(result.success, true);
  assert.equal(result.quality_status, 'passed');
  assert.equal(result.model_calls, 3);
  assert.equal(modelRequests.filter(message => message.json_response !== true).length, 2);
  assert.equal(modelRequests.filter(message => message.json_response === true).length, 1);
  assert.ok(modelRequests.some(message => {
    const messages = message.messages as Array<Record<string, unknown>>;
    return String(messages?.[1]?.content ?? '').includes('system fact boundary adds');
  }));
  const finalStep = (result.steps as Array<Record<string, unknown>>)
    .find(step => step.id === 'final');
  assert.deepEqual(finalStep?.verification, { pass: true, failed: [], reworked: true });
  assert.match(String(finalStep?.output ?? ''), /短评审包/);
  channel.close();
});

test('v2 execution resumes from completed steps without billing them again', async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  const channel = new JsonlChannel(input, output);
  const requestedSystems: string[] = [];
  const requestedUsers: string[] = [];
  const events: Record<string, unknown>[] = [];
  let outputBuffer = '';
  output.on('data', chunk => {
    outputBuffer += chunk.toString('utf8');
    const lines = outputBuffer.split('\n');
    outputBuffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line) continue;
      const message = JSON.parse(line) as Record<string, unknown>;
      if (message.type === 'event') events.push(message.event as Record<string, unknown>);
      if (message.type !== 'model_request') continue;
      const rawMessages = message.messages as Array<Record<string, unknown>>;
      const system = String(rawMessages?.[0]?.content ?? '');
      requestedSystems.push(system);
      requestedUsers.push(String(rawMessages?.[1]?.content ?? ''));
      const content = system.includes('reviewer') || system.includes('验收员')
        ? '{"pass":true,"failed":[]}'
        : 'new-final-output';
      input.write(`${JSON.stringify({
        protocol: AGENCY_EXECUTION_PROTOCOL,
        type: 'model_response',
        id: message.id,
        request_id: message.request_id,
        ok: true,
        result: { content, usage: { input_tokens: 5, output_tokens: 7 } },
      })}\n`);
    }
  });

  const workflow = {
    name: 'resume',
    steps: [
      { id: 'research', role: 'agent-alpha', task: 'Research', output: 'research_output', depends_on: [] },
      {
        id: 'synthesis', role: 'agent-beta', task: 'Use {{research_output}}',
        acceptance: 'Must be actionable', output: 'final_output', depends_on: ['research'],
      },
    ],
  };
  const paidResearchOutput = [
    'CONFIRMED_HEAD: preserve the original user constraints.',
    'ordinary supporting detail\n'.repeat(700),
    '## Risks and TBD\nRISK_TBD_SENTINEL: budget and owner remain pending confirmation.',
    'additional supporting detail\n'.repeat(500),
    'CONFIRMED_TAIL: rollback remains mandatory.',
  ].join('\n');
  const result = await executeRequest(request(workflow, [], {
    source_task_id: 'agency_dag_previous',
    prior_model_calls: 1,
    prior_usage: { input_tokens: 11, output_tokens: 13 },
    completed_steps: [{
      task_id: 'research',
      output_variable: 'research_output',
      output: paidResearchOutput,
    }],
  }), channel);

  assert.equal(result.success, true);
  assert.equal(result.model_calls, 3);
  assert.deepEqual(result.usage, { input_tokens: 21, output_tokens: 27 });
  assert.equal(requestedSystems.length, 2);
  assert.match(requestedUsers[0], /ModelMirror bounded dependency excerpt/);
  assert.match(requestedUsers[0], /CONFIRMED_HEAD/);
  assert.match(requestedUsers[0], /RISK_TBD_SENTINEL/);
  assert.match(requestedUsers[0], /CONFIRMED_TAIL/);
  assert.ok(requestedUsers[0].length < paidResearchOutput.length / 2);
  assert.ok(events.some(event => (
    event.task_id === 'research'
    && event.reused === true
    && event.output === paidResearchOutput
  )));
  assert.ok(events.some(event => event.event === 'agency.run.completed' && event.resumed_from_task_id === 'agency_dag_previous'));
  channel.close();
});

test('v2 revision reruns the target and downstream while reusing an independent sibling', async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  const channel = new JsonlChannel(input, output);
  const requestedUsers: string[] = [];
  const events: Record<string, unknown>[] = [];
  let outputBuffer = '';
  output.on('data', chunk => {
    outputBuffer += chunk.toString('utf8');
    const lines = outputBuffer.split('\n');
    outputBuffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line) continue;
      const message = JSON.parse(line) as Record<string, unknown>;
      if (message.type === 'event') events.push(message.event as Record<string, unknown>);
      if (message.type !== 'model_request') continue;
      const rawMessages = message.messages as Array<Record<string, unknown>>;
      const system = String(rawMessages?.[0]?.content ?? '');
      const user = String(rawMessages?.[1]?.content ?? '');
      requestedUsers.push(user);
      const content = system.includes('reviewer') || system.includes('验收员')
        ? '{"pass":true,"failed":[]}'
        : user.includes('TARGET_TASK')
          ? 'revised-target-output'
          : 'revised-final-output';
      input.write(`${JSON.stringify({
        protocol: AGENCY_EXECUTION_PROTOCOL,
        type: 'model_response',
        id: message.id,
        request_id: message.request_id,
        ok: true,
        result: { content, usage: { input_tokens: 3, output_tokens: 4 } },
      })}\n`);
    }
  });

  const workflow = {
    name: 'revision fan-out fan-in',
    steps: [
      { id: 'root', role: 'agent-alpha', task: 'ROOT_TASK', output: 'root_output', depends_on: [] },
      { id: 'target', role: 'agent-alpha', task: 'TARGET_TASK {{root_output}}', output: 'target_output', depends_on: ['root'] },
      { id: 'sibling', role: 'agent-beta', task: 'SIBLING_TASK {{root_output}}', output: 'sibling_output', depends_on: ['root'] },
      {
        id: 'final', role: 'agent-beta',
        task: 'FINAL_TASK {{target_output}} {{sibling_output}}', output: 'final_output',
        acceptance: 'Must integrate both branches', depends_on: ['target', 'sibling'],
      },
    ],
  };
  const feedback = 'Keep the evidence and tighten the budget recommendation.';
  const result = await executeRequest(request(workflow, [], undefined, {
    source_task_id: 'agency_dag_source',
    target_task_id: 'target',
    feedback,
    previous_output: 'previous-target-output',
    completed_steps: [
      { task_id: 'root', output_variable: 'root_output', output: 'existing-root-output' },
      { task_id: 'sibling', output_variable: 'sibling_output', output: 'existing-sibling-output' },
    ],
  }), channel);

  assert.equal(result.success, true);
  assert.equal(result.model_calls, 3);
  assert.deepEqual(result.usage, { input_tokens: 9, output_tokens: 12 });
  assert.deepEqual(new Set(result.reused_task_ids as string[]), new Set(['root', 'sibling']));
  const targetPrompt = requestedUsers.find(value => value.includes('TARGET_TASK')) ?? '';
  assert.match(targetPrompt, /previous-target-output/);
  assert.match(targetPrompt, /tighten the budget recommendation/);
  const nonTargetPrompts = requestedUsers.filter(value => !value.includes('TARGET_TASK'));
  assert.ok(nonTargetPrompts.every(value => !value.includes(feedback)));
  assert.ok(events.some(event => event.task_id === 'root' && event.reused === true));
  assert.ok(events.some(event => event.task_id === 'sibling' && event.reused === true));
  assert.ok(events.some(event => event.task_id === 'target' && event.reused !== true));
  assert.ok(events.some(event => (
    event.event === 'agency.run.completed'
    && event.revision_parent_task_id === 'agency_dag_source'
    && event.revision_target_task_id === 'target'
    && event.resumed_from_task_id === undefined
  )));
  channel.close();
});

test('v2 revision supports a single-step DAG with no restored steps', async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  const channel = new JsonlChannel(input, output);
  const prompts: string[] = [];
  let outputBuffer = '';
  output.on('data', chunk => {
    outputBuffer += chunk.toString('utf8');
    const lines = outputBuffer.split('\n');
    outputBuffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line) continue;
      const message = JSON.parse(line) as Record<string, unknown>;
      if (message.type !== 'model_request') continue;
      const rawMessages = message.messages as Array<Record<string, unknown>>;
      const system = String(rawMessages?.[0]?.content ?? '');
      prompts.push(String(rawMessages?.[1]?.content ?? ''));
      input.write(`${JSON.stringify({
        protocol: AGENCY_EXECUTION_PROTOCOL,
        type: 'model_response',
        id: message.id,
        request_id: message.request_id,
        ok: true,
        result: {
          content: system.includes('reviewer') || system.includes('验收员')
            ? '{"pass":true,"failed":[]}'
            : 'single-revision-output',
          usage: { input_tokens: 1, output_tokens: 2 },
        },
      })}\n`);
    }
  });

  const result = await executeRequest(request({
    name: 'single revision',
    steps: [{
      id: 'only', role: 'agent-alpha', task: 'ONLY_TASK', output: 'final_output',
      acceptance: 'Must be concrete', depends_on: [],
    }],
  }, [], undefined, {
    source_task_id: 'single-source',
    target_task_id: 'only',
    feedback: 'Please make the conclusion more concrete.',
    previous_output: 'previous single output',
    completed_steps: [],
  }), channel);

  assert.equal(result.success, true);
  assert.equal(result.model_calls, 2);
  assert.deepEqual(result.reused_task_ids, []);
  const revisionPrompt = prompts.find(value => value.includes('ONLY_TASK')) ?? '';
  assert.match(revisionPrompt, /previous single output/);
  assert.match(revisionPrompt, /make the conclusion more concrete/);
  channel.close();
});

test('v2 revision rejects conflicting or malformed revision state with stable errors', async () => {
  const workflow = {
    name: 'revision validation',
    steps: [
      { id: 'first', role: 'agent-alpha', task: 'FIRST', output: 'first_output', depends_on: [] },
      {
        id: 'final', role: 'agent-beta', task: 'FINAL {{first_output}}',
        output: 'final_output', acceptance: 'Must be complete', depends_on: ['first'],
      },
    ],
  };
  const resume = {
    source_task_id: 'source', prior_model_calls: 1,
    prior_usage: { input_tokens: 1, output_tokens: 1 },
    completed_steps: [{ task_id: 'first', output_variable: 'first_output', output: 'done' }],
  };
  const revision = {
    source_task_id: 'source', target_task_id: 'first',
    feedback: 'Please improve this completed output.', previous_output: 'done', completed_steps: [],
  };
  await assert.rejects(
    executeRequest(request(workflow, [], resume, revision), new JsonlChannel(new PassThrough(), new PassThrough())),
    (error: unknown) => error instanceof AgencyBridgeError && error.code === 'agency_execution_plan_invalid',
  );
  await assert.rejects(
    executeRequest(request(workflow, [], undefined, { ...revision, target_task_id: 'unknown' }), new JsonlChannel(new PassThrough(), new PassThrough())),
    (error: unknown) => error instanceof AgencyBridgeError && error.code === 'agency_execution_revision_invalid',
  );
  await assert.rejects(
    executeRequest(request(workflow, [], undefined, { ...revision, feedback: 'short' }), new JsonlChannel(new PassThrough(), new PassThrough())),
    (error: unknown) => error instanceof AgencyBridgeError && error.code === 'agency_execution_revision_invalid',
  );
  await assert.rejects(
    executeRequest(request(workflow, [], undefined, {
      ...revision,
      completed_steps: [{ task_id: 'first', output_variable: 'first_output', output: 'done' }],
    }), new JsonlChannel(new PassThrough(), new PassThrough())),
    (error: unknown) => error instanceof AgencyBridgeError && error.code === 'agency_execution_revision_invalid',
  );
});

test('v2 execution reports token-limit truncation as an actionable error', async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  const channel = new JsonlChannel(input, output);
  const events: Record<string, unknown>[] = [];
  let outputBuffer = '';
  output.on('data', chunk => {
    outputBuffer += chunk.toString('utf8');
    const lines = outputBuffer.split('\n');
    outputBuffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line) continue;
      const message = JSON.parse(line) as Record<string, unknown>;
      if (message.type === 'event') events.push(message.event as Record<string, unknown>);
      if (message.type !== 'model_request') continue;
      input.write(`${JSON.stringify({
        protocol: AGENCY_EXECUTION_PROTOCOL,
        type: 'model_response',
        id: message.id,
        request_id: message.request_id,
        ok: true,
        result: {
          content: 'partial output',
          finish_reason: 'length',
          usage: { input_tokens: 2, output_tokens: 4096 },
        },
      })}\n`);
    }
  });

  await assert.rejects(
    executeRequest(request({
      name: 'truncated',
      steps: [{
        id: 'final', role: 'agent-alpha', task: 'Write', output: 'final_output',
        acceptance: 'Complete', depends_on: [],
      }],
    }), channel),
    (error: unknown) => error instanceof AgencyBridgeError && error.code === 'model_output_truncated',
  );
  const failedEvent = events.find(event => event.event === 'agency.run.failed');
  assert.deepEqual(failedEvent?.usage, { input_tokens: 2, output_tokens: 4096 });
  channel.close();
});

test('v2 execution surfaces an empty model response without hidden paid retries', async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  const channel = new JsonlChannel(input, output);
  let requestCount = 0;
  let outputBuffer = '';
  output.on('data', chunk => {
    outputBuffer += chunk.toString('utf8');
    const lines = outputBuffer.split('\n');
    outputBuffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line) continue;
      const message = JSON.parse(line) as Record<string, unknown>;
      if (message.type !== 'model_request') continue;
      requestCount += 1;
      input.write(`${JSON.stringify({
        protocol: AGENCY_EXECUTION_PROTOCOL,
        type: 'model_response', id: message.id, request_id: message.request_id,
        ok: false,
        error: {
          code: 'model_response_empty', message: 'No deliverable content.',
          usage: { input_tokens: 5, output_tokens: 0 }, finish_reason: 'stop',
        },
      })}\n`);
    }
  });

  await assert.rejects(
    executeRequest(request({
      name: 'empty response',
      steps: [{
        id: 'final', role: 'agent-alpha', task: 'Write', output: 'final_output',
        acceptance: 'Complete', depends_on: [],
      }],
    }), channel),
    (error: unknown) => error instanceof AgencyBridgeError && error.code === 'model_response_empty',
  );
  assert.equal(requestCount, 1);
  channel.close();
});

test('v2 execution rejects unsupported and multi-sink workflows before model calls', async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  const channel = new JsonlChannel(input, output);
  await assert.rejects(
    executeRequest(request({
      name: 'invalid',
      steps: [
        {
          id: 'one', role: 'agent-alpha', task: 'one', output: 'one_output',
          acceptance: 'done', depends_on: [], skill: 'forbidden',
        },
        {
          id: 'two', role: 'agent-beta', task: 'two', output: 'two_output',
          acceptance: 'done', depends_on: [],
        },
      ],
    }), channel),
    (error: unknown) => error instanceof AgencyBridgeError && error.code === 'agency_execution_plan_invalid',
  );
  channel.close();
});

test('execution rejects structured property references on plain-text outputs before model calls', async () => {
  const channel = new JsonlChannel(new PassThrough(), new PassThrough());
  await assert.rejects(
    executeRequest(request({
      name: 'structured reference',
      steps: [
        {
          id: 'source', role: 'agent-alpha', task: 'Collect requirements.',
          output: 'requirements_output', depends_on: [],
        },
        {
          id: 'final', role: 'agent-beta', task: 'Use {{requirements_output.format}}.',
          output: 'final_output', acceptance: 'Must follow the format.', depends_on: ['source'],
        },
      ],
    }), channel),
    (error: unknown) => (
      error instanceof AgencyBridgeError
      && error.code === 'agency_execution_plan_invalid'
      && /unsupported structured template reference/.test(error.message)
    ),
  );
  channel.close();
});

test('v2 execution injects a host-approved method Skill without enabling tools', async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  const channel = new JsonlChannel(input, output);
  let observedSystem = '';
  let outputBuffer = '';
  output.on('data', chunk => {
    outputBuffer += chunk.toString('utf8');
    const lines = outputBuffer.split('\n');
    outputBuffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line) continue;
      const message = JSON.parse(line) as Record<string, unknown>;
      if (message.type !== 'model_request') continue;
      const rawMessages = message.messages as Array<Record<string, unknown>>;
      const system = String(rawMessages?.[0]?.content ?? '');
      observedSystem += system;
      const content = system.includes('reviewer') || system.includes('验收员')
        ? '{"pass":true,"failed":[]}'
        : 'method-aware-result';
      input.write(`${JSON.stringify({
        protocol: AGENCY_EXECUTION_PROTOCOL,
        type: 'model_response',
        id: message.id,
        request_id: message.request_id,
        ok: true,
        result: { content, usage: { input_tokens: 1, output_tokens: 1 } },
      })}\n`);
    }
  });

  const result = await executeRequest(request({
    name: 'method skill',
    steps: [{
      id: 'analysis', role: 'agent-alpha', task: 'Analyze {{user_input}}',
      acceptance: 'Must be structured', output: 'final_output', depends_on: [],
      skills: ['data-analysis'],
    }],
  }, [{
    skill_id: 'data-analysis',
    name: 'Data Analysis',
    description: 'A bounded analysis method.',
    body: 'METHOD_SKILL_SENTINEL: verify evidence before conclusions.',
    digest: 'a'.repeat(64),
  }]), channel);

  assert.equal(result.success, true);
  assert.match(observedSystem, /METHOD_SKILL_SENTINEL/);
  await assert.rejects(
    executeRequest(request({
      name: 'unknown method',
      steps: [{
        id: 'analysis', role: 'agent-alpha', task: 'Analyze', acceptance: 'Done',
        output: 'final_output', depends_on: [], skills: ['unknown-skill'],
      }],
    }), new JsonlChannel(new PassThrough(), new PassThrough())),
    (error: unknown) => error instanceof AgencyBridgeError && error.code === 'agency_execution_plan_invalid',
  );
  channel.close();
});
