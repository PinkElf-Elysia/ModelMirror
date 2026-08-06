export interface SkillSetMemberSource {
  id: string;
  name: string;
  repoUrl: string;
  subPath: string;
  verifiedCommit: string;
  directoryTreeSha: string;
  nestedSkillCount: number;
}

export interface SkillSetMemberGroup {
  id: string;
  repoUrl: string;
  verifiedCommit: string;
  scopeSubPath: string;
  memberIds: string[];
  skillDocumentCount: number;
  nestedSkillCount: number;
  duplicateMemberCount: number;
}

export interface SkillSetMemberIndex {
  version: 2;
  fingerprint: string;
  skillSets: Record<string, SkillSetMemberGroup>;
  members: Record<string, SkillSetMemberSource>;
}

let memberIndexPromise: Promise<SkillSetMemberIndex> | undefined;

export function loadSkillSetMemberIndex() {
  memberIndexPromise ??= import("./skillSetMembers.generated.json").then(
    (module) => module.default as SkillSetMemberIndex,
  );
  return memberIndexPromise;
}
