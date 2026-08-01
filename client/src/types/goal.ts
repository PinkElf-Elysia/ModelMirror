import { type XpertConversationMessage } from "./xpert";

export type GoalStatus =
  | "planning"
  | "awaiting_review"
  | "running"
  | "paused"
  | "needs_attention"
  | "completed"
  | "cancelled";

export type GoalStepStatus =
  | "pending"
  | "running"
  | "waiting_approval"
  | "waiting_client"
  | "completed"
  | "failed"
  | "blocked"
  | "skipped"
  | "cancelled";

export interface GoalStep {
  step_id: string;
  title: string;
  instruction: string;
  target_xpert_id: string;
  target_version: number | null;
  depends_on: string[];
  status: GoalStepStatus;
  task_id: string | null;
  handoff_id: string | null;
  xpert_run_id: string | null;
  result: string | null;
  error: string | null;
  attempts: number;
  created_at: number;
  updated_at: number;
}

export interface ConversationGoal {
  goal_id: string;
  title: string;
  objective: string;
  planner_xpert_id: string;
  planner_version: number;
  source_xpert_id: string | null;
  source_conversation_id: string | null;
  file_asset_ids: string[];
  messages: XpertConversationMessage[];
  status: GoalStatus;
  plan_summary: string;
  plan_revision: number;
  final_step_id: string | null;
  max_parallel: number;
  steps: GoalStep[];
  result: string | null;
  error: string | null;
  run_id: string | null;
  created_at: number;
  updated_at: number;
}

export interface GoalSummary {
  goal_id: string;
  title: string;
  objective_preview: string;
  planner_xpert_id: string;
  planner_version: number;
  source_xpert_id: string | null;
  source_conversation_id: string | null;
  file_asset_ids: string[];
  status: GoalStatus;
  plan_summary: string;
  plan_revision: number;
  final_step_id: string | null;
  max_parallel: number;
  error: string | null;
  run_id: string | null;
  step_count: number;
  completed_step_count: number;
  created_at: number;
  updated_at: number;
}

export interface GoalListResponse {
  version: string;
  items: GoalSummary[];
  total: number;
}

export interface CreateGoalPayload {
  title: string;
  objective: string;
  planner_xpert_id: string;
  source_xpert_id?: string;
  source_conversation_id?: string;
  file_asset_ids?: string[];
  messages?: XpertConversationMessage[];
  max_parallel?: number;
}
