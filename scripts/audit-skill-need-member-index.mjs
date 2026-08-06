import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fingerprintSkillSetMemberIndex } from "./skill-member-index.mjs";

const memberIndex = JSON.parse(
  await readFile("client/src/data/skillSetMembers.generated.json", "utf8"),
);
const searchIndex = JSON.parse(
  await readFile("client/src/data/skillNeedMembers.generated.json", "utf8"),
);

assert.equal(memberIndex.version, 2, "成员注册表必须使用版本 2");
assert.match(memberIndex.fingerprint, /^[a-f0-9]{64}$/);
assert.equal(
  memberIndex.fingerprint,
  fingerprintSkillSetMemberIndex(memberIndex.skillSets, memberIndex.members),
  "成员注册表指纹与内容不一致",
);
assert.equal(searchIndex.version, 1, "成员需求索引版本不受支持");
assert.equal(
  searchIndex.memberIndexFingerprint,
  memberIndex.fingerprint,
  "成员需求索引与安装注册表不是同一批数据",
);

const memberIds = Object.keys(memberIndex.members).sort();
assert.deepEqual(
  Object.keys(searchIndex.members).sort(),
  memberIds,
  "成员需求索引必须完整覆盖安装注册表",
);

const referencedMembers = new Set();
for (const [skillSetId, group] of Object.entries(memberIndex.skillSets)) {
  assert.ok(group.memberIds.length >= 2, `${skillSetId} 不构成成员集合`);
  for (const memberId of group.memberIds) {
    const member = memberIndex.members[memberId];
    assert.ok(member, `${skillSetId} 引用了不存在的成员 ${memberId}`);
    assert.equal(member.repoUrl, group.repoUrl, `${skillSetId} 包含跨仓库成员`);
    assert.equal(
      member.verifiedCommit,
      group.verifiedCommit,
      `${skillSetId} 包含不同固定提交的成员`,
    );
    referencedMembers.add(memberId);
  }
}
assert.equal(referencedMembers.size, memberIds.length, "存在不属于任何 SkillSet 的成员");

const sourceKeys = new Set();
for (const memberId of memberIds) {
  const member = memberIndex.members[memberId];
  const metadata = searchIndex.members[memberId];
  assert.match(member.verifiedCommit, /^[a-f0-9]{40}$/);
  assert.ok(metadata.displayName.trim(), `${memberId} 缺少显示名称`);
  assert.ok(metadata.sourceDescription.trim(), `${memberId} 缺少来源说明`);
  assert.ok(Array.isArray(metadata.tags), `${memberId} 标签格式无效`);
  const sourceKey = `${member.repoUrl.toLowerCase()}#${member.subPath}#${member.verifiedCommit}`;
  assert.ok(!sourceKeys.has(sourceKey), `重复成员安装映射：${sourceKey}`);
  sourceKeys.add(sourceKey);
}

console.log(
  `Skill 成员需求索引审计通过：${memberIds.length} 个成员，${Object.keys(memberIndex.skillSets).length} 个集合`,
);
