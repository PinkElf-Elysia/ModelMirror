import assert from "node:assert/strict";
import {
  TransientOfficialSourceError,
  extractOfficialSkillDeclaration,
  resolveOfficialSkillPage,
} from "./official-skill-source-resolver.mjs";

function page(setup) {
  return `<html><body><h2>Setup &amp; Installation</h2><section>${setup}</section><h2>What This Skill Does</h2></body></html>`;
}

const directUrl = "https://github.com/auth0/agent-skills/tree/main/plugins/auth0-sdks/skills/auth0-react";
const direct = extractOfficialSkillDeclaration(
  page(`<a href="${directUrl}">${directUrl}</a><code>npx skills add https://github.com/auth0/agent-skills --skill auth0-react</code>`),
  "auth0-react",
);
assert.equal(direct.ok, true);
assert.deepEqual(direct.candidate, {
  repoUrl: "https://github.com/auth0/agent-skills",
  declaredUrl: directUrl,
  declaredAction: "tree",
  declaredRefAndPath: "main/plugins/auth0-sdks/skills/auth0-react",
  method: "source-page-declared-path",
});

const encoded = extractOfficialSkillDeclaration(
  page(
    "https:\\u002F\\u002Fgithub.com\\u002Fexample\\u002Fskills\\u002Fblob\\u002Frelease%2Fv2\\u002Fskills\\u002Fpdf\\u002FSKILL.md",
  ),
  "pdf",
);
assert.equal(encoded.ok, true);
assert.equal(encoded.candidate.declaredAction, "blob");
assert.equal(
  encoded.candidate.declaredRefAndPath,
  "release/v2/skills/pdf/SKILL.md",
);

const command = extractOfficialSkillDeclaration(
  page("<code>npx skills add https://github.com/example/agent-skills --skill spreadsheet</code>"),
  "spreadsheet",
);
assert.equal(command.ok, true);
assert.deepEqual(command.candidate, {
  repoUrl: "https://github.com/example/agent-skills",
  declaredUrl: "https://github.com/example/agent-skills",
  method: "source-page-command-exact-match",
  requiresExactName: true,
});

const ambiguous = extractOfficialSkillDeclaration(
  page(
    '<a href="https://github.com/one/skills/tree/main/skills/pdf">one</a><a href="https://github.com/two/skills/tree/main/skills/pdf">two</a>',
  ),
  "pdf",
);
assert.equal(ambiguous.ok, false);
assert.equal(ambiguous.reasonCode, "source-page-declaration-ambiguous");

const invalidHost = extractOfficialSkillDeclaration(
  page("<code>npx skills add https://example.com/not-allowed/repo --skill pdf</code>"),
  "pdf",
);
assert.equal(invalidHost.ok, false);
assert.equal(invalidHost.reasonCode, "source-page-declaration-missing");

const missingSetup = extractOfficialSkillDeclaration(
  `<html><body><a href="${directUrl}">unscoped link</a></body></html>`,
  "auth0-react",
);
assert.equal(missingSetup.ok, false);
assert.equal(missingSetup.reasonCode, "source-page-declaration-missing");

function response(status, body = "", url = "https://officialskills.sh/example/skills/pdf") {
  return {
    status,
    ok: status >= 200 && status < 300,
    url,
    headers: { get: () => "text/html; charset=utf-8" },
    text: async () => body,
  };
}

const project = {
  sourceUrl: "https://officialskills.sh/example/skills/pdf",
  name: "example/pdf",
};
const notFound = await resolveOfficialSkillPage(project, {
  fetchImpl: async () => response(404),
});
assert.equal(notFound.ok, false);
assert.equal(notFound.reasonCode, "source-page-not-found");

await assert.rejects(
  () =>
    resolveOfficialSkillPage(project, {
      fetchImpl: async () => response(429),
    }),
  TransientOfficialSourceError,
);
await assert.rejects(
  () =>
    resolveOfficialSkillPage(project, {
      fetchImpl: async () => {
        throw new Error("timeout");
      },
    }),
  TransientOfficialSourceError,
);
await assert.rejects(
  () =>
    resolveOfficialSkillPage(project, {
      fetchImpl: async () => response(200, page(directUrl), "https://example.com/redirected"),
    }),
  TransientOfficialSourceError,
);

console.log(
  "OfficialSkills 来源解析审计通过：目录链接、命令回退、编码路径、歧义、域名限制和瞬时失败均符合预期",
);
