import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { cp, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import {
  GithubSkillTreePool,
  GithubSkillTreeTransientError,
  buildSkillSetMembers,
  classifySkillScope,
} from "./github-skill-tree.mjs";

const execFileAsync = promisify(execFile);
const fixtureRoot = await mkdtemp(join(tmpdir(), "modelmirror-skill-tree-audit-"));
const repository = join(fixtureRoot, "repository");

async function git(args) {
  const { stdout = "" } = await execFileAsync("git", args, {
    cwd: repository,
    windowsHide: true,
  });
  return stdout.trim();
}

async function writeSkill(subPath, body = "# Fixture\n") {
  const directory = join(repository, ...subPath.split("/"));
  await mkdir(directory, { recursive: true });
  await writeFile(
    join(directory, "SKILL.md"),
    `---\nname: ${subPath.split("/").at(-1)}\n---\n\n${body}`,
    "utf8",
  );
}

await mkdir(repository, { recursive: true });
await git(["init", "-b", "main"]);
await git(["config", "user.name", "ModelMirror Audit"]);
await git(["config", "user.email", "audit@example.invalid"]);

await writeSkill("single");
await writeSkill("package");
await writeSkill("package/child");
await writeSkill("collection/alpha");
await writeSkill("collection/beta");
await writeSkill("unique-wrapper/member");
await writeSkill("unique-wrapper/member/nested");
await writeSkill("duplicates/a", "identical\n");
await cp(join(repository, "duplicates", "a"), join(repository, "duplicates", "b"), {
  recursive: true,
});
await writeSkill("different/a", "same skill markdown\n");
await writeSkill("different/b", "same skill markdown\n");
await writeFile(join(repository, "different", "a", "asset.txt"), "left", "utf8");
await writeFile(join(repository, "different", "b", "asset.txt"), "right", "utf8");
await writeSkill("overlap/group/a");
await writeSkill("overlap/group/b");
await writeSkill("overlap/other");

await git(["add", "-A"]);
await git(["commit", "-m", "create structural fixtures"]);
const head = await git(["rev-parse", "HEAD"]);

const pool = new GithubSkillTreePool();
try {
  const scan = await pool.scan({
    repoUrl: repository,
    verifiedCommit: head,
    requireHead: true,
  });

  const single = classifySkillScope({
    skillFiles: scan.skillFiles,
    scopeSubPath: "single",
  });
  assert.equal(single.verifiedKind, "skill");
  assert.equal(single.installMode, "direct");

  const packageResult = classifySkillScope({
    skillFiles: scan.skillFiles,
    scopeSubPath: "package",
  });
  assert.equal(packageResult.verifiedKind, "skillset");
  assert.equal(packageResult.skillsetMode, "package");
  assert.equal(packageResult.nestedSkillCount, 1);

  const collection = classifySkillScope({
    skillFiles: scan.skillFiles,
    scopeSubPath: "collection",
  });
  assert.equal(collection.verifiedKind, "skillset");
  assert.equal(collection.installMode, "members");
  assert.equal(collection.topMemberSubPaths.length, 2);

  const uniquePackage = classifySkillScope({
    skillFiles: scan.skillFiles,
    scopeSubPath: "unique-wrapper",
  });
  assert.equal(uniquePackage.verifiedKind, "skillset");
  assert.equal(uniquePackage.installMode, "direct");
  assert.equal(uniquePackage.resolvedSubPath, "unique-wrapper/member");

  const duplicates = classifySkillScope({
    skillFiles: scan.skillFiles,
    scopeSubPath: "duplicates",
  });
  const deduplicated = buildSkillSetMembers({
    repoUrl: "https://github.com/example/fixture",
    verifiedCommit: head,
    classification: duplicates,
    skillFiles: scan.skillFiles,
    directoryTrees: scan.directoryTrees,
  });
  assert.equal(deduplicated.members.length, 1);
  assert.equal(deduplicated.duplicateMemberCount, 1);

  const different = classifySkillScope({
    skillFiles: scan.skillFiles,
    scopeSubPath: "different",
  });
  const retained = buildSkillSetMembers({
    repoUrl: "https://github.com/example/fixture",
    verifiedCommit: head,
    classification: different,
    skillFiles: scan.skillFiles,
    directoryTrees: scan.directoryTrees,
  });
  assert.equal(retained.members.length, 2);

  const fullOverlap = classifySkillScope({
    skillFiles: scan.skillFiles,
    scopeSubPath: "overlap",
  });
  const nestedOverlap = classifySkillScope({
    skillFiles: scan.skillFiles,
    scopeSubPath: "overlap/group",
  });
  const fullMembers = buildSkillSetMembers({
    repoUrl: "https://github.com/example/fixture",
    verifiedCommit: head,
    classification: fullOverlap,
    skillFiles: scan.skillFiles,
    directoryTrees: scan.directoryTrees,
  });
  const nestedMembers = buildSkillSetMembers({
    repoUrl: "https://github.com/example/fixture",
    verifiedCommit: head,
    classification: nestedOverlap,
    skillFiles: scan.skillFiles,
    directoryTrees: scan.directoryTrees,
  });
  assert.ok(
    nestedMembers.members.every((member) =>
      fullMembers.members.some((candidate) => candidate.id === member.id),
    ),
  );

  const empty = classifySkillScope({
    skillFiles: scan.skillFiles,
    scopeSubPath: "empty",
  });
  assert.equal(empty.status, "empty");
} finally {
  await pool.close();
  await rm(fixtureRoot, { recursive: true, force: true });
}

const unavailablePool = new GithubSkillTreePool({
  runCommand: async () => ({
    code: 1,
    stdout: "",
    stderr: "simulated network failure",
  }),
});
try {
  await assert.rejects(
    () =>
      unavailablePool.scan({
        repoUrl: "https://github.com/example/unavailable",
        verifiedCommit: "a".repeat(40),
        requireHead: true,
      }),
    (error) => error instanceof GithubSkillTreeTransientError,
  );
} finally {
  await unavailablePool.close();
}

console.log(
  "GitHub SkillSet 结构审计通过：单 Skill、父级包、成员集合、唯一后代、嵌套、目录去重、重叠集合、空范围和临时故障均符合预期",
);
