import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import remarkGfm from "remark-gfm";
import AdvancedParamsPanel, {
  type ChatAdvancedParams,
} from "../components/AdvancedParamsPanel";
import BrandLogo from "../components/BrandLogo";
import {
  federationFallbackModelId,
  federationRouteId,
} from "../components/FederationRouterCard";
import PromptSidebar from "../components/PromptSidebar";
import ResourceNav from "../components/ResourceNav";
import { WorldGenerationPanel } from "../components/world/WorldGenerationPanel";
import { useModelPreference } from "../context/ModelPreferenceContext";
import { models } from "../data/models";
import { recruitmentTheme } from "../theme/recruitmentTheme";
import { compressImage } from "../utils/compressImage";
import {
  downloadImage,
  extractImages,
  filenameForImage,
  svgDataUrlToPng,
  type ExtractedImageKind,
} from "../utils/extractImages";
import {
  AGENT_DEFAULT_MODEL_NOTICE_KEY,
  type AgentInterviewPayload,
  readAgentInterview,
} from "../utils/agentInterview";
import { deriveProviderFromModel } from "../utils/userFriendlyText";
import {
  fetchChatStream,
  type ChatApiMessage,
  type ChatMessageContent,
  type ChatRole,
} from "../utils/fetchChatStream";

interface UploadedImage {
  id: string;
  name: string;
  url: string;
}

type LightboxKind = ExtractedImageKind | "upload";

interface LightboxItem {
  src: string;
  kind: LightboxKind;
  name: string;
}

interface ChatMessage {
  id: string;
  role: Exclude<ChatRole, "system">;
  content: ChatMessageContent;
  displayContent: string;
  images?: UploadedImage[];
}

interface KnowledgeBase {
  id: string;
  name: string;
  document_count: number;
}

interface KnowledgeBaseListResponse {
  knowledge_bases: KnowledgeBase[];
}

interface RagSource {
  document_name: string;
  text: string;
  score: number;
}

interface RagQueryResponse {
  answer: string;
  sources: RagSource[];
}

interface InstalledSkill {
  skill_id: string;
  name: string;
  description: string;
  repo_url: string;
  sub_path: string;
  installed_at: number;
}

interface InstalledSkillsResponse {
  skills: InstalledSkill[];
}

interface SkillContentResponse {
  skill_id: string;
  content: string;
}

const modalityLabels: Record<string, string> = {
  text: "文本",
  image: "图片",
  audio: "音频",
  video: "视频",
};

const SUPER_PROMPT_PREFIX = `# 角色：超级提示词架构师 (Super Prompt Architect)

你是一位世界顶级的AI交互设计师和提示词工程师。你的使命是运用D.E.E.P.方法论，将用户任何模糊的、初步的想法，转化为极其清晰、结构化、能激发AI最佳表现的“黄金提示词”。你像一个耐心的导师，引导用户完成这个过程。

## D.E.E.P. 核心工作流（必须遵循）

### 第一步：D (Determine) — 模式判断与启动
当收到用户的初步需求时，首先快速判断其复杂度，并以此决定进入哪种模式。

**模式 1：快速模式 (Quick Mode)**
- **触发条件：** 需求简单、直接、无复杂逻辑。如：写一封简短的感谢信、翻译一句话、解释一个概念。
- **操作：** 快速应用核心技巧（设定角色+清晰指令+简洁格式），直接生成优化后的提示词，然后交付。

**模式 2：深度模式 (Deep Mode)**
- **触发条件：** 需求复杂、多步骤、专业性强、需处理数据。如：制定市场策略、分析合同风险、构建代码审查流程。
- **操作：** 正式启动 E.E.P. 流程。你必须在开始时告知用户：“这是一个复杂任务，我建议我们花几分钟深入沟通，这能确保最终效果提升数倍。我们开始吧？”

### 第二步：E (Explore) — 结构化探索与澄清
在深度模式下，你必须以极其耐心的态度，每次只问1-2个问题，一步步引导用户厘清目标与上下文、角色与受众、行动与格式、示例校准四个维度，并分别产出 <context_summary>、<role_audience>、<action_format>、<examples> 标签内容等待用户确认。

### 第三步：E (Engineer) — 黄金提示词构建
基于探索阶段的全部产出，严格遵循五段式架构组装最终提示词：角色与目标、背景与数据、行动与格式规则、思维链与防幻觉机制、少样本示例。

必须嵌入：
- 请在<thinking>标签中一步步推理，再在<answer>中给出最终答案。
- 仅基于<reference_doc>中的信息作答。如果不确定，请直接说明“我不知道”，严禁猜测。

### 第四步：P (Present) — 呈现与交付
对于简单请求，直接给出优化后的提示词和简短改进说明。对于复杂请求，完整呈现五段式结构提示词，并附加关键技巧应用、平台建议和首次运行指导。

用户的需求是：`;

function createId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }

  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function defaultAdvancedParams(maxTokenLimit = 2048): ChatAdvancedParams {
  return {
    temperature: 0.7,
    topP: 1,
    maxTokens: Math.min(2048, maxTokenLimit),
    seed: "",
    stopSequences: "",
  };
}

function advancedParamsStorageKey(modelId: string) {
  return `modelmirror-chat-params:${modelId}`;
}

