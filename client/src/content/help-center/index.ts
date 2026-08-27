import checkAvailabilityCostData from "./articles/check-availability-cost-data.md?raw";
import chooseModelAgentWorkflow from "./articles/choose-model-agent-workflow.md?raw";
import modulesAndTerms from "./articles/modules-and-terms.md?raw";
import recoverUnavailableFeature from "./articles/recover-unavailable-feature.md?raw";
import reviewRemoteMcpAuth from "./articles/review-remote-mcp-auth.md?raw";
import startWithAModel from "./articles/start-with-a-model.md?raw";
import submitKnowledgeProposal from "./articles/submit-knowledge-proposal.md?raw";

export type HelpCategory = "第一次使用" | "按目标找指南" | "按模块浏览" | "解决问题" | "安全、费用与数据";
export type HelpContentType = "tutorial" | "how-to" | "explanation" | "reference";
export type HelpSectionId = "getting-started" | "goals" | "modules" | "troubleshooting" | "safety";

export interface HelpArticle {
  slug: string;
  title: string;
  summary: string;
  category: HelpCategory;
  contentType: HelpContentType;
  audience: string;
  estimatedMinutes: number;
  keywords: string[];
  relatedRoutes: string[];
  verifiedCommit: string;
  verifiedDate: string;
  content: string;
  nextSlug?: string;
}

export interface HelpIndexItem {
  id: string;
  title: string;
  summary: string;
  to: string;
  keywords: string[];
  badge?: string;
}

export interface HelpSection {
  id: HelpSectionId;
  title: HelpCategory;
  summary: string;
  path: string;
  items: HelpIndexItem[];
}

export interface HelpModuleTopic {
  id: string;
  title: string;
  summary: string;
  outcome: string;
  points: string[];
  productRoute?: string;
  badge?: string;
  keywords: string[];
}

export interface HelpModule {
  id: string;
  title: string;
  summary: string;
  keywords: string[];
  productRoute?: string;
  topics: HelpModuleTopic[];
  homeTopicIds: string[];
}

export type HelpSearchEntry = {
  id: string;
  kind: "article" | "section" | "module" | "topic";
  title: string;
  summary: string;
  keywords: string[];
  to: string;
  category: HelpCategory;
};

export const verifiedBaseline = { commit: "cc49136c", date: "2026-08-26" };
export const remoteMcpReviewBaseline = { commit: "f9e3cfe2", date: "2026-08-26" };

export const helpContentTypeLabels: Record<HelpContentType, string> = {
  tutorial: "入门教程",
  "how-to": "操作指南",
  explanation: "概念说明",
  reference: "速查参考",
};

