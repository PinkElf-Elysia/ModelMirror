import { createHash } from "node:crypto";

function orderedRecord(record) {
  return Object.fromEntries(
    Object.entries(record ?? {}).sort(([left], [right]) =>
      left.localeCompare(right, "en"),
    ),
  );
}

export function canonicalSkillSetMemberIndex(skillSets, members) {
  const orderedSkillSets = Object.entries(orderedRecord(skillSets)).map(
    ([id, group]) => ({
      id,
      repoUrl: group.repoUrl,
      verifiedCommit: group.verifiedCommit,
      scopeSubPath: group.scopeSubPath,
      memberIds: [...group.memberIds],
      skillDocumentCount: group.skillDocumentCount,
      nestedSkillCount: group.nestedSkillCount,
      duplicateMemberCount: group.duplicateMemberCount,
    }),
  );
  const orderedMembers = Object.entries(orderedRecord(members)).map(
    ([id, member]) => ({
      id,
      name: member.name,
      repoUrl: member.repoUrl,
      subPath: member.subPath,
      verifiedCommit: member.verifiedCommit,
      directoryTreeSha: member.directoryTreeSha,
      nestedSkillCount: member.nestedSkillCount,
    }),
  );
  return { skillSets: orderedSkillSets, members: orderedMembers };
}

export function fingerprintSkillSetMemberIndex(skillSets, members) {
  return createHash("sha256")
    .update(JSON.stringify(canonicalSkillSetMemberIndex(skillSets, members)))
    .digest("hex");
}

export function buildSkillSetMemberIndex(skillSets, members) {
  const orderedSkillSets = orderedRecord(skillSets);
  const orderedMembers = orderedRecord(members);
  return {
    version: 2,
    fingerprint: fingerprintSkillSetMemberIndex(
      orderedSkillSets,
      orderedMembers,
    ),
    skillSets: orderedSkillSets,
    members: orderedMembers,
  };
}
