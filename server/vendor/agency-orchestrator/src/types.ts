/** 工作流 YAML 定义的类型 */

export interface WorkflowDefinition {
  name: string;
  description?: string;
  agents_dir: string;
  llm: LLMConfig;
  concurrency?: number;       // 最大并行步骤数，默认 2
  verify?: boolean;           // acceptance 自动核验+未过自动返工一轮（默认开）。false = 整个工作流关闭
  inputs?: InputDefinition[];
  steps: StepDefinition[];
}

export interface LLMConfig {
  provider: 'claude' | 'openai' | 'ollama' | 'deepseek' | 'claude-code' | 'gemini-cli' | 'copilot-cli' | 'codex-cli' | 'openclaw-cli' | 'hermes-cli' | (string & {});
  base_url?: string;          // 自定义 API 地址（DeepSeek、智谱等）
  api_key?: string;           // 可在 YAML 中配置，也可用环境变量
  model?: string;              // CLI providers 可省略（使用 CLI 默认模型）
  agent?: string;             // openclaw-cli 专用：agent ID（默认 "main"）
  max_tokens?: number;        // 默认 4096
  temperature?: number;       // 采样温度。未设置=用 provider 默认；设 0=近确定性（可复现、适合评测/抽取类任务）
  params?: Record<string, unknown>; // 供应商专有参数，原样并入请求体（如 DeepSeek/OpenAI 的 reasoning 档位、Anthropic thinking）。核心字段（model/messages/stream 等）不可被覆盖
  timeout?: number;           // 单步超时 ms。未设置时按 provider 默认（API 120000 / CLI·ollama 600000）并按输入规模动态抬高首次超时（每 1K 字符 +8s，最多 +600000）。因超时触发重试时，下一次 timeout 自动 x1.5（上限 3600000 / 60min）。设为 0 表示不限时；显式设置则不做动态调整
  retry?: number;             // 失败重试次数，默认 3
}

export interface InputDefinition {
  name: string;
  description?: string;
  required?: boolean;
  default?: string;            // 可选输入的默认值
}

export interface StepDefinition {
  id: string;
  role: string;               // agency-agents 路径，如 "engineering/engineering-sre"
  name?: string;              // 自定义显示名（覆盖角色文件的 name）
  emoji?: string;             // 自定义 emoji（覆盖角色文件的 emoji）
  task: string;               // 任务描述，支持 {{变量}} 模板
  acceptance?: string;        // 验收标准（支持 {{变量}}）：注入 prompt 末尾要求产出满足；产出后自动核验，未过自动返工一轮；随产出展示，并作盲评评分锚点
  verify?: boolean;           // false = 本步关闭 acceptance 自动核验（优先级高于顶层 verify）
  output?: string;            // 输出变量名
  skill?: string;             // 给本步挂一个方法论 skill（注入 system prompt），如 "test-driven-development"
  skills?: string[];          // 多个 skill（与 skill 合并）
  depends_on?: string[];      // 依赖的步骤 id
  type?: 'normal' | 'approval' | 'human_input'; // 节点类型
  prompt?: string;            // approval / human_input 类型的提示文本
  condition?: string;           // 如 "{{category}} contains bug"
  depends_on_mode?: 'all' | 'any_completed';  // 默认 'all'（任一跳过→跳过），'any_completed' = 只要有一个完成就执行
  llm?: Partial<LLMConfig>;   // 步骤级 LLM 配置，覆盖全局 llm
  loop?: {
    back_to: string;            // 跳回的步骤 id
    max_iterations: number;     // 最大循环次数，必填，上限 10
    exit_condition: string;     // 退出条件，同 condition 语法
  };
}

/** DAG 执行相关类型 */

export interface DAGNode {
  step: StepDefinition;
  dependencies: string[];     // 依赖的 node id
  dependents: string[];       // 被谁依赖
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped';
  result?: string;
  error?: string;
  startTime?: number;
  endTime?: number;
  tokenUsage?: { input: number; output: number };
  agentName?: string;         // 角色显示名（如"趋势研究员"）
  agentEmoji?: string;        // 角色 emoji
  acceptance?: string;        // 执行时渲染后的验收标准（executeStep 写入，进 StepResult/metadata）
  verification?: StepVerification; // acceptance 自动核验结果（executeStep 写入）
}

/**
 * acceptance 自动核验结果。验收不过是质量信号而非执行错误：步骤不会因此 failed，
 * 最坏情况是"带 ⚠️ 标记的返工版"照常流向下游。
 */
export interface StepVerification {
  pass: boolean;              // 最终产出是否通过核验（返工后复核不可用时保守记 false）
  failed: string[];           // 未满足条目（"条目（原因）"），pass=true 时为空
  reworked: boolean;          // 是否触发过自动返工
}

/** LLM Connector 相关类型 */

export interface LLMResult {
  content: string;
  usage: {
    input_tokens: number;
    output_tokens: number;
  };
}

export interface LLMConnector {
  chat(systemPrompt: string, userMessage: string, config: LLMConfig): Promise<LLMResult>;
}

/** Agent Loader 相关类型 */

export interface AgentDefinition {
  name: string;
  description: string;
  emoji?: string;
  tools?: string;
  rolePath?: string;          // 角色路径，如 "engineering/engineering-sre"
  systemPrompt: string;       // frontmatter 之后的完整 markdown 内容
}

/** 执行结果 */

export interface WorkflowResult {
  name: string;
  success: boolean;
  steps: StepResult[];
  totalDuration: number;
  totalTokens: { input: number; output: number };
  /** 原始用户输入（用于 --resume 时恢复） */
  inputs?: Record<string, string>;
  /** 源工作流文件绝对路径（随 metadata 存档，供历史记录重跑/续跑定位源文件） */
  file?: string;
}

export interface StepResult {
  id: string;
  role: string;
  agentName?: string;             // 角色显示名（如"趋势研究员"）
  agentEmoji?: string;            // 角色 emoji
  status: 'completed' | 'failed' | 'skipped';
  output?: string;
  output_var?: string;            // 输出变量名（用于 resume 时重建 context）
  acceptance?: string;            // 该步的验收标准（渲染后），随 metadata 存档供查看器展示
  error?: string;
  duration: number;
  tokens: { input: number; output: number };
  iterations?: number;          // 该步骤实际执行次数（循环场景 > 1）
  verification?: StepVerification; // acceptance 自动核验结果（进 metadata，查看器/summary 展示）
}