export const helpArticles: HelpArticle[] = [
  {
    slug: "start-with-a-model",
    title: "第一次使用：找到能看图片的模型",
    summary: "筛选支持图片输入和图片识别的模型，并找到聊天页中的图片选择入口。",
    category: "第一次使用",
    contentType: "tutorial",
    audience: "第一次使用模镜、希望让 AI 理解图片的用户",
    estimatedMinutes: 4,
    keywords: ["第一次使用", "图片", "图片识别", "模型", "聊天", "立即面试", "Kimi"],
    relatedRoutes: ["/models", "/chat/moonshotai%2Fkimi-k3"],
    verifiedCommit: verifiedBaseline.commit,
    verifiedDate: verifiedBaseline.date,
    content: startWithAModel,
    nextSlug: "check-availability-cost-data",
  },
  {
    slug: "choose-model-agent-workflow",
    title: "该用模型、Agent，还是 Workflow？",
    summary: "按一次性任务、重复角色和固定多步骤流程，快速判断从哪里开始。",
    category: "按目标找指南",
    contentType: "explanation",
    audience: "知道要完成什么，但不确定该进入哪个模块的用户",
    estimatedMinutes: 3,
    keywords: ["模型", "Agent", "智能体", "Workflow", "工作流", "选择"],
    relatedRoutes: ["/models", "/agents", "/workflow"],
    verifiedCommit: verifiedBaseline.commit,
    verifiedDate: verifiedBaseline.date,
    content: chooseModelAgentWorkflow,
    nextSlug: "modules-and-terms",
  },
  {
    slug: "submit-knowledge-proposal",
    title: "从工作流提交待审知识",
    summary: "把确定性文本送入 Knowledge Inbox，并保持人工审批与活动版本隔离。",
    category: "按目标找指南",
    contentType: "how-to",
    audience: "需要让公告、规范或整理结果进入知识审核流程的工作流用户",
    estimatedMinutes: 5,
    keywords: ["工作流", "知识写入提议", "Knowledge Inbox", "知识库", "审批", "去重"],
    relatedRoutes: ["/workflow/classic", "/rag"],
    verifiedCommit: verifiedBaseline.commit,
    verifiedDate: verifiedBaseline.date,
    content: submitKnowledgeProposal,
    nextSlug: "check-availability-cost-data",
  },
  {
    slug: "modules-and-terms",
    title: "先认识模镜：资源、工作台与运行状态",
    summary: "了解模镜怎样组织 AI 能力、任务入口和运行状态，再决定从哪里开始。",
    category: "按模块浏览",
    contentType: "reference",
    audience: "第一次浏览模镜，或不确定各入口如何配合的用户",
    estimatedMinutes: 5,
    keywords: ["模镜", "整体结构", "模块", "术语", "入口", "状态", "资源", "工作台", "运维"],
    relatedRoutes: ["/models", "/agents", "/mcps", "/skills", "/runtime", "/prompts"],
    verifiedCommit: verifiedBaseline.commit,
    verifiedDate: verifiedBaseline.date,
    content: modulesAndTerms,
    nextSlug: "recover-unavailable-feature",
  },
  {
    slug: "recover-unavailable-feature",
    title: "功能暂不可用时怎么办",
    summary: "看懂“交互待适配”“开关未开启”等状态，并找到安全可用的替代入口。",
    category: "解决问题",
    contentType: "how-to",
    audience: "遇到按钮不可用、能力未开放或需要配置提示的用户",
    estimatedMinutes: 4,
    keywords: ["不可用", "未开放", "需要配置", "待适配", "开关未开启", "恢复", "RAG", "Skill", "重排"],
    relatedRoutes: ["/models", "/rag", "/skills/rerank", "/settings"],
    verifiedCommit: verifiedBaseline.commit,
    verifiedDate: verifiedBaseline.date,
    content: recoverUnavailableFeature,
    nextSlug: "review-remote-mcp-auth",
  },
  {
    slug: "review-remote-mcp-auth",
    title: "连接并复核需要认证的远程 MCP",
    summary: "核对固定 Origin，保存静态 Token 或完成 OAuth，并通过 Review Factory 发布最小只读契约。",
    category: "安全、费用与数据",
    contentType: "how-to",
    audience: "在本地单主体部署中配置和审核远程 MCP 的运维者",
    estimatedMinutes: 8,
    keywords: ["MCP", "远程 MCP", "Token", "OAuth", "Review Factory", "契约", "Origin", "Scope"],
    relatedRoutes: ["/mcps", "/mcps?view=hub"],
    verifiedCommit: remoteMcpReviewBaseline.commit,
    verifiedDate: remoteMcpReviewBaseline.date,
    content: reviewRemoteMcpAuth,
    nextSlug: "check-availability-cost-data",
  },
  {
    slug: "check-availability-cost-data",
    title: "操作前检查可用性、费用与数据影响",
    summary: "发送、上传或启用工具前，检查是否可用、哪里可能收费，以及资料能否发送。",
    category: "安全、费用与数据",
    contentType: "explanation",
    audience: "希望避免误操作、意外费用或不必要数据发送的用户",
    estimatedMinutes: 4,
    keywords: ["费用", "价格", "数据", "文件", "上传", "发送", "安全", "权限"],
    relatedRoutes: ["/models", "/settings"],
    verifiedCommit: verifiedBaseline.commit,
    verifiedDate: verifiedBaseline.date,
    content: checkAvailabilityCostData,
    nextSlug: "start-with-a-model",
  },
];