function parseStopSequences(value: string) {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function decodeModelId(value: string | undefined) {
  if (!value) return "";

  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function wrapWithSuperPrompt(content: string) {
  return `${SUPER_PROMPT_PREFIX}${content || "请基于我上传的图片生成一个高质量提示词。"}`;
}

async function readApiError(response: Response) {
  try {
    const data = (await response.json()) as { detail?: string; error?: string };
    return data.detail ?? data.error ?? `请求失败：${response.status}`;
  } catch {
    return `请求失败：${response.status}`;
  }
}

function formatRagAnswer(data: RagQueryResponse) {
  if (data.sources.length === 0) return data.answer;

  const sources = data.sources
    .map((source, index) => {
      const preview =
        source.text.length > 180 ? `${source.text.slice(0, 180)}...` : source.text;
      return `${index + 1}. **${source.document_name}**（相关度 ${source.score.toFixed(2)}）\n> ${preview}`;
    })
    .join("\n\n");

  return `${data.answer}\n\n---\n**引用来源**\n\n${sources}`;
}

function buildUserContent(
  text: string,
  images: UploadedImage[],
  superPromptMode: boolean,
): ChatMessageContent {
  const outgoingText = superPromptMode ? wrapWithSuperPrompt(text) : text;

  if (images.length === 0) return outgoingText;

  return [
    ...(outgoingText ? [{ type: "text" as const, text: outgoingText }] : []),
    ...images.map((image) => ({
      type: "image_url" as const,
      image_url: { url: image.url },
    })),
  ];
}

function inferLightboxKind(src: string): LightboxKind {
  if (src.startsWith("data:image/svg+xml")) return "svg";
  if (src.startsWith("data:image/") || src.startsWith("blob:")) return "data";
  return "url";
}

function extractedKindForLightbox(
  kind: LightboxKind,
  src: string,
): ExtractedImageKind {
  if (kind === "upload") return inferLightboxKind(src) === "svg" ? "svg" : "data";
  return kind;
}

function lightboxFilename(item: LightboxItem): string {
  if (item.kind === "upload" && /\.[A-Za-z0-9]+$/.test(item.name)) {
    return item.name.replace(/[^A-Za-z0-9_.-]+/g, "_").slice(0, 80);
  }
  const kind = extractedKindForLightbox(item.kind, item.src);
  return filenameForImage(
    {
      id: "lightbox",
      kind,
      name: item.name,
      source: item.src,
      raw: item.src,
    },
    1,
  );
}

function markdownComponents(
  onImageClick: (src: string, meta?: Partial<LightboxItem>) => void,
  isUser: boolean,
) {
  return {
    p: ({ children }: { children?: React.ReactNode }) => (
      <p className="mb-3 last:mb-0">{children}</p>
    ),
    ul: ({ children }: { children?: React.ReactNode }) => (
      <ul className="mb-3 list-disc space-y-1 pl-5 last:mb-0">{children}</ul>
    ),
    ol: ({ children }: { children?: React.ReactNode }) => (
      <ol className="mb-3 list-decimal space-y-1 pl-5 last:mb-0">{children}</ol>
    ),
    strong: ({ children }: { children?: React.ReactNode }) => (
      <strong className={`font-semibold ${isUser ? "text-ink-950" : "text-white"}`}>
        {children}
      </strong>
    ),
    pre: ({ children }: { children?: React.ReactNode }) => (
      <pre className="mb-3 overflow-x-auto rounded-lg border border-white/10 bg-ink-950/90 p-3 text-xs leading-5 text-slate-100 shadow-inner last:mb-0">
        {children}
      </pre>
    ),
    code: ({ children }: { children?: React.ReactNode }) => (
      <code className="rounded bg-white/10 px-1.5 py-0.5 text-[0.9em] text-sky-100">
        {children}
      </code>
    ),
    img: ({ src, alt }: { src?: string; alt?: string }) => (
      <button
        className="my-2 block overflow-hidden rounded-lg border border-white/10 bg-white/[0.06] transition hover:border-brand-300/30"
        onClick={() =>
          src &&
          onImageClick(src, {
            kind: inferLightboxKind(src),
            name: alt ?? "Markdown 图片",
          })
        }
        type="button"
      >
        <img
          alt={alt ?? "模型输出图片"}
          className="max-h-72 max-w-full object-contain"
          src={src}
        />
      </button>
    ),
    a: ({
      children,
      href,
    }: {
      children?: React.ReactNode;
      href?: string;
    }) => (
      <a
        className="text-sky-300 underline decoration-sky-300/40 underline-offset-4 transition hover:text-sky-100"
        href={href}
        rel="noreferrer"
        target="_blank"
      >
        {children}
      </a>
    ),
  };
}

function MessageBubble({
  message,
  isSending,
  onImageClick,
}: {
  message: ChatMessage;
  isSending: boolean;
  onImageClick: (src: string, meta?: Partial<LightboxItem>) => void;
}) {
  const isUser = message.role === "user";
  const { text: cleanedContent, images: extractedImages } = useMemo(
    () => extractImages(message.displayContent),
    [message.displayContent],
  );

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[88%] px-4 py-3 text-sm leading-6 shadow-prism sm:max-w-[76%] ${
          isUser
            ? "rounded-lg rounded-br-sm bg-brand-300 text-ink-950 shadow-neon"
            : "rounded-lg rounded-bl-sm border border-white/10 bg-surface-850/90 text-slate-100"
        }`}
      >
        <p
          className={`mb-2 text-[11px] font-semibold ${
            isUser ? "text-ink-800" : "text-hire-200"
          }`}
        >
          {isUser ? "面试官说" : "候选人说"}
        </p>
        {message.images && message.images.length > 0 ? (
          <div className="mb-3 flex flex-wrap gap-2">
            {message.images.map((image) => (
              <button
                className="overflow-hidden rounded-lg border border-white/20 bg-white/10 transition hover:border-brand-300/40"
                key={image.id}
                onClick={() =>
                  onImageClick(image.url, {
                    kind: "upload",
                    name: image.name,
                  })
                }
                type="button"
              >
                <img
                  alt={image.name}
                  className="h-32 w-32 object-cover sm:h-40 sm:w-40"
                  src={image.url}
                />
              </button>
            ))}
          </div>
        ) : null}

        {extractedImages.length > 0 ? (
          <div className="mb-3 flex flex-wrap gap-2">
            {extractedImages.map((image, index) => (
              <button
                aria-label={`预览 ${image.name}`}
                className="group overflow-hidden rounded-lg border border-white/15 bg-white/[0.055] text-left transition hover:border-brand-300/40 focus:outline-none focus:ring-4 focus:ring-brand-300/10"
                key={image.id}
                onClick={() =>
                  onImageClick(image.source, {
                    kind: image.kind,
                    name: image.name,
                  })
                }
                type="button"
              >
                <img
                  alt={image.name}
                  className="h-32 w-32 bg-ink-950/70 object-contain sm:h-40 sm:w-40"
                  src={image.source}
                />
                <div className="border-t border-white/10 px-2 py-1">
                  <p className="truncate text-[11px] font-semibold text-slate-100">
                    {filenameForImage(image, index + 1)}
                  </p>
                  <p className="text-[10px] text-slate-400">
                    {image.kind === "svg"
                      ? "SVG"
                      : image.kind === "data"
                        ? "Data URL"
                        : "URL"}
                  </p>
                </div>
              </button>
            ))}
          </div>
        ) : null}

        {cleanedContent ? (
          <ReactMarkdown
            components={markdownComponents(onImageClick, isUser)}
            remarkPlugins={[remarkGfm]}
          >
            {cleanedContent}
          </ReactMarkdown>
        ) : extractedImages.length > 0 ? (
          <p className="text-xs text-slate-400">图片已从文本中分离，可点击预览或下载。</p>
        ) : isSending && !isUser ? (
          <span className="inline-flex items-center gap-2 text-slate-300">
            思考中
            <span className="h-2 w-2 animate-pulse rounded-full bg-brand-300 shadow-[0_0_16px_rgba(34,211,238,0.7)]" />
          </span>
        ) : null}
      </div>
    </div>
  );
}

export default function ChatPage() {
  const { modelId } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { setPreferredModelId } = useModelPreference();
  const decodedModelId = useMemo(() => decodeModelId(modelId), [modelId]);
  const isFederationRoute = decodedModelId === federationRouteId;
  const model = useMemo(
    () => {
      if (isFederationRoute) {
        return (
          models.find((item) => item.id === federationFallbackModelId) ??
          models.find((item) => item.id === "openai/gpt-4o") ??
          models[0]
        );
      }

      return models.find((item) => item.id === decodedModelId);
    },
    [decodedModelId, isFederationRoute],
  );
  const agentInterview = useMemo<AgentInterviewPayload | null>(() => {
    const agentId = searchParams.get("agentId");
    const stored = readAgentInterview(agentId);
    if (stored) return stored;

    const agentPrompt = searchParams.get("agentPrompt");
    if (!agentPrompt) return null;

    return {
      agentId: agentId ?? "url-agent",
      agentName: searchParams.get("agentName") ?? "AI 专家",
      department: searchParams.get("agentDepartment") ?? "AI 人才市场",
      expertise: searchParams.get("agentExpertise") ?? "按指定角色进入面试",
      prompt: agentPrompt,
      sourceUrl: "",
    };
  }, [searchParams]);
  const maxTokenLimit = model
    ? Math.min(128000, Math.max(1, model.context_length))
    : 2048;
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [uploadedImages, setUploadedImages] = useState<UploadedImage[]>([]);
  const [isSending, setIsSending] = useState(false);
  const [isUploadingImage, setIsUploadingImage] = useState(false);
  const [isDraggingImage, setIsDraggingImage] = useState(false);
  const [error, setError] = useState("");
  const [lightboxImage, setLightboxImage] = useState<LightboxItem | null>(null);
  const [promptSidebarOpen, setPromptSidebarOpen] = useState(false);
  const [superPromptMode, setSuperPromptMode] = useState(false);
  const [advancedParamsOpen, setAdvancedParamsOpen] = useState(
    () => searchParams.get("advanced") === "1",
  );
  const [advancedParams, setAdvancedParams] = useState<ChatAdvancedParams>(() =>
    defaultAdvancedParams(),
  );
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [selectedKnowledgeBaseId, setSelectedKnowledgeBaseId] = useState("");
  const [isLoadingKnowledgeBases, setIsLoadingKnowledgeBases] = useState(false);
  const [installedSkills, setInstalledSkills] = useState<InstalledSkill[]>([]);
  const [selectedSkillId, setSelectedSkillId] = useState("");
  const [skillContentCache, setSkillContentCache] = useState<Record<string, string>>({});
  const [isLoadingSkills, setIsLoadingSkills] = useState(false);
  const [modelSwitchNotice, setModelSwitchNotice] = useState("");
  const [agentDefaultModelNotice, setAgentDefaultModelNotice] = useState("");
  const chatSectionRef = useRef<HTMLElement>(null);
  const messageViewportRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const openLightbox = useCallback(
    (src: string, meta?: Partial<LightboxItem>) => {
      setLightboxImage({
        src,
        kind: meta?.kind ?? inferLightboxKind(src),
        name: meta?.name ?? "图片",
      });
    },
    [],
  );

  useEffect(() => {
    document.title = agentInterview
      ? `模镜面试间 - ${agentInterview.agentName}`
      : isFederationRoute
        ? "模镜面试间 - 模型联邦智能路由器"
      : model
        ? `模镜面试间 - ${model.name}`
        : "模镜 - AI 牛马招聘会";
  }, [agentInterview, isFederationRoute, model]);

  useEffect(() => {
    if (window.matchMedia("(min-width: 1024px)").matches) {
      setPromptSidebarOpen(true);
    }
  }, []);

  useEffect(() => {
    void loadKnowledgeBases();
  }, []);

  useEffect(() => {
    void loadInstalledSkills();
  }, []);

  function scrollMessagesToBottom(behavior: ScrollBehavior = "smooth") {
    window.requestAnimationFrame(() => {
      const chatSection = chatSectionRef.current;

      if (chatSection) {
        const sectionBottom =
          chatSection.getBoundingClientRect().bottom + window.scrollY;
        const targetTop = Math.max(0, sectionBottom - window.innerHeight);

        window.scrollTo({
          top: targetTop,
          behavior,
        });
      }

      const viewport = messageViewportRef.current;

      if (viewport) {
        viewport.scrollTo({
          top: viewport.scrollHeight,
          behavior,
        });
      }

      scrollRef.current?.scrollIntoView({ behavior, block: "end" });
    });
  }

  useEffect(() => {
    scrollMessagesToBottom("auto");
    const timeoutId = window.setTimeout(() => scrollMessagesToBottom("auto"), 250);

    return () => window.clearTimeout(timeoutId);
  }, [decodedModelId]);

  useEffect(() => {
    scrollMessagesToBottom("smooth");
  }, [messages, isSending]);

  useEffect(() => {
    if (model && !isFederationRoute) {
      setPreferredModelId(model.id);
    }
  }, [isFederationRoute, model, setPreferredModelId]);

  useEffect(() => {
    const notice = window.sessionStorage.getItem(AGENT_DEFAULT_MODEL_NOTICE_KEY);
    if (!notice) return;

    setAgentDefaultModelNotice(notice);
    window.sessionStorage.removeItem(AGENT_DEFAULT_MODEL_NOTICE_KEY);
  }, []);

  useEffect(() => {
    if (!model) return;

    const defaults = defaultAdvancedParams(maxTokenLimit);
    const raw = window.localStorage.getItem(advancedParamsStorageKey(model.id));
    if (!raw) {
      setAdvancedParams(defaults);
      return;
    }

    try {
      const saved = JSON.parse(raw) as Partial<ChatAdvancedParams>;
      setAdvancedParams({
        ...defaults,
        ...saved,
        maxTokens: Math.min(
          maxTokenLimit,
          Math.max(1, Number(saved.maxTokens ?? defaults.maxTokens)),
        ),
      });
    } catch {
      setAdvancedParams(defaults);
    }
  }, [maxTokenLimit, model]);

  async function addImageFiles(files: File[]) {
    const imageFiles = files.filter((file) => file.type.startsWith("image/"));
    if (imageFiles.length === 0) return;

    setError("");
    setIsUploadingImage(true);

    try {
      const compressedImages = await Promise.all(
        imageFiles.map(async (file) => ({
          id: createId(),
          name: file.name,
          url: await compressImage(file),
        })),
      );

      setUploadedImages((current) => [...current, ...compressedImages].slice(0, 4));
    } catch (uploadError) {
      setError(
        uploadError instanceof Error
          ? uploadError.message
          : "图片处理失败，请重试。",
      );
    } finally {
      setIsUploadingImage(false);
    }
  }

  function removeUploadedImage(id: string) {
    setUploadedImages((current) => current.filter((image) => image.id !== id));
  }

  async function loadKnowledgeBases() {
    setIsLoadingKnowledgeBases(true);
    try {
      const response = await fetch("/api/rag/knowledge_bases");
      if (!response.ok) throw new Error(await readApiError(response));
      const data = (await response.json()) as KnowledgeBaseListResponse;
      setKnowledgeBases(data.knowledge_bases);
      if (
        selectedKnowledgeBaseId &&
        !data.knowledge_bases.some((item) => item.id === selectedKnowledgeBaseId)
      ) {
        setSelectedKnowledgeBaseId("");
      }
    } catch (loadError) {
      console.error("知识库列表加载失败", loadError);
    } finally {
      setIsLoadingKnowledgeBases(false);
    }
  }

  async function loadInstalledSkills() {
    setIsLoadingSkills(true);
    try {
      const response = await fetch("/api/skills/installed");
      if (!response.ok) throw new Error(await readApiError(response));
      const data = (await response.json()) as InstalledSkillsResponse;
      setInstalledSkills(data.skills);
      if (
        selectedSkillId &&
        !data.skills.some((skill) => skill.skill_id === selectedSkillId)
      ) {
        setSelectedSkillId("");
      }
    } catch (loadError) {
      console.error("Skill list failed to load", loadError);
    } finally {
      setIsLoadingSkills(false);
    }
  }

  async function loadSkillContent(skillId: string) {
    if (!skillId) return "";
    const cached = skillContentCache[skillId];
    if (cached) return cached;

    const response = await fetch(`/api/skills/${encodeURIComponent(skillId)}/content`);
    if (!response.ok) throw new Error(await readApiError(response));
    const data = (await response.json()) as SkillContentResponse;
    setSkillContentCache((current) => ({
      ...current,
      [skillId]: data.content,
    }));
    return data.content;
  }

  async function handleSkillSelection(skillId: string) {
    setSelectedSkillId(skillId);
    setError("");
    if (!skillId || skillContentCache[skillId]) return;

    try {
      setIsLoadingSkills(true);
      await loadSkillContent(skillId);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Skill 内容加载失败");
      setSelectedSkillId("");
    } finally {
      setIsLoadingSkills(false);
    }
  }

  async function sendMessage(overrideText?: string) {
    const rawText = (overrideText ?? input).trim();
    const images = overrideText ? [] : uploadedImages;
    if ((!rawText && images.length === 0) || isSending || !model) return;

    if (images.length > 0 && !model.input_modalities.includes("image")) {
      setError("当前候选人不接视觉岗面试，请切换支持图片输入的候选人");
      return;
    }

    if (selectedKnowledgeBaseId && images.length > 0) {
      setError("知识库检索模式暂不支持图片问题，请先移除图片或取消知识库选择。");
      return;
    }

    let activeSkillContent = "";
    if (selectedSkillId) {
      try {
        activeSkillContent = await loadSkillContent(selectedSkillId);
      } catch (loadError) {
        setError(loadError instanceof Error ? loadError.message : "Skill 内容加载失败");
        return;
      }
    }

    const userContent = buildUserContent(rawText, images, superPromptMode);
    const userMessage: ChatMessage = {
      id: createId(),
      role: "user",
      content: userContent,
      displayContent: rawText,
      images,
    };
    const assistantId = createId();
    const assistantMessage: ChatMessage = {
      id: assistantId,
      role: "assistant",
      content: "",
      displayContent: "",
    };

    const selectedSkill = installedSkills.find(
      (skill) => skill.skill_id === selectedSkillId,
    );
    const skillSystemMessages: ChatApiMessage[] = activeSkillContent
      ? [
          {
            role: "system",
            content: `当前激活 Skill：${selectedSkill?.name ?? selectedSkillId}\n\n${activeSkillContent}`,
          },
        ]
      : [];
    const systemMessages: ChatApiMessage[] = [
      ...skillSystemMessages,
      ...(agentInterview?.prompt
        ? [{ role: "system" as const, content: agentInterview.prompt }]
        : []),
    ];
    const apiMessages: ChatApiMessage[] = [
      ...systemMessages,
      ...messages.map((message) => ({
        role: message.role,
        content: message.content,
      })),
      { role: "user", content: userContent },
    ];

    setMessages((current) => [...current, userMessage, assistantMessage]);
    setInput("");
    if (!overrideText) setUploadedImages([]);
    setError("");
    setIsSending(true);

    try {
      if (selectedKnowledgeBaseId && rawText) {
        const ragQuestion = activeSkillContent
          ? `请遵循以下 Skill 说明回答，并结合知识库检索结果。\n\n${activeSkillContent}\n\n用户问题：${rawText}`
          : rawText;
        const response = await fetch("/api/rag/query", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            kb_id: selectedKnowledgeBaseId,
            question: ragQuestion,
          }),
        });
        if (!response.ok) throw new Error(await readApiError(response));
        const data = (await response.json()) as RagQueryResponse;
        const answer = formatRagAnswer(data);
        setMessages((current) =>
          current.map((message) =>
            message.id === assistantId
              ? {
                  ...message,
                  content: answer,
                  displayContent: answer,
                }
              : message,
          ),
        );
        return;
      }

      await fetchChatStream({
        modelId: model.id,
        messages: apiMessages,
        temperature: advancedParams.temperature,
        topP: advancedParams.topP,
        maxTokens: advancedParams.maxTokens,
        seed: advancedParams.seed.trim()
          ? Number(advancedParams.seed)
          : undefined,
        stop: parseStopSequences(advancedParams.stopSequences),
        onDelta: (delta) => {
          setMessages((current) =>
            current.map((message) =>
              message.id === assistantId
                ? {
                    ...message,
                    content:
                      typeof message.content === "string"
                        ? message.content + delta
                        : delta,
                    displayContent: message.displayContent + delta,
                  }
                : message,
            ),
          );
        },
      });

      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId && message.displayContent.trim().length === 0
            ? {
                ...message,
                content: "（模型没有返回内容）",
                displayContent: "（模型没有返回内容）",
              }
            : message,
        ),
      );
    } catch (streamError) {
      const message =
        streamError instanceof Error && streamError.message
          ? streamError.message
          : "抱歉，模型暂时无法响应，请稍后重试。";
      setError(message);
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantId
            ? {
                ...item,
                content: "抱歉，模型暂时无法响应，请稍后重试。",
                displayContent: "抱歉，模型暂时无法响应，请稍后重试。",
              }
            : item,
        ),
      );
    } finally {
      setIsSending(false);
    }
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    void sendMessage();
  }

  function handlePaste(event: React.ClipboardEvent<HTMLTextAreaElement>) {
    const files = Array.from(event.clipboardData.files).filter((file) =>
      file.type.startsWith("image/"),
    );
    if (files.length === 0) return;

    event.preventDefault();
    void addImageFiles(files);
  }

  function handleDrop(event: React.DragEvent<HTMLElement>) {
    event.preventDefault();
    setIsDraggingImage(false);
    void addImageFiles(Array.from(event.dataTransfer.files));
  }

  function handleModelChange(nextModelId: string) {
    if (!nextModelId || nextModelId === model?.id) return;

    setPreferredModelId(nextModelId);
    setModelSwitchNotice("已切换当前使用模型；切换模型后对话上下文可能不兼容。");
    setError("");

    const queryString = searchParams.toString();
    navigate(
      `/chat/${encodeURIComponent(nextModelId)}${queryString ? `?${queryString}` : ""}`,
    );
  }

  function handleAdvancedParamsChange(nextParams: ChatAdvancedParams) {
    const normalizedParams: ChatAdvancedParams = {
      ...nextParams,
      temperature: Number(nextParams.temperature.toFixed(2)),
      topP: Number(nextParams.topP.toFixed(2)),
      maxTokens: Math.min(
        maxTokenLimit,
        Math.max(1, Math.round(nextParams.maxTokens)),
      ),
      seed: nextParams.seed,
      stopSequences: nextParams.stopSequences,
    };

    setAdvancedParams(normalizedParams);
    if (model) {
      window.localStorage.setItem(
        advancedParamsStorageKey(model.id),
        JSON.stringify(normalizedParams),
      );
    }
  }

  function resetAdvancedParams() {
    const defaults = defaultAdvancedParams(maxTokenLimit);
    setAdvancedParams(defaults);
    if (model) {
      window.localStorage.removeItem(advancedParamsStorageKey(model.id));
    }
  }

  if (!model) {
    return (
      <main className="museum-grid min-h-screen px-4 py-10 text-slate-100">
        <div className="surface-panel mx-auto max-w-2xl rounded-lg p-8">
          <BrandLogo />
          <h1 className="mt-3 text-2xl font-semibold text-white">候选人走错面试间了</h1>
          <p className="mt-3 text-sm leading-6 text-slate-300">
            请返回招聘会现场重新选择一位可面试的候选人。
          </p>
          <Link
            className="mt-6 inline-flex rounded-full bg-brand-300 px-4 py-2 text-sm font-semibold text-ink-950 transition hover:bg-brand-200"
            to="/models"
          >
            返回招聘会现场
          </Link>
        </div>
      </main>
    );
  }

  const canSend =
    (input.trim().length > 0 || uploadedImages.length > 0) &&
    !isSending &&
    !isUploadingImage;
  const supportsImageInput = model.input_modalities.includes("image");
  const providerName = deriveProviderFromModel(model);
  const displayCandidateName = isFederationRoute
    ? "模型联邦智能路由器"
    : agentInterview?.agentName ?? model.name;
  const displayCandidateDescription = isFederationRoute
    ? "智能路由功能正在紧锣密鼓开发中，当前将使用默认模型为您服务。"
    : agentInterview?.expertise ?? model.description;

  if (model?.worldModel) {
    return (
      <main className="museum-grid min-h-screen pb-24 pt-5 text-slate-100 lg:pt-24">
        <ResourceNav activeResource="models" />
        <div className="mx-auto flex min-h-screen w-full max-w-[1200px] flex-col px-4 py-5 sm:px-6 lg:px-8">
          <header className="border-y border-hire-300/20 bg-ink-950/72 px-4 py-4 backdrop-blur-2xl">
            <Link
              className="inline-flex items-center rounded-full border border-white/10 bg-white/[0.05] px-3 py-1.5 text-sm font-medium text-slate-300 transition hover:border-brand-300/30 hover:bg-brand-300/10 hover:text-brand-100"
              to="/models"
            >
              返回招聘会现场
            </Link>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <span className="inline-flex items-center gap-2 rounded-full border border-emerald-300/30 bg-emerald-300/10 px-3 py-1.5 text-xs font-semibold text-emerald-100">
                <span className="h-2 w-2 animate-pulse rounded-full bg-emerald-300" />
                3D 世界生成
              </span>
            </div>
            <h1 className="mt-3 text-2xl font-semibold tracking-normal text-white sm:text-4xl">
              世界模型：{model.name}
            </h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-300">
              {model.description}
            </p>
          </header>
          <div className="surface-panel mt-5 min-h-[560px] flex-1 overflow-hidden rounded-lg border border-white/10 shadow-prism">
            <WorldGenerationPanel />
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="museum-grid min-h-screen pb-24 pt-5 text-slate-100 lg:pt-24">
      <ResourceNav activeResource={agentInterview ? "agents" : "models"} />
      <div className="mx-auto flex min-h-screen w-full max-w-[1540px] flex-col px-4 py-5 sm:px-6 lg:px-8">
        <header className="sticky top-4 z-30 border-y border-hire-300/20 bg-ink-950/72 px-0 py-4 backdrop-blur-2xl md:flex md:items-center md:justify-between md:gap-6 lg:top-24">
          <div>
            <BrandLogo className="mb-4 lg:hidden" />
            <Link
              className="inline-flex items-center rounded-full border border-white/10 bg-white/[0.05] px-3 py-1.5 text-sm font-medium text-slate-300 transition hover:border-brand-300/30 hover:bg-brand-300/10 hover:text-brand-100"
              to={agentInterview ? "/agents" : "/models"}
            >
              {agentInterview ? "返回 AI 人才市场" : "返回招聘会现场"}
            </Link>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <span className="inline-flex items-center gap-2 rounded-full border border-emerald-300/30 bg-emerald-300/10 px-3 py-1.5 text-xs font-semibold text-emerald-100">
                <span className="h-2 w-2 animate-pulse rounded-full bg-emerald-300" />
                面试中
              </span>
              <span className="rounded-full border border-hire-300/30 bg-hire-300/10 px-3 py-1.5 text-xs font-semibold text-hire-100">
                {agentInterview ? "专家已入场" : "候选人已入场"}
              </span>
              {agentInterview ? (
                <span className="rounded-full border border-brand-300/30 bg-brand-300/10 px-3 py-1.5 text-xs font-semibold text-brand-100">
                  {agentInterview.department}
                </span>
              ) : null}
              {isFederationRoute ? (
                <span className="rounded-full border border-hire-300/30 bg-hire-300/10 px-3 py-1.5 text-xs font-semibold text-hire-100">
                  默认模型代班：{model.name}
                </span>
              ) : null}
            </div>
            <h1 className="mt-3 text-2xl font-semibold tracking-normal text-white sm:text-4xl">
              面试进行中：与 {displayCandidateName} 交谈
            </h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-300">
              {displayCandidateDescription}
            </p>
            {isFederationRoute ? (
              <div className="mt-4 rounded-lg border border-hire-300/25 bg-hire-300/10 px-4 py-3 text-sm leading-6 text-hire-50">
                智能路由功能正在紧锣密鼓开发中，当前将使用默认模型为您服务。
              </div>
            ) : null}
          </div>

          {(agentDefaultModelNotice || modelSwitchNotice) ? (
            <div className="mt-4 space-y-3 md:mt-0">
              {agentDefaultModelNotice ? (
                <div className="rounded-lg border border-brand-300/25 bg-brand-300/10 px-4 py-3 text-sm leading-6 text-brand-50">
                  {agentDefaultModelNotice}
                </div>
              ) : null}
              {modelSwitchNotice ? (
                <div className="flex items-start justify-between gap-4 rounded-lg border border-amber-300/25 bg-amber-300/10 px-4 py-3 text-sm leading-6 text-amber-50">
                  <span>{modelSwitchNotice}</span>
                  <button
                    className="shrink-0 rounded-full border border-amber-200/30 px-2 py-0.5 text-xs font-semibold transition hover:bg-amber-200/10"
                    onClick={() => setModelSwitchNotice("")}
                    type="button"
                  >
                    知道了
                  </button>
                </div>
              ) : null}
            </div>
          ) : null}

          <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-slate-300 md:mt-0 md:justify-end">
            <label className="flex items-center gap-2 rounded-full border border-hire-300/25 bg-hire-300/10 px-3 py-1.5 text-hire-50">
              <span className="font-semibold">当前使用模型</span>
              <select
                className="max-w-[220px] bg-transparent text-xs font-semibold text-white outline-none"
                onChange={(event) => handleModelChange(event.target.value)}
                value={model.id}
              >
                {models.map((item) => (
                  <option
                    className="bg-slate-950 text-white"
                    key={item.id}
                    value={item.id}
                  >
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <span className="rounded-full border border-white/10 bg-white/[0.06] px-3 py-1.5">
              {providerName}
            </span>
            <span className="rounded-full border border-white/10 bg-white/[0.06] px-3 py-1.5">
              上下文 {model.context_length.toLocaleString("zh-CN")}
            </span>
            <span
              className={`rounded-full border px-3 py-1.5 ${
                supportsImageInput
                  ? "border-brand-300/30 bg-brand-300/10 text-brand-100"
                  : "border-white/10 bg-white/[0.06] text-slate-300"
              }`}
            >
              {supportsImageInput ? "支持图片输入" : "仅文本输入"}
            </span>
          </div>
        </header>

        <div className="grid min-w-0 flex-1 gap-5 py-5 lg:grid-cols-[280px_minmax(0,1fr)] xl:grid-cols-[300px_minmax(0,1fr)]">
          <aside className="surface-panel rounded-lg p-5 lg:sticky lg:top-36 lg:h-[calc(100vh-11rem)]">
            <p className="text-sm font-semibold text-white">候选人档案</p>
            <p className="mt-2 text-sm leading-6 text-slate-300">
              {agentInterview
                ? "这场面试会带上智能体的完整岗位人设。刷新页面后会重新开始。"
                : "当前对话只在本页会话中保留。刷新页面后会重新开始。"}
            </p>
            <div className="mt-5 grid grid-cols-2 gap-3 text-sm lg:grid-cols-1">
              <div className="rounded-lg border border-white/10 bg-white/[0.045] p-3">
                <p className="text-xs text-slate-400">输入薪资</p>
                <p className="mt-1 font-semibold text-white">
                  ¥{model.price_cny.input.toFixed(2)}
                </p>
              </div>
              <div className="rounded-lg border border-white/10 bg-white/[0.045] p-3">
                <p className="text-xs text-slate-400">输出薪资</p>
                <p className="mt-1 font-semibold text-white">
                  ¥{model.price_cny.output.toFixed(2)}
                </p>
              </div>
            </div>
            <div className="mt-4 rounded-lg border border-white/10 bg-[linear-gradient(135deg,rgba(36,217,255,0.10),rgba(124,58,237,0.08))] p-3">
              <p className="text-xs text-slate-400">输入模态</p>
              <p className="mt-2 text-sm font-semibold text-white">
                {model.input_modalities
                  .map((modality) => modalityLabels[modality] ?? modality)
                  .join(" / ")}
              </p>
            </div>
          </aside>

          <div className="flex min-w-0 gap-5 overflow-hidden">
            <section
              className={`relative flex min-h-[560px] min-w-0 basis-0 flex-1 flex-col overflow-hidden rounded-lg border bg-surface-900/80 shadow-prism backdrop-blur-xl transition lg:h-[calc(100vh-11rem)] lg:min-h-[560px] ${
                isDraggingImage
                  ? "border-brand-300/70 ring-4 ring-brand-300/10"
                  : "border-white/10"
              }`}
              ref={chatSectionRef}
              onDragLeave={() => setIsDraggingImage(false)}
              onDragOver={(event) => {
                event.preventDefault();
                setIsDraggingImage(true);
              }}
              onDrop={handleDrop}
            >
              {isDraggingImage ? (
                <div className="pointer-events-none absolute inset-4 z-10 flex items-center justify-center rounded-lg border border-dashed border-brand-300/60 bg-brand-300/10 text-sm font-medium text-brand-100 backdrop-blur">
                  松开即可上传图片
                </div>
              ) : null}

              <div
                className="flex-1 overflow-y-auto px-4 py-5 sm:px-6"
                ref={messageViewportRef}
              >
                {messages.length === 0 ? (
                  <div className="flex h-full min-h-[260px] flex-col items-center justify-start pt-20 text-center sm:justify-center sm:pt-0">
                    <img
                      alt="模镜"
                      className="h-16 w-16 rounded-lg object-cover shadow-neon"
                      src="/logo.png"
                    />
                    <h2 className="mt-5 text-xl font-semibold text-white">
                      {isFederationRoute
                        ? "智能路由调度员正在候场..."
                        : agentInterview
                        ? `正在等待 ${agentInterview.agentName} 入场...`
                        : recruitmentTheme.interviewWaiting}
                    </h2>
                    <p className="mt-2 max-w-md text-sm leading-6 text-slate-400">
                      {isFederationRoute
                        ? "先由默认模型代班回答。后续路由上线后，会自动按任务挑选更合适的候选人。"
                        : agentInterview
                        ? "向这位 AI 专家描述你的任务，系统会自动带上他的完整简历和工作方式。"
                        : "输入问题，上传图片，或从右侧面试题库抽一道题开场。"}
                    </p>
                  </div>
                ) : (
                  <div className="space-y-5">
                    {messages.map((message) => (
                      <MessageBubble
                        isSending={isSending}
                        key={message.id}
                        message={message}
                        onImageClick={openLightbox}
                      />
                    ))}
                    <div ref={scrollRef} />
                  </div>
                )}
              </div>

              {error ? (
                <div className="flex flex-col gap-3 border-t border-rose-300/20 bg-rose-400/10 px-4 py-3 text-sm text-rose-100 sm:flex-row sm:items-center sm:justify-between sm:px-6">
                  <span>{error}</span>
                  <button
                    className="w-fit rounded-full border border-rose-200/30 bg-rose-200/10 px-3 py-1.5 text-xs font-semibold text-rose-50 transition hover:bg-rose-200/20"
                    onClick={() => handleModelChange("deepseek/deepseek-chat")}
                    type="button"
                  >
                    切换至国内可用模型
                  </button>
                </div>
              ) : null}

              <AdvancedParamsPanel
                isOpen={advancedParamsOpen}
                maxTokenLimit={maxTokenLimit}
                onChange={handleAdvancedParamsChange}
                onReset={resetAdvancedParams}
                onToggle={() => setAdvancedParamsOpen((current) => !current)}
                params={advancedParams}
              />

              <div className="border-t border-white/10 bg-ink-950/40 p-4 sm:p-5">
                <div className="mb-3 flex flex-col gap-2 rounded-lg border border-white/10 bg-white/[0.045] px-3 py-3 sm:flex-row sm:items-center sm:justify-between">
                  <label className="flex flex-1 flex-col gap-2 text-xs font-semibold text-slate-300 sm:flex-row sm:items-center">
                    <span className="shrink-0 text-hire-100">知识库</span>
                    <select
                      className="min-w-0 flex-1 rounded-full border border-white/10 bg-ink-950/80 px-3 py-2 text-xs font-semibold text-white outline-none transition focus:border-hire-300/50 focus:ring-4 focus:ring-hire-300/10"
                      disabled={isSending || isLoadingKnowledgeBases}
                      onChange={(event) => setSelectedKnowledgeBaseId(event.target.value)}
                      value={selectedKnowledgeBaseId}
                    >
                      <option value="">不使用知识库，直接面试</option>
                      {knowledgeBases.map((kb) => (
                        <option className="bg-slate-950 text-white" key={kb.id} value={kb.id}>
                          {kb.name}（{kb.document_count} 份文档）
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="flex flex-1 flex-col gap-2 text-xs font-semibold text-slate-300 sm:flex-row sm:items-center">
                    <span className="shrink-0 text-brand-100">Skill</span>
                    <select
                      className="min-w-0 flex-1 rounded-full border border-white/10 bg-ink-950/80 px-3 py-2 text-xs font-semibold text-white outline-none transition focus:border-brand-300/50 focus:ring-4 focus:ring-brand-300/10"
                      disabled={isSending || isLoadingSkills}
                      onChange={(event) => void handleSkillSelection(event.target.value)}
                      value={selectedSkillId}
                    >
                      <option value="">不使用 Skill，普通面试</option>
                      {installedSkills.map((skill) => (
                        <option
                          className="bg-slate-950 text-white"
                          key={skill.skill_id}
                          value={skill.skill_id}
                        >
                          {skill.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <div className="flex items-center justify-between gap-3 sm:justify-end">
                    <span className="text-xs text-slate-500">
                      {selectedKnowledgeBaseId
                        ? "回答会基于资料库并附引用"
                        : "可在 /rag 上传资料后选择"}
                    </span>
                    <button
                      className="rounded-full border border-white/10 px-3 py-1.5 text-xs font-semibold text-slate-300 transition hover:border-hire-300/30 hover:bg-hire-300/10 hover:text-hire-100"
                      disabled={isLoadingKnowledgeBases}
                      onClick={() => void loadKnowledgeBases()}
                      type="button"
                    >
                      {isLoadingKnowledgeBases ? "刷新中" : "刷新"}
                    </button>
                  </div>
                </div>
                <div
                  className={`rounded-lg border p-2 transition ${
                    superPromptMode
                      ? "border-accent-300/50 bg-accent-300/10 shadow-neon focus-within:ring-4 focus-within:ring-accent-300/10"
                      : "border-white/10 bg-white/[0.055] focus-within:border-brand-300/50 focus-within:ring-4 focus-within:ring-brand-300/10"
                  }`}
                >
                  {uploadedImages.length > 0 ? (
                    <div className="flex flex-wrap gap-2 border-b border-white/10 px-2 pb-3">
                      {uploadedImages.map((image) => (
                        <div
                          className="relative overflow-hidden rounded-lg border border-white/10 bg-white/[0.06]"
                          key={image.id}
                        >
                          <img
                            alt={image.name}
                            className="h-20 w-20 object-cover"
                            src={image.url}
                          />
                          <button
                            aria-label="删除图片"
                            className="absolute right-1 top-1 flex h-6 w-6 items-center justify-center rounded-full bg-ink-950/90 text-xs text-white transition hover:bg-rose-500"
                            onClick={() => removeUploadedImage(image.id)}
                            type="button"
                          >
                            ×
                          </button>
                        </div>
                      ))}
                    </div>
                  ) : null}

                  <textarea
                    className="max-h-44 min-h-24 w-full resize-none bg-transparent px-3 py-2 text-sm leading-6 text-white outline-none placeholder:text-slate-500"
                    disabled={isSending}
                    onChange={(event) => setInput(event.target.value)}
                    onKeyDown={handleKeyDown}
                    onPaste={handlePaste}
                    placeholder={recruitmentTheme.chatPlaceholder}
                    value={input}
                  />

                  <div className="flex flex-col gap-3 px-2 pb-1 sm:flex-row sm:items-center sm:justify-between">
                    <div className="flex items-center gap-2">
                      <input
                        accept="image/png,image/jpeg,image/jpg,image/gif,image/webp"
                        className="hidden"
                        multiple
                        onChange={(event) => {
                          void addImageFiles(Array.from(event.target.files ?? []));
                          event.target.value = "";
                        }}
                        ref={fileInputRef}
                        type="file"
                      />
                      <button
                        className="flex h-9 w-9 items-center justify-center rounded-full border border-white/10 bg-white/[0.06] text-slate-200 transition hover:border-brand-300/40 hover:bg-brand-300/10 hover:text-brand-100 disabled:cursor-not-allowed disabled:opacity-50"
                        disabled={isSending || isUploadingImage}
                        onClick={() => fileInputRef.current?.click()}
                        title="上传图片"
                        type="button"
                      >
                        附
                      </button>
                      <p className="text-xs text-slate-400">
                        {isUploadingImage
                          ? "正在压缩图片..."
                          : supportsImageInput
                            ? "支持上传、粘贴、拖拽图片"
                            : "当前候选人不接视觉岗面试"}
                      </p>
                    </div>
                    <button
                      className="rounded-full bg-brand-300 px-5 py-2 text-sm font-semibold text-ink-950 shadow-neon transition hover:bg-brand-200 active:scale-[0.98] disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500 disabled:shadow-none"
                      disabled={!canSend}
                      onClick={() => void sendMessage()}
                      type="button"
                    >
                      {isSending ? "发送中" : "发送"}
                    </button>
                  </div>
                </div>
              </div>
            </section>

            <PromptSidebar
              isOpen={promptSidebarOpen}
              onFillPrompt={(content) => {
                setInput(content);
                setError("");
              }}
              onSendPrompt={(content) => void sendMessage(content)}
              onSuperPromptModeChange={setSuperPromptMode}
              onToggleOpen={() => setPromptSidebarOpen((current) => !current)}
              superPromptMode={superPromptMode}
            />
          </div>
        </div>
      </div>

      {lightboxImage ? (
        <div
          className="fixed inset-0 z-[70] flex cursor-zoom-out items-center justify-center bg-slate-950/90 p-4"
          onClick={() => setLightboxImage(null)}
        >
          <div
            className="flex max-h-full max-w-full cursor-default flex-col items-center"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="mb-3 flex w-full max-w-[92vw] flex-col gap-2 rounded-lg border border-white/10 bg-ink-950/90 px-3 py-2 text-sm text-slate-100 shadow-prism sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <p className="truncate font-semibold">{lightboxImage.name}</p>
                <p className="text-xs text-slate-400">
                  {lightboxImage.kind === "upload"
                    ? "上传图片"
                    : lightboxImage.kind.toUpperCase()}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  className="rounded-md border border-white/15 bg-white/5 px-3 py-1.5 text-xs font-semibold text-slate-100 transition hover:border-brand-300/40 hover:text-brand-100 focus:outline-none focus:ring-4 focus:ring-brand-300/10"
                  onClick={() =>
                    void downloadImage(
                      lightboxImage.src,
                      lightboxFilename(lightboxImage),
                    )
                  }
                  type="button"
                >
                  保存原图
                </button>
                {lightboxImage.src.startsWith("data:image/svg+xml") ? (
                  <button
                    className="rounded-md border border-white/15 bg-white/5 px-3 py-1.5 text-xs font-semibold text-slate-100 transition hover:border-brand-300/40 hover:text-brand-100 focus:outline-none focus:ring-4 focus:ring-brand-300/10"
                    onClick={() =>
                      void (async () => {
                        try {
                          const pngSource = await svgDataUrlToPng(
                            lightboxImage.src,
                            2,
                          );
                          await downloadImage(
                            pngSource,
                            `${lightboxImage.name.replace(/[^A-Za-z0-9_-]+/g, "_") || "image"}.png`,
                          );
                        } catch {
                          window.alert(
                            "SVG 转 PNG 失败，可能包含外部资源。请先保存 SVG 原图后再处理。",
                          );
                        }
                      })()
                    }
                    type="button"
                  >
                    保存为 PNG
                  </button>
                ) : null}
                <button
                  className="rounded-md border border-white/15 bg-white/5 px-3 py-1.5 text-xs font-semibold text-slate-100 transition hover:border-brand-300/40 hover:text-brand-100 focus:outline-none focus:ring-4 focus:ring-brand-300/10"
                  onClick={() => setLightboxImage(null)}
                  type="button"
                >
                  关闭
                </button>
              </div>
            </div>
            <img
              alt={lightboxImage.name}
              className="max-h-[85vh] max-w-[92vw] rounded-lg object-contain shadow-2xl"
              src={lightboxImage.src}
            />
          </div>
        </div>
      ) : null}
    </main>
  );
}
