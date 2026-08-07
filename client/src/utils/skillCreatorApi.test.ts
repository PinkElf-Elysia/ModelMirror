import { afterEach, describe, expect, it, vi } from "vitest";
import {
  approveSkillCreatorProposal,
  copySkillCreatorSession,
  createSkillCreatorSession,
  generateSkillCreatorProposal,
  saveSkillCreatorDraft,
  updateSkillCreatorSession,
  type SkillCreatorDraft,
  type SkillCreatorProposal,
  type SkillCreatorSession,
} from "./skillCreatorApi";

function jsonResponse(payload: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  }));
}

const session: SkillCreatorSession = {
  session_id: "creator_1",
  session_revision: 3,
  draft_state_revision: 2,
  mode: "blank",
  assistant_agent_id: "skill-creator-assistant-v1",
  intent: "整理 PDF 证据",
  positive_examples: ["比较两份 PDF"],
  near_miss_examples: [],
  expected_output: "中文对比表",
  success_criteria: ["包含页码"],
  selected_evidence: [],
  current_revision: 2,
  current_digest: "a".repeat(64),
  draft_id: "draft_1",
  state: "editing_draft",
  created_at: 1,
  updated_at: 2,
};

const draft: SkillCreatorDraft = {
  draft_id: "draft_1",
  root_name: "compare-pdf",
  name: "compare-pdf",
  slug: "compare-pdf",
  description: "比较 PDF 并保留证据页码。",
  skill_markdown: "---\nname: compare-pdf\ndescription: 比较 PDF 并保留证据页码。\n---\n\n# Compare",
  files: {},
  status: "draft",
  revision: 2,
  content_revision: 2,
  content_digest: "a".repeat(64),
  quality_required: true,
  quality_status: "not_evaluated",
};

const proposal: SkillCreatorProposal = {
  proposal_id: "proposal_1",
  kind: "skill_create",
  title: "创建 PDF 对比 Skill",
  status: "pending",
  revision: 2,
  creator_session_id: session.session_id,
  apply_key: `apply_${"b".repeat(40)}`,
  payload_digest: "c".repeat(64),
  content_digest: "a".repeat(64),
  payload: draft,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("skillCreatorApi", () => {
  it("keeps trusted Xpert linkage in a typed session request", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({ session }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await createSkillCreatorSession({
      mode: "run",
      source_kind: "xpert_chat",
      source_task_id: "task_1",
      source_run_id: "run_1",
      source_xpert_id: "xpert_1",
      source_conversation_id: "conversation_1",
      source_message_id: "message_1",
    });

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body).toMatchObject({
      source_xpert_id: "xpert_1",
      source_conversation_id: "conversation_1",
      source_message_id: "message_1",
    });
  });

  it("sends both session and immutable draft consistency guards", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({ session: { ...session, draft } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await saveSkillCreatorDraft(session, draft, draft);

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body).toMatchObject({
      expected_session_revision: 3,
      expected_revision: 2,
      expected_digest: "a".repeat(64),
      name: "compare-pdf",
      slug: "compare-pdf",
    });
    expect(body).not.toHaveProperty("package");
  });

  it("guards generation with the current session revision", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({ session, proposal }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await generateSkillCreatorProposal(session);

    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      expected_session_revision: 3,
    });
  });

  it("accepts an approve envelope and sends the trusted apply key", async () => {
    const approved = { ...proposal, status: "approved" as const };
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({ proposal: approved }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(approveSkillCreatorProposal(proposal)).resolves.toEqual(approved);
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toMatchObject({
      revision: 2,
      apply_key: proposal.apply_key,
    });
  });

  it("preserves structured issue codes from a conflict response", async () => {
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse({
      detail: {
        code: "skill_creator_revision_conflict",
        message: "会话已更新",
        issues: [{
          code: "skill_name_invalid",
          message: "名称格式错误",
          severity: "error",
          path: "SKILL.md",
          line: 2,
        }],
      },
    }, 409)));

    await expect(updateSkillCreatorSession("creator_1", {
      expected_session_revision: 3,
      intent: "new",
    })).rejects.toMatchObject({
      status: 409,
      code: "skill_creator_revision_conflict",
      issues: [expect.objectContaining({ code: "skill_name_invalid", line: 2 })],
    });
  });

  it("confirms empty blank evidence before copying a conflicting draft", async () => {
    const copiedSession = {
      ...session,
      session_id: "creator_copy",
      session_revision: 1,
      draft_id: null,
      draft: null,
    };
    const updatedSession = { ...copiedSession, session_revision: 2 };
    const confirmedSession = { ...updatedSession, session_revision: 3 };
    const copiedDraft = {
      ...draft,
      draft_id: "draft_copy",
      revision: 1,
      content_revision: 1,
    };
    const withDraft = {
      ...confirmedSession,
      session_revision: 4,
      draft_id: copiedDraft.draft_id,
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const call = fetchMock.mock.calls.length;
      if (url.endsWith("/source-preview")) {
        return jsonResponse({
          preview_fingerprint: "blank-preview-fingerprint",
          source_kind: "blank",
          source_task_id: null,
          source_run_id: null,
          candidates: [],
        });
      }
      if (url.endsWith("/evidence")) {
        return jsonResponse({ session: confirmedSession });
      }
      if (url.endsWith("/draft") && init?.method === "POST") {
        return jsonResponse({ session: withDraft, draft: copiedDraft });
      }
      if (url.endsWith("/draft") && init?.method === "PUT") {
        return jsonResponse({ session: withDraft, draft: copiedDraft });
      }
      if (call === 1) return jsonResponse({ session: copiedSession });
      return jsonResponse({ session: updatedSession });
    });
    vi.stubGlobal("fetch", fetchMock);

    await copySkillCreatorSession(session, draft);

    const urls = fetchMock.mock.calls.map(([input]) => String(input));
    expect(urls.findIndex((url) => url.endsWith("/source-preview"))).toBeLessThan(
      urls.findIndex((url) => url.endsWith("/draft")),
    );
    const evidenceCall = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith("/evidence")
    );
    expect(JSON.parse(String(evidenceCall?.[1]?.body))).toEqual({
      expected_session_revision: 2,
      preview_fingerprint: "blank-preview-fingerprint",
      candidate_ids: [],
    });
  });
});