export const helpModules: HelpModule[] = [
  {
    id: "models",
    title: "模型",
    summary: "查找适合当前任务的模型，确认输入、状态和价格后进入对应工作区。",
    keywords: ["模型", "模型市场", "筛选", "比较", "聊天", "图片", "视频", "语音"],
    productRoute: "/models",
    homeTopicIds: ["filter-and-compare", "image-understanding"],
    topics: [
      { id: "filter-and-compare", title: "查找、筛选与比较", summary: "按输入形式和任务能力缩小范围，再比较状态与价格。", outcome: "从模型市场找出当前任务真正可用的候选模型。", points: ["输入形式和任务能力是两组不同条件", "模型卡片会显示可完成任务、可接收输入和价格", "比较结果仍需结合状态与本次任务判断"], productRoute: "/models", keywords: ["搜索", "筛选", "比较", "状态", "价格"] },
      { id: "smart-router", title: "智能路由", summary: "让系统按任务、预算和已验证能力选择模型。", outcome: "在不固定供应商时使用 ModelMirror Router 进入任务。", points: ["路由会参考本轮任务、预算和已验证能力", "路由结果不代表所有候选都已配置", "进入工作区后仍需在发送前检查费用和数据"], productRoute: "/models", keywords: ["智能路由", "Router", "预算", "自动选择"] },
      { id: "text-and-files", title: "文本与文件", summary: "查找支持文字提问或文件输入的模型。", outcome: "为普通问答、长文档阅读或文件分析找到合适入口。", points: ["模型市场可按“文本”或“文件”输入筛选", "文件可见不等于所有格式都支持", "添加文件前先确认资料是否允许发送"], productRoute: "/models", keywords: ["文本", "文件", "文档", "输入"] },
      { id: "image-understanding", title: "图片理解", summary: "同时确认“图片”输入和“图片识别”任务。", outcome: "找到能接收图片并分析内容的模型。", points: ["“图片”表示输入形式", "“图片识别”表示模型要完成的任务", "进入聊天后从加号菜单确认图片入口"], productRoute: "/models", keywords: ["图片", "图片识别", "看图", "视觉"] },
      { id: "image-generation", title: "图片生成与编辑", summary: "查找能够生成新图或编辑图片的模型。", outcome: "进入与图片生成任务匹配的工作区。", points: ["图片理解和图片生成是两类任务", "编辑图片时还要确认是否支持图片输入", "生成前检查计费和输出使用限制"], productRoute: "/models", keywords: ["图片生成", "图片编辑", "生成图片"] },
      { id: "video", title: "视频分析与生成", summary: "按视频输入或视频生成任务选择模型。", outcome: "区分分析已有视频和生成新视频两种入口。", points: ["视频输入用于分析已有内容", "视频生成使用独立任务能力", "时长、格式和费用以当前工作区提示为准"], productRoute: "/models", keywords: ["视频", "视频分析", "视频生成"] },
      { id: "realtime-voice", title: "实时语音", summary: "查找支持实时语音交互的模型。", outcome: "判断当前模型和环境是否具备实时语音入口。", points: ["模型市场提供“实时语音”任务筛选", "浏览器权限和服务连接会影响可用性", "开始前确认麦克风与费用提示"], productRoute: "/models", keywords: ["实时语音", "语音对话", "麦克风"] },
      { id: "transcription", title: "语音转写", summary: "把音频内容转换为文字。", outcome: "找到支持语音转写的模型和输入方式。", points: ["按“语音转写”任务筛选", "再确认模型是否接收音频输入", "上传音频前确认内容授权"], productRoute: "/models", keywords: ["语音转写", "音频转文字", "STT"] },
      { id: "speech-synthesis", title: "语音合成", summary: "把文字内容转换为语音。", outcome: "找到支持语音合成的模型和输出工作区。", points: ["语音合成与语音转写方向相反", "按“语音合成”任务筛选", "生成前检查声音、格式和费用提示"], productRoute: "/models", keywords: ["语音合成", "文字转语音", "TTS"] },
      { id: "music-generation", title: "音乐生成", summary: "查找能够生成音乐内容的模型。", outcome: "进入音乐生成任务对应的工作区。", points: ["按“音乐生成”任务筛选", "模型可用状态和交互适配需要分别确认", "生成前检查费用与输出使用限制"], productRoute: "/models", keywords: ["音乐生成", "音乐", "音频生成"] },
      { id: "start-chatting", title: "进入聊天与添加内容", summary: "核对模型名称，并从加号菜单查看可以添加的内容。", outcome: "进入正确模型的工作区，并在发送前找到图片或文件入口。", points: ["页面标题应显示所选模型", "加号菜单只显示当前工作区支持的内容类型", "选择文件不等于已经发送请求"], productRoute: "/models", keywords: ["聊天", "加号", "图片", "文件", "发送前"] },
    ],
  },
  {
    id: "agents",
    title: "Agent",
    summary: "寻找现成专家，或创建、评测和运行可重复使用的智能体。",
    keywords: ["Agent", "智能体", "角色", "工作流生成器", "自动化", "长期任务", "评测", "专家团", "Data X"],
    productRoute: "/agents",
    homeTopicIds: ["agent-studio", "workflow-generator"],
    topics: [
      { id: "agent-market", title: "寻找现成 Agent", summary: "按部门、专长和任务场景查找可用专家。", outcome: "从 Agent 人才市场找到适合当前任务的角色。", points: ["可按部门和关键词缩小范围", "先查看角色说明和能力标签", "进入对话前仍要选择并确认模型"], productRoute: "/agents", keywords: ["人才市场", "专家", "部门", "搜索"] },
      { id: "agent-studio", title: "Agent Studio", summary: "创建和管理自己的 Agent。", outcome: "进入正确的 Agent 管理入口。", points: ["在“我的智能体”中查看草稿、已发布和已归档项目", "创建时可配置模型、Toolset、知识和 Handoff", "发布前检查工具、知识和权限"], productRoute: "/agents/studio", keywords: ["Agent Studio", "我的智能体", "创建", "管理"] },
      { id: "workflow-generator", title: "AI 工作流生成器", summary: "用自然语言目标生成可检查的 Agent 或工作流草稿。", outcome: "把目标转换成可继续检查和编辑的候选草稿。", points: ["先说明目标、输入和期望结果", "生成结果是提案，不会自动发布", "批准后仍要在对应工作台继续检查"], productRoute: "/agents/meta-agent", badge: "Beta", keywords: ["AI工作流生成器", "元智能体", "草稿"] },
      { id: "automations", title: "自动化任务", summary: "按单次、间隔或 Cron 运行已发布 Agent。", outcome: "为固定版本 Agent 安排可追踪的定时任务。", points: ["先准备已发布的 Agent 版本", "可设置预算、重试和死信处理", "自动运行可能产生模型或工具费用"], productRoute: "/agents/automations", badge: "Beta", keywords: ["自动化", "定时", "Cron", "重试"] },
      { id: "goals", title: "长期 Goal", summary: "审核可暂停、可恢复的长期任务计划。", outcome: "查看依赖计划，并在失败时恢复或改派步骤。", points: ["计划需要用户审核后再执行", "可查看依赖、执行结果和恢复操作", "长期任务可能调用多个 Agent 和工具"], productRoute: "/agents/goals", badge: "Beta", keywords: ["长期任务", "Goal", "计划", "恢复"] },
      { id: "evaluations", title: "Agent 评测", summary: "用固定数据集比较候选、草稿或已发布版本。", outcome: "查看质量、成本和错误报告，而不改变当前 Agent。", points: ["评测使用固定目标和执行快照", "运行报告不会自动修改草稿或发布状态", "真实模型评测可能产生费用"], productRoute: "/agents/evaluations", keywords: ["评测", "基准", "数据集", "运行报告"] },
      { id: "evolution", title: "Agent 受控优化", summary: "在固定边界内优化 Prompt 或工作流结构。", outcome: "生成待审批的优化提案，而不是直接改动 Agent。", points: ["先固定草稿、能力快照和数据集", "质量、成本和安全门禁通过后只生成 Proposal", "采用前仍需人工审批"], productRoute: "/agents/evolution", keywords: ["受控优化", "进化", "Prompt", "工作流结构", "Proposal"] },
      { id: "datax", title: "Data X 数据分析", summary: "导入数据快照，建立语义模型和版本化指标。", outcome: "在本地项目中完成受限分析，并把已审核指标提供给 Agent。", points: ["支持 CSV、XLSX 和 Parquet", "项目之间的数据和语义模型相互隔离", "指标提案需要审核后发布"], productRoute: "/datax", keywords: ["Data X", "数据分析", "指标", "CSV", "Excel"] },
      { id: "expert-team", title: "专家团", summary: "使用模型融合、自动路由或 AI Team 处理多角色任务。", outcome: "根据任务选择合适的多角色协作方式。", points: ["Fusion 可并行调用 2–5 个模型并综合结果", "自动路由根据任务选择合适专家", "AI Team 支持接力或辩论，关键结果仍需人工确认"], productRoute: "/expert-team", keywords: ["专家团", "Fusion", "自动路由", "AI Team", "多角色"] },
    ],
  },
  {
    id: "mcps",
    title: "MCP",
    summary: "发现并连接外部工具，查看已注册能力，或发布供 Agent 使用的 Toolset。",
    keywords: ["MCP", "工具", "连接", "服务", "Hub", "Toolset"],
    productRoute: "/mcps",
    homeTopicIds: ["tool-shelf", "connected-registry"],
    topics: [
      { id: "tool-shelf", title: "工具货架", summary: "按用途、分类和适配状态查找目录中的 MCP 工具。", outcome: "找到与任务有关的工具，并看懂它当前是否已适配。", points: ["工具货架是目录，不代表工具已经连接", "先按任务筛选，再查看适配状态", "需要凭据的工具由有权限的人配置"], productRoute: "/mcps", keywords: ["工具货架", "浏览", "目录", "筛选"] },
      { id: "connected-registry", title: "已连接注册表", summary: "查看当前连接后已经注册的工具。", outcome: "确认 MCP 服务是否已经向平台提供可见工具。", points: ["这里显示连接后注册的工具，不是完整目录", "没有记录时先回到工具货架连接服务", "运行状态也可在 Runtime 中查看"], productRoute: "/mcps?view=registry", keywords: ["已连接注册表", "已注册工具", "连接"] },
      { id: "mcp-hub", title: "MCP Hub", summary: "从官方 Registry 受控发现远程 MCP 服务。", outcome: "了解远程候选、复核和连接边界。", points: ["Registry 收录不等于安全认证", "功能默认关闭时，同步和远程试连不可用", "OAuth 工具只有在契约、当前 Token 版本和 Schema 匹配时才能进入 Runtime，且每次调用都要审批"], productRoute: "/mcps?view=hub", badge: "受限", keywords: ["MCP Hub", "Registry", "远程", "OAuth"] },
      { id: "toolsets", title: "Toolset Runtime", summary: "连接 MCP，或导入 OpenAPI、OData 并发布固定版本。", outcome: "把经过配置和测试的工具集发布给 Agent 绑定。", points: ["Toolset 与 MCP 目录承担不同职责", "凭据只通过加密引用保存", "测试和发布前要检查工具权限与审批要求"], productRoute: "/toolsets", badge: "管理", keywords: ["Toolset", "OpenAPI", "OData", "发布", "凭据"] },
    ],
  },
  {
    id: "skills",
    title: "Skill",
    summary: "查找、安装、创建和管理可重复使用的 Skill 与 SkillSet。",
    keywords: ["Skill", "技能", "SkillSet", "Creator", "导入", "草稿", "提案", "重排"],
    productRoute: "/skills",
    homeTopicIds: ["creator", "local-import"],
    topics: [
      { id: "market", title: "Skill 市场", summary: "按分类、安装状态和资源类型查找 Skill。", outcome: "找到可复用的做法，并先查看来源与信任状态。", points: ["Skill 保存可重复使用的做法和约束", "SkillSet 是一组相关 Skill", "信任策略阻断时不要绕过安装"], productRoute: "/skills", keywords: ["市场", "浏览", "SkillSet", "信任策略"] },
      { id: "installed", title: "已安装 Skill", summary: "查看当前版本、历史版本和恢复点。", outcome: "确认哪些 Skill 已安装，并管理之后运行使用的版本。", points: ["替换会保留不可变版本", "卸载会保留恢复点", "切换版本只影响之后启动的运行"], productRoute: "/skills?tab=installed", keywords: ["已安装", "版本", "恢复", "卸载"] },
      { id: "creator", title: "Skill Creator", summary: "用一句需求生成提案，或从空白 Skill 开始。", outcome: "进入创建入口，并知道生成不等于自动安装。", points: ["没有配置模型时仍可创建和编辑空白 Skill", "AI 先生成提案，需要用真实任务检查", "只有确认后才安装"], productRoute: "/skills/create", keywords: ["Creator", "创建", "提案", "安装"] },
      { id: "local-import", title: "本地导入", summary: "选择 ZIP 或文件夹，在安装前完成本地扫描。", outcome: "先检查来源、脚本和敏感信息，再决定是否安装。", points: ["导入过程不会执行脚本", "页面会检查路径、密钥和脚本风险", "只导入你有权使用并信任的内容"], productRoute: "/skills/import", keywords: ["本地", "导入", "ZIP", "文件夹", "扫描"] },
      { id: "drafts", title: "工作区草稿", summary: "查看已批准但尚未安装的 Skill 草稿。", outcome: "把提案批准和实际安装作为两个独立决定处理。", points: ["批准提案只生成草稿", "草稿可以继续检查", "安装始终需要显式操作"], productRoute: "/skills?tab=drafts", keywords: ["工作区草稿", "草稿", "安装"] },
      { id: "proposals", title: "待审提案", summary: "审核 Agent 提交的版本化 Skill 提案。", outcome: "决定是否把提案转为草稿，而不自动安装。", points: ["提案需要人工审核", "批准后不会自动安装 Skill", "同时不会自动发布 Agent 或 Prompt"], productRoute: "/skills?tab=proposals", keywords: ["待审提案", "自编写提案", "审核"] },
      { id: "rerank", title: "语义重排治理", summary: "检查固定金标、显式反馈和 Router 影子统计。", outcome: "在管理端评测或回退语义排序，不改变 Skill 权限。", points: ["治理 Store 不可用时相关操作会停止", "晋级不会授予安装或运行权限", "评测、晋级和回退都需要单独确认"], productRoute: "/skills/rerank", badge: "管理", keywords: ["语义重排", "重排治理", "Router", "反馈", "评测"] },
    ],
  },
  {
    id: "prompts",
    title: "提示词",
    summary: "使用现成模板，管理可发布命令，或组合声明式 Plugin 资源包。",
    keywords: ["提示词", "Prompt", "模板", "命令", "Plugin"],
    productRoute: "/prompts",
    homeTopicIds: ["templates", "prompt-command"],
    topics: [
      { id: "templates", title: "模板库", summary: "按分类找到接近当前任务的提示词模板。", outcome: "把模板填入对话草稿，再按当前任务修改。", points: ["模板只填入对话草稿，不会自动发送", "先确认模板需要哪些输入", "使用前选择目标模型"], productRoute: "/prompts", keywords: ["模板库", "分类", "对话草稿"] },
      { id: "prompt-command", title: "Prompt Command", summary: "管理需要版本、校验、发布和评测的提示词命令。", outcome: "区分一次使用的模板和需要长期维护的命令。", points: ["需要长期维护时再使用 Prompt Command", "发布前完成校验和评测", "不要写入未获授权的敏感资料"], productRoute: "/prompts?view=commands", keywords: ["Prompt Command", "版本", "发布", "评测"] },
      { id: "plugins", title: "Plugin 资源包", summary: "把 Prompt、Skill、固定 Toolset 和中间件预设组合在一起。", outcome: "导入和管理不加载服务端动态代码的声明式资源包。", points: ["Plugin 是本地声明式资源包", "导入后先作为草稿检查", "资源绑定和发布仍受各自权限与版本规则约束"], productRoute: "/plugins", badge: "管理", keywords: ["Plugin", "资源包", "Prompt", "Skill", "Toolset"] },
    ],
  },
  {
    id: "runtime",
    title: "运维",
    summary: "集中查看运行记录、客户端宿主和运行资源；管理操作回到对应模块完成。",
    keywords: ["运维", "Runtime", "运行记录", "客户端宿主", "运行资源", "诊断"],
    productRoute: "/runtime",
    homeTopicIds: ["run-records", "runtime-resources"],
    topics: [
      { id: "run-records", title: "运行记录", summary: "按类型和状态查找运行中或失败的任务。", outcome: "找到需要进一步检查的工作流、聊天或 Agent 运行。", points: ["可按工作流、Agent、聊天和长期目标筛选", "可按等待中、运行中、已完成、失败和已取消筛选", "Runtime 只做诊断，管理操作回到对应模块完成"], productRoute: "/runtime", keywords: ["运行记录", "失败", "筛选", "工作流", "聊天", "Agent"] },
      { id: "client-hosts", title: "客户端宿主", summary: "查看 Chrome 或 Office 是否已配对。", outcome: "在需要客户端工具前确认宿主连接状态。", points: ["配对码是一次性的", "Token 只保存在客户端", "宿主未配对时先在 Runtime 发起配对"], productRoute: "/runtime", keywords: ["客户端宿主", "Chrome", "Office", "配对"] },
      { id: "runtime-resources", title: "运行资源", summary: "切换查看 MCP 连接、工具、Skill 和环境依赖。", outcome: "在一个面板中找到未连接、异常或未就绪的资源。", points: ["MCP 连接可按活跃、异常、已关闭和未知筛选", "工具和 Skill 显示当前运行侧可见状态", "环境依赖只显示是否就绪，不展示密钥"], productRoute: "/runtime", keywords: ["运行资源", "MCP", "工具", "Skill", "环境依赖"] },
    ],
  },
  {
    id: "workspace",
    title: "工作台与设置",
    summary: "编排流程、管理资料和本地数据，处理代码任务或系统连接。",
    keywords: ["工作台", "工作流", "RAG", "数据表", "Coding", "设置"],
    homeTopicIds: ["rag", "coding"],
    topics: [
      { id: "workflow", title: "经典工作流", summary: "在稳定画布中编排并试运行多步骤任务。", outcome: "把固定顺序、条件分支和资源调用组织成可重复流程。", points: ["经典画布是当前稳定工作流入口", "草稿可本地保存并通过后端试运行", "需要把确定性文本交给知识管理员时，可使用“知识写入提议”进入 Knowledge Inbox"], productRoute: "/workflow", keywords: ["工作流", "经典画布", "流程", "分支", "知识写入提议", "Knowledge Inbox"] },
      { id: "rag", title: "RAG 知识库", summary: "创建资料库、上传文档，并让回答引用指定资料。", outcome: "知道什么时候使用自己的资料库，而不是普通聊天。", points: ["RAG 可以理解为先从指定资料找内容，再让模型回答", "当前空白页从“新建知识库”开始", "上传前确认资料权限和数据边界"], productRoute: "/rag", keywords: ["RAG", "知识库", "资料", "引用"] },
      { id: "data-tables", title: "本地数据表", summary: "为私有工作流维护有固定字段的业务记录。", outcome: "创建数据表、发布 Schema，并管理本地记录。", points: ["数据表用于类型化业务记录", "Schema 以不可变版本发布", "字段和记录操作属于数据表内部条目，本轮不展开"], productRoute: "/data-tables", keywords: ["数据表", "Schema", "业务记录", "数据库"] },
      { id: "coding", title: "Coding", summary: "在只读实验工作台中查看项目并询问代码问题。", outcome: "理解当前入口的只读边界和启用状态。", points: ["当前页面说明只能读取项目并回答问题", "不能修改文件或运行命令", "页面显示“代码助手暂时不可用”时，应等待管理员启用"], productRoute: "/coding", keywords: ["Coding", "代码", "只读实验", "暂时不可用"] },
      { id: "settings", title: "系统设置", summary: "由授权人员管理 Provider、路由实验和其他服务连接。", outcome: "知道哪些设置需要交给有配置权限的人处理。", points: ["未配置时页面会明确提示", "Provider 管理和其他集成分区显示", "设置变更可能影响其他用户或产生外部费用"], productRoute: "/settings", badge: "管理员", keywords: ["设置", "Provider", "连接", "权限", "路由实验"] },
    ],
  },
  {
    id: "experimental",
    title: "实验功能",
    summary: "单独查看仍在试验阶段、入口或行为可能变化的工作台。",
    keywords: ["实验", "Workflow Native", "Science", "矩阵绿洲", "Beta"],
    homeTopicIds: ["science", "matrix-oasis"],
    topics: [
      { id: "workflow-native", title: "Workflow Native", summary: "在隔离校验台检查工作流图结构。", outcome: "试验 Native 静态校验，而不影响经典工作流主入口。", points: ["页面只验证图结构", "不会执行模型、Tool 或 RAG", "稳定工作流继续使用经典画布"], productRoute: "/workflow-native", badge: "实验", keywords: ["Workflow Native", "工作流", "静态校验", "实验"] },
      { id: "science", title: "Science", summary: "当前没有独立可用的 Science 页面。", outcome: "避免把不存在的入口当成已开放功能。", points: ["本次验证访问现有地址会回到模型市场", "实验入口和范围可能变化", "以产品界面中的可见入口为准"], badge: "实验", keywords: ["Science", "科学", "实验", "模型市场"] },
      { id: "matrix-oasis", title: "矩阵绿洲", summary: "当前是空间与世界体验的预告页。", outcome: "了解它仍处于实验展示阶段。", points: ["页面显示“世界仍在生成”", "当前入口主要用于查看预告", "实验结果和入口可能变化"], productRoute: "/matrix-oasis", badge: "实验", keywords: ["矩阵绿洲", "空间", "世界仍在生成", "实验"] },
    ],
  },
];

