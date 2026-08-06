import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, rename, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import {
  GithubPathHistoryTransientError,
  GithubSkillPathHistoryPool,
  followExactRenameChain,
} from "./github-skill-path-history.mjs";

const execFileAsync = promisify(execFile);
const fixtureRoot = await mkdtemp(join(tmpdir(), "modelmirror-history-audit-"));

async function git(repository, args) {
  const { stdout = "" } = await execFileAsync("git", args, {
    cwd: repository,
    windowsHide: true,
  });
  return stdout.trim();
}

async function createRepository(name) {
  const repository = join(fixtureRoot, name);
  await mkdir(repository, { recursive: true });
  await git(repository, ["init", "-b", "main"]);
  await git(repository, ["config", "user.name", "ModelMirror Audit"]);
  await git(repository, ["config", "user.email", "audit@example.invalid"]);
  return repository;
}

async function writeSkill(repository, subPath, body = "# Fixture\n") {
  const directory = join(repository, ...subPath.split("/"));
  await mkdir(directory, { recursive: true });
  await writeFile(
    join(directory, "SKILL.md"),
    `---\nname: ${subPath.split("/").at(-1)}\n---\n\n${body}`,
    "utf8",
  );
}

async function commitAll(repository, message) {
  await git(repository, ["add", "-A"]);
  await git(repository, ["commit", "-m", message]);
  return git(repository, ["rev-parse", "HEAD"]);
}

async function moveSkill(repository, fromSubPath, toSubPath) {
  const destination = join(repository, ...toSubPath.split("/"));
  await mkdir(join(destination, ".."), { recursive: true });
  await rename(
    join(repository, ...fromSubPath.split("/")),
    destination,
  );
}

const chainRepository = await createRepository("rename-chain");
await writeSkill(chainRepository, "skills/old");
await commitAll(chainRepository, "add old skill");
await moveSkill(chainRepository, "skills/old", "skills/middle");
const firstRenameCommit = await commitAll(chainRepository, "rename old to middle");
await moveSkill(chainRepository, "skills/middle", "skills/final");
const chainHead = await commitAll(chainRepository, "rename middle to final");

const deletionRepository = await createRepository("deletion");
await writeSkill(deletionRepository, "skills/deleted");
await commitAll(deletionRepository, "add deleted skill");
await rm(join(deletionRepository, "skills", "deleted"), {
  recursive: true,
  force: true,
});
const deletionHead = await commitAll(deletionRepository, "remove skill");

const changedMoveRepository = await createRepository("changed-move");
await writeSkill(changedMoveRepository, "skills/original", "original body\n");
await commitAll(changedMoveRepository, "add original skill");
await moveSkill(changedMoveRepository, "skills/original", "skills/replacement");
await writeSkill(
  changedMoveRepository,
  "skills/replacement",
  "completely different replacement body with unrelated instructions\n",
);
const changedMoveHead = await commitAll(
  changedMoveRepository,
  "replace skill with changed content",
);

const laterDeletionRepository = await createRepository("later-deletion");
await writeSkill(laterDeletionRepository, "skills/source");
await commitAll(laterDeletionRepository, "add source skill");
await moveSkill(laterDeletionRepository, "skills/source", "skills/temporary");
await commitAll(laterDeletionRepository, "rename source to temporary");
await rm(join(laterDeletionRepository, "skills", "temporary"), {
  recursive: true,
  force: true,
});
const laterDeletionHead = await commitAll(
  laterDeletionRepository,
  "remove renamed target",
);

const neverSeenRepository = await createRepository("never-seen");
await writeSkill(neverSeenRepository, "skills/unrelated");
const neverSeenHead = await commitAll(neverSeenRepository, "add unrelated skill");

