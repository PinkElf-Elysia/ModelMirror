export interface SkillNeedMemberMetadata {
  displayName: string;
  sourceDescription: string;
  tags: string[];
}

export interface SkillNeedMemberMetadataIndex {
  version: 1;
  memberIndexFingerprint: string;
  members: Record<string, SkillNeedMemberMetadata>;
}

let memberMetadataPromise: Promise<SkillNeedMemberMetadataIndex> | undefined;

export function loadSkillNeedMemberMetadataIndex() {
  memberMetadataPromise ??= import("./skillNeedMembers.generated.json").then(
    (module) => module.default as SkillNeedMemberMetadataIndex,
  );
  return memberMetadataPromise;
}