export const helpSections: HelpSection[] = [
  {
    id: "getting-started",
    title: "第一次使用",
    summary: "先认识入口，再完成一次不发送、不上传的安全练习。",
    path: "/help/sections/getting-started",
    items: [
      { id: "find-image-model", title: "找到能看图片的模型", summary: "筛选模型，并找到聊天页中的图片选择入口。", to: "/help/start-with-a-model", keywords: ["图片", "模型", "第一次", "加号"] },
      { id: "choose-entry", title: "选对模型、Agent 或 Workflow", summary: "按任务是否重复和是否多步骤选择入口。", to: "/help/choose-model-agent-workflow", keywords: ["选择", "Agent", "Workflow"] },
      { id: "check-before-send", title: "发送前检查费用与数据", summary: "先确认可用性、费用提示和数据边界。", to: "/help/check-availability-cost-data", keywords: ["费用", "数据", "发送前"] },
    ],
  },
  {
    id: "goals",
    title: "按目标找指南",
    summary: "按要完成的事选择入口，不必先理解所有模块。",
    path: "/help/sections/goals",
    items: [
      { id: "one-time", title: "只完成眼前这一次", summary: "问答、写作、看图或临时分析，先直接用模型。", to: "/help/modules/models", keywords: ["一次", "问答", "写作", "看图"] },
      { id: "repeat-role", title: "以后反复使用同一角色", summary: "需要保留角色设定和工具时，查看 Agent。", to: "/help/modules/agents", keywords: ["重复", "角色", "Agent"] },
      { id: "repeat-process", title: "按固定顺序完成多步任务", summary: "需要同一流程重复运行时，查看 Workflow 判断。", to: "/help/choose-model-agent-workflow", keywords: ["多步", "固定顺序", "Workflow"] },
      { id: "connect-tool", title: "让 AI 使用外部工具", summary: "需要访问外部服务时，先查看 MCP 目录与连接状态。", to: "/help/modules/mcps", keywords: ["外部工具", "MCP", "连接"] },
      { id: "use-own-docs", title: "根据自己的资料回答", summary: "需要从指定文档查找内容时，查看 RAG 知识库。", to: "/help/modules/workspace/rag", keywords: ["自己的资料", "文档", "RAG", "知识库"] },
      { id: "propose-knowledge", title: "把工作流结果提交给知识管理员", summary: "将确定性文本送入 Knowledge Inbox，审批后再决定是否激活。", to: "/help/submit-knowledge-proposal", keywords: ["工作流", "知识", "Inbox", "审批", "提议"] },
      { id: "check-runtime", title: "查找运行或连接问题", summary: "任务失败或工具未连接时，到 Runtime 运维查看状态。", to: "/help/modules/runtime", keywords: ["运行失败", "连接", "Runtime", "运维"] },
    ],
  },
  {
    id: "modules",
    title: "按模块浏览",
    summary: "先看模块负责什么，再进入具体功能。",
    path: "/help/sections/modules",
    items: helpModules.map((module) => ({ id: module.id, title: module.title, summary: module.summary, to: `/help/modules/${module.id}`, keywords: module.keywords })),
  },
  {
    id: "troubleshooting",
    title: "解决问题",
    summary: "根据页面提示判断问题，并找到可继续的入口。",
    path: "/help/sections/troubleshooting",
    items: [
      { id: "unavailable", title: "功能不可用或尚未开放", summary: "看懂待适配、开关未开启、需要配置和当前入口未开放。", to: "/help/recover-unavailable-feature", keywords: ["不可用", "未开放", "待适配", "开关"] },
      { id: "no-response", title: "页面、按钮或运行没有反应", summary: "先检查加载、按钮状态和可见错误，再决定是否重试。", to: "/help/sections/troubleshooting#no-response", keywords: ["没反应", "按钮", "加载"] },
      { id: "configuration", title: "配置、权限与连接问题", summary: "判断是否需要有管理权限的人处理。", to: "/help/sections/troubleshooting#configuration", keywords: ["配置", "权限", "连接"] },
    ],
  },
  {
    id: "safety",
    title: "安全、费用与数据",
    summary: "发送、上传或配置前，确认权限、费用和资料范围。",
    path: "/help/sections/safety",
    items: [
      { id: "availability", title: "使用前确认是否可用", summary: "区分目录存在、入口可进入和真实调用已验证。", to: "/help/check-availability-cost-data#可用性怎么看", keywords: ["可用", "状态", "入口"] },
      { id: "cost", title: "查看价格与收费环节", summary: "看懂输入、输出、动态和按媒体计费。", to: "/help/check-availability-cost-data#费用怎么看", keywords: ["价格", "收费", "费用"] },
      { id: "data", title: "了解文件与数据处理", summary: "只发送完成任务所需、并且允许外发的内容。", to: "/help/check-availability-cost-data#文件与数据怎么看", keywords: ["文件", "数据", "上传", "隐私"] },
    ],
  },
];