const pool = new GithubSkillPathHistoryPool();
try {
  const chain = await pool.resolve({
    repoUrl: chainRepository,
    headCommit: chainHead,
    declaredSubPath: "skills/old",
    currentSkillFiles: ["skills/final/SKILL.md"],
  });
  assert.equal(chain.status, "renamed");
  assert.equal(chain.subPath, "skills/final");
  assert.equal(chain.renameChain.length, 2);
  assert.equal(chain.renameChain[0].commit, firstRenameCommit);
  assert.deepEqual(
    chain.renameChain.map(({ fromSubPath, toSubPath }) => ({
      fromSubPath,
      toSubPath,
    })),
    [
      { fromSubPath: "skills/old", toSubPath: "skills/middle" },
      { fromSubPath: "skills/middle", toSubPath: "skills/final" },
    ],
  );

  const oneStep = await pool.resolve({
    repoUrl: chainRepository,
    headCommit: chainHead,
    declaredSubPath: "skills/middle",
    currentSkillFiles: ["skills/final/SKILL.md"],
  });
  assert.equal(oneStep.status, "renamed");
  assert.equal(oneStep.renameChain.length, 1);

  const deleted = await pool.resolve({
    repoUrl: deletionRepository,
    headCommit: deletionHead,
    declaredSubPath: "skills/deleted",
    currentSkillFiles: [],
  });
  assert.equal(deleted.status, "removed");

  const changedMove = await pool.resolve({
    repoUrl: changedMoveRepository,
    headCommit: changedMoveHead,
    declaredSubPath: "skills/original",
    currentSkillFiles: ["skills/replacement/SKILL.md"],
  });
  assert.equal(changedMove.status, "removed");

  const targetDeleted = await pool.resolve({
    repoUrl: laterDeletionRepository,
    headCommit: laterDeletionHead,
    declaredSubPath: "skills/source",
    currentSkillFiles: [],
  });
  assert.equal(targetDeleted.status, "removed");
  assert.equal(targetDeleted.renameChain.length, 1);

  const neverSeen = await pool.resolve({
    repoUrl: neverSeenRepository,
    headCommit: neverSeenHead,
    declaredSubPath: "skills/missing",
    currentSkillFiles: ["skills/unrelated/SKILL.md"],
  });
  assert.equal(neverSeen.status, "never-seen");
} finally {
  await pool.close();
  await rm(fixtureRoot, { recursive: true, force: true });
}

const ambiguous = await followExactRenameChain({
  declaredSubPath: "skills/a",
  currentSkillFiles: ["skills/b/SKILL.md", "skills/c/SKILL.md"],
  lookupTransition: async () => ({
    kind: "renamed",
    commit: "a".repeat(40),
    candidates: ["skills/b/SKILL.md", "skills/c/SKILL.md"],
  }),
});
assert.equal(ambiguous.status, "ambiguous");

const transitions = new Map([
  [
    "skills/a/SKILL.md",
    {
      kind: "renamed",
      commit: "b".repeat(40),
      candidates: ["skills/b/SKILL.md"],
    },
  ],
  [
    "skills/b/SKILL.md",
    {
      kind: "renamed",
      commit: "c".repeat(40),
      candidates: ["skills/a/SKILL.md"],
    },
  ],
]);
const cycle = await followExactRenameChain({
  declaredSubPath: "skills/a",
  currentSkillFiles: [],
  lookupTransition: async (skillFile) => transitions.get(skillFile),
});
assert.equal(cycle.status, "ambiguous");

const unavailablePool = new GithubSkillPathHistoryPool({
  runCommand: async () => ({
    code: 1,
    stdout: "",
    stderr: "simulated network failure",
  }),
});
try {
  await assert.rejects(
    () =>
      unavailablePool.resolve({
        repoUrl: "https://github.com/example/unavailable",
        headCommit: "d".repeat(40),
        declaredSubPath: "skills/example",
        currentSkillFiles: [],
      }),
    (error) => error instanceof GithubPathHistoryTransientError,
  );
} finally {
  await unavailablePool.close();
}

console.log(
  "GitHub Skill 路径历史审计通过：单步、多步、删除、内容变化、目标删除、从未出现、多候选、循环和临时故障均符合预期",
);
