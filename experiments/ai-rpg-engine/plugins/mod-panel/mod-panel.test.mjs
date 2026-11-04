import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { modCatalog, resolveMods, assembleMods } from "./catalog.mjs";
import { start } from "../../studio/server.mjs";
import { configFetch, installCustomConfig } from "../custom-config/host.mjs";
import { canonical, sha } from "../catalog.mjs";

test("bundled samples are card-specific and assemble whole documents in catalog order", async () => {
  const all = await modCatalog();
  assert.deepEqual(
    all.map((m) => m.name),
    ["实力系统", "世界观", "王朝管理", "防止绝望/保持理智/加强逻辑"],
  );
  const messages = [
    { role: "system", content: "原卡" },
    { role: "user", content: "开局" },
  ];
  const mods = await resolveMods("ancient", all.map((m) => m.id).reverse());
  assert.deepEqual(assembleMods(messages, mods), [
    {
      role: "system",
      content: ["原卡", ...all.map((m) => m.text)].join("\n\n"),
    },
    messages[1],
  ]);
  assert.equal(messages[0].content, "原卡");
  for (const ids of [[all[0].id, all[0].id], ["../other"], []])
    await assert.rejects(resolveMods("ancient", ids));
  await assert.rejects(resolveMods("god", [all[0].id]));
  for (const card of [
    "earth",
    "god",
    "ancient",
    "xingjian",
    "cultivation",
    "lord-of-mysteries",
  ])
    assert.deepEqual(await resolveMods(card, ["common.rationality.v1"]), [
      all[3],
    ]);
});

test("ancient archive sends three dedicated mods plus a common mod, one remaining mod, then none", async (t) => {
  const directory = await mkdtemp(join(tmpdir(), "mod-panel-"));
  const seen = [];
  const model = "google/gemini-3.8-flash";
  const parameters = { temperature: 0.7, top_p: 0.8, max_tokens: 16384 };
  const fake = async (url, options) => {
    if (options.method === "GET")
      return Response.json({
        models: [
          {
            name: "本地样例",
            model,
            parameters,
            available: true,
            selectionId: "fixture",
            selectionRevision: "v1",
          },
        ],
      });
    const { customStoryMessages, ...base } = JSON.parse(options.body);
    const messages = customStoryMessages ?? base.messages;
    seen.push({ base, messages });
    const raw = "本地样例回执",
      sse = "data: [DONE]\n\n";
    return Response.json({
      raw,
      sse,
      receipt: {
        gateway: "rpg_scoped",
        sessionId: base.sessionId,
        requestId: base.requestId,
        selectionId: "fixture",
        selectionRevision: "v1",
        requestedModel: model,
        actualModel: model,
        parameters,
        status: "complete",
        error: null,
        dispatched: true,
        retries: 0,
        requestHash: sha(canonical(base)),
        effectiveRequestHash: sha(canonical({ ...base, messages })),
        rawHash: sha(raw),
        sseHash: sha(sse),
      },
    });
  };
  const { server } = await start({
    port: 0,
    directory,
    archiveControls: {
      ancient: {
        enabled: true,
        limit: 8,
        baseURL: "http://server:8000",
        serviceToken: "synthetic-offline-mod-test-credential-000",
        fetcher: (u, o) => configFetch(u, o, fake),
      },
    },
  });
  t.after(async () => {
    await new Promise((ok) => server.close(ok));
    await rm(directory, { recursive: true, force: true });
  });
  const origin = "http://127.0.0.1:" + server.address().port;
  installCustomConfig(server, [origin], directory);
  const route = origin + "/rpg-app/ancient/chat/api/sessions";
  const post = (url, value, headers = {}) =>
    fetch(url, {
      method: "POST",
      headers: {
        Origin: origin,
        "Content-Type": "application/json",
        ...headers,
      },
      body: JSON.stringify(value),
    });
  const list = await (
    await fetch(origin + "/rpg-app/mod-panel/catalog")
  ).json();
  const ids = list.items.map((m) => m.id);
  const opening = "架空王朝的新君在武馆校阅禁军。";
  let session = await (await post(route, { opening, name: "装配样例" })).json();
  assert.ok(session.id);
  const path = route + "/" + session.id;
  for (const [i, selection] of [ids, [ids[2]], []].entries()) {
    const policy = await (await fetch(path + "/memory-palace")).json();
    const request = {
      operationId: randomUUID(),
      expectedSessionRevision: session.revision,
      expectedMemoryRevision: policy.memoryRevision,
      expectedContextPolicyRevision: policy.effectivePolicy.revision,
      input: i ? "查看国库。" : opening,
    };
    const h = { "X-Rpg-Mod-Panel": "1" };
    if (i === 0) {
      assert.equal(
        (await post(path + "/send", { modIds: ["unknown"], request }, h))
          .status,
        400,
      );
      assert.equal(
        (
          await post(
            path + "/send",
            { modIds: ids, request },
            { ...h, Origin: "https://bad.invalid" },
          )
        ).status,
        403,
      );
      assert.equal(
        (
          await post(
            path.replace("/ancient/", "/cultivation/") + "/send",
            { modIds: ids, request },
            h,
          )
        ).status,
        400,
      );
      assert.equal(seen.length, 0);
    }
    const response = await post(
      path + "/send",
      selection.length ? { modIds: selection, request } : request,
      selection.length ? h : {},
    );
    assert.equal(response.status, 200);
    session = await response.json();
    assert.equal(session.turns.at(-1)?.raw, "本地样例回执");
    assert.equal(seen.length, i + 1);
    const { base, messages } = seen[i];
    assert.equal(
      messages[0].content,
      [
        base.messages[0].content,
        ...list.items
          .filter((m) => selection.includes(m.id))
          .map((m) => m.text),
      ].join("\n\n"),
    );
    assert.deepEqual(messages.slice(1), base.messages.slice(1));
  }
});