export const helpCategories = helpSections.map((section) => section.title);

export function findHelpArticle(slug: string | undefined) { return helpArticles.find((article) => article.slug === slug); }
export function findHelpSection(id: string | undefined) { return helpSections.find((section) => section.id === id); }
export function findHelpModule(id: string | undefined) { return helpModules.find((module) => module.id === id); }
export function findHelpModuleTopic(moduleId: string | undefined, topicId: string | undefined) { return findHelpModule(moduleId)?.topics.find((topic) => topic.id === topicId); }

export function getHelpSearchEntries(): HelpSearchEntry[] {
  const articleEntries: HelpSearchEntry[] = helpArticles.map((article) => ({ id: article.slug, kind: "article", title: article.title, summary: article.summary, keywords: article.keywords, to: `/help/${article.slug}`, category: article.category }));
  const sectionEntries: HelpSearchEntry[] = helpSections.map((section) => ({ id: section.id, kind: "section", title: section.title, summary: section.summary, keywords: section.items.flatMap((item) => item.keywords), to: section.path, category: section.title }));
  const moduleEntries: HelpSearchEntry[] = helpModules.flatMap((module) => [
    { id: module.id, kind: "module" as const, title: module.title, summary: module.summary, keywords: module.keywords, to: `/help/modules/${module.id}`, category: "按模块浏览" as const },
    ...module.topics.map((topic) => ({ id: `${module.id}/${topic.id}`, kind: "topic" as const, title: `${module.title}：${topic.title}`, summary: topic.summary, keywords: topic.keywords, to: `/help/modules/${module.id}/${topic.id}`, category: "按模块浏览" as const })),
  ]);
  return [...articleEntries, ...sectionEntries, ...moduleEntries];
}

export function searchHelpContent(query: string) {
  const normalized = query.trim().toLocaleLowerCase();
  const entries = getHelpSearchEntries();
  if (!normalized) return entries;
  return entries.filter((entry) => [entry.title, entry.summary, ...entry.keywords].join(" ").toLocaleLowerCase().includes(normalized));
}
