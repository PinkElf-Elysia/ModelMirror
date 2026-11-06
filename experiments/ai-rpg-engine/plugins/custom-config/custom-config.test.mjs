import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { once } from "node:events";
import { createHash } from "node:crypto";
import { empty, encode, decode, validate } from "./public/config.mjs";
import { newWorldEntry, mergeWorldbook } from "./public/worldbook-ui.mjs";
import { runJob } from "./public/engine.mjs";
import { installCustomConfig, configFetch } from "./host.mjs";
import { project } from "./node-job.mjs";
import { canonical } from "../catalog.mjs";
const sys = { role: "system", content: "card system" },
  user = { role: "user", content: "visit" };
const id = "b154280a-af83-46ee-af91-ac83ff290200",
  key = "operation-key";
const hash = (v) => createHash("sha256").update(canonical(v)).digest("hex");
function fixture() {
  const g = empty(),
    s = empty();
  g.prefix = "雾港";
  s.suffix = "结尾";
  s.usePrefill = true;
  s.prefill = "开头";
  const e = newWorldEntry(g.worldbook.defaults, "port");
  e.body = "港口设定";
  e.keywords = ["雾港"];
  g.worldbook.entries.push(e);
  g.rules = [
    {
      id: "filter",
      name: "display",
      enabled: true,
      scope: "assistant",
      renderOnly: true,
      pattern: "old",
      flags: "g",
      replacement: "new",
    },
  ];
  return [g, s];
}
test("local exchange, imported identifiers, editor template merging", () => {
  const [g] = fixture();
  assert.deepEqual(decode(encode(g)), g);
  const merged = mergeWorldbook(g.worldbook, g.worldbook, () => "port2");
  assert.equal(merged.entries.length, 2);
  assert.throws(() =>
    validate({
      ...g,
      worldbook: {
        ...g.worldbook,
        defaults: { ...g.worldbook.defaults, id: 'unsafe"' },
      },
    }),
  );
  assert.throws(() => decode('{"format":"other"}'));
});
test("global and session prompts, wrapped keyword trigger, display only and history replacement", async () => {
  const configs = fixture(),
    messages = [sys, user, { role: "assistant", content: "old" }, user];
  const m = await project({ kind: "story", configs, messages });
  assert.deepEqual(m, [
    { role: "system", content: "card system\n\n港口设定" },
    user,
    { role: "assistant", content: "old" },
    { role: "user", content: "雾港\n\nvisit\n\n结尾" },
    { role: "assistant", content: "开头" },
  ]);
  assert.equal(
    runJob({ kind: "display", configs, role: "assistant", source: "old" }),
    "new",
  );
  configs[0].rules[0].renderOnly = false;
  assert.equal(runJob({ kind: "story", configs, messages })[2].content, "new");
  assert.equal(messages[2].content, "old");
});
test("all six card chats and legacy routes scope configuration to one story; auxiliaries remain unaffected", async (t) => {
  const seen = [];
  const origin = "http://127.0.0.1:12345";
  const server = http.createServer(async (req, res) => {
    const chunks = [];
    for await (const c of req) chunks.push(c);
    const original = JSON.parse(Buffer.concat(chunks));
    assert(!original.profiles);
    const storyId = req.headers["test-story-id"];
    for (const requestId of [storyId, "auxiliary"]) {
      const body = JSON.stringify({
        requestId,
        sessionId: id,
        messages: [sys, user],
      });
      await configFetch(
        "http://server/api/rpg/v1/chat/completions",
        { method: "POST", body },
        async (_url, options) => {
          const v = JSON.parse(options.body);
          seen.push(v);
          const { customStoryMessages, ...base } = v;
          return Response.json({
            receipt: {
              effectiveRequestHash: hash({
                ...base,
                messages: customStoryMessages ?? base.messages,
              }),
            },
          });
        },
      );
    }
    res.end("done");
  });
  installCustomConfig(server, [origin]);
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(() => server.close());
  const base = "http://127.0.0.1:" + server.address().port;
  for (const [path, request, storyId] of [
    ...["ancient/chat", "xingjian", "lord-of-mysteries/chat"].map(card=>[
      "/"+card+"/api/sessions/"+id+"/send",
      {operationId:key},
      hash({id,key,stageId:"story"}),
    ]),
    [
      "/cultivation/chat/api/sessions/" + id + "/send",
      { operationId: key },
      hash({ id, key, stageId: "story" }),
    ],
    [
      "/earth/api/sessions/" + id + "/send",
      { operationId: key },
      hash({ id, key, stageId: "story" }),
    ],
    [
      "/god/api/sessions/" + id + "/send",
      { operationId: key },
      hash({ id, key, purpose: "story" }),
    ],
    [
      "/cultivation/api/sessions/" + id + "/turns",
      { revision: 3 },
      id + "-4-story",
    ],
    ...["earth", "god", "cultivation"].map((card) => [
      "/cultivation/api/summary-v2/" + card + "/sessions/" + id,
      { action: "send", revision: 3 },
      id + "-3-story",
    ]),
  ]) {
    const req = {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Origin: origin,
        "X-Rpg-Custom-Config": "1",
        "test-story-id": storyId,
      },
      body: JSON.stringify({ profiles: fixture(), request }),
    };
    const r = await fetch(base + "/rpg-app" + path, req);
    assert.equal(r.status, 200);
    await r.text();
    assert.equal(seen.at(-2).customStoryMessages.at(-1).content, "开头");
    assert.equal(seen.at(-1).customStoryMessages, undefined);
    if (path.endsWith("/send")) {
      const combined = await fetch(base + "/rpg-app" + path, {
        ...req,
        headers: { ...req.headers, "X-Rpg-Mod-Panel": "1" },
        body: JSON.stringify({
          profiles: fixture(),
          modIds: ["common.rationality.v1"],
          request,
        }),
      });
      assert.equal(combined.status, 200);
      await combined.text();
      assert.match(
        seen.at(-2).customStoryMessages[0].content,
        /角色核心行为逻辑框架/,
      );
      assert.equal(seen.at(-2).customStoryMessages.at(-1).content, "开头");
      assert.equal(seen.at(-1).customStoryMessages, undefined);
    }
    const forbidden = await fetch(base + "/rpg-app" + path, {
      ...req,
      headers: { ...req.headers, Origin: "https://other.invalid" },
    });
    assert.equal(forbidden.status, 403);
  }
});

test("non-display rules project only structured history fields for small summaries and memory", () => {
  const configs = fixture(),
    original = [
      sys,
      {
        role: "user",
        content: JSON.stringify({
          entries: [{ name: "old" }],
          turns: [{ user: "old", assistant: "old" }],
        }),
      },
    ];
  const job = { kind: "memory-source", configs, messages: original };
  assert.deepEqual(runJob(job), original);
  configs[0].rules[0].renderOnly = false;
  const data = JSON.parse(runJob(job)[1].content);
  assert.deepEqual(data, {
    entries: [{ name: "old" }],
    turns: [{ user: "old", assistant: "new" }],
  });
});
