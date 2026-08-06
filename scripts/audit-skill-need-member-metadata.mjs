import assert from "node:assert/strict";
import {
  MAX_SKILL_DOCUMENT_BYTES,
  buildSkillNeedMemberSearchIndex,
  extractSkillNeedMemberMetadata,
  firstMeaningfulParagraph,
  parseSkillFrontmatter,
} from "./skill-need-member-metadata.mjs";

const encoder = new TextEncoder();
const member = {
  id: "member-fixture",
  name: "folder-name",
  repoUrl: "https://github.com/example/skills",
  subPath: "skills/web-testing/playwright-helper",
  verifiedCommit: "a".repeat(40),
  directoryTreeSha: "b".repeat(40),
  nestedSkillCount: 0,
};

const standard = extractSkillNeedMemberMetadata({
  member,
  bytes: encoder.encode(`---\nname: Playwright Helper\ndescription: Test web apps with Playwright.\ntags: [testing, browser]\n---\n# Details\n`),
});
assert.equal(standard.displayName, "Playwright Helper");
assert.equal(standard.sourceDescription, "Test web apps with Playwright.");
assert.ok(standard.tags.includes("testing"));
assert.ok(standard.tags.includes("playwright"));

const multiline = parseSkillFrontmatter(`---\nname: Folded\ndescription: >\n  Review database access\n  and security controls.\nkeywords:\n  - postgres\n  - audit\n---\nBody`);
assert.equal(
  multiline.attributes.description,
  "Review database access and security controls.",
);
assert.deepEqual(multiline.attributes.keywords, ["postgres", "audit"]);

assert.equal(
  firstMeaningfulParagraph(`# Heading\n\nThis paragraph explains the reusable workflow in enough detail.\n\nMore.`),
  "This paragraph explains the reusable workflow in enough detail.",
);

const fallback = extractSkillNeedMemberMetadata({
  member,
  bytes: encoder.encode(`# Helper\n\nUse this workflow to inspect a database and prepare a concise report.`),
});
assert.equal(fallback.displayName, "folder-name");
assert.match(fallback.sourceDescription, /inspect a database/);

assert.throws(
  () =>
    extractSkillNeedMemberMetadata({
      member,
      bytes: new Uint8Array(MAX_SKILL_DOCUMENT_BYTES + 1),
    }),
  /超过/,
);
assert.throws(
  () => extractSkillNeedMemberMetadata({ member, bytes: new Uint8Array([0xff]) }),
  /UTF-8/,
);
assert.throws(
  () =>
    extractSkillNeedMemberMetadata({
      member,
      bytes: encoder.encode("---\nname: Empty\n---\n# Empty"),
    }),
  /缺少可用说明/,
);

const memberIndex = {
  version: 2,
  fingerprint: "c".repeat(64),
  skillSets: {},
  members: { [member.id]: member },
};
await assert.rejects(
  buildSkillNeedMemberSearchIndex({
    memberIndex,
    readDocument: async () => {
      throw new Error("fixed commit missing");
    },
  }),
  /fixed commit missing/,
);

const built = await buildSkillNeedMemberSearchIndex({
  memberIndex,
  readDocument: async () =>
    encoder.encode(`---\nname: Indexed\ndescription: Indexed from a fixed commit.\n---`),
});
assert.equal(built.memberIndexFingerprint, memberIndex.fingerprint);
assert.equal(built.members[member.id].displayName, "Indexed");

console.log("Skill 成员元数据夹具审计通过");
