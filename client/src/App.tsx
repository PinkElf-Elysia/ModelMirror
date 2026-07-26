import { Navigate, Route, Routes } from "react-router-dom";
import AgentsPage from "./pages/AgentsPage";
import ChatPage from "./pages/ChatPage";
import ExpertTeamPage from "./pages/ExpertTeamPage";
import McpBrowserPage from "./pages/McpBrowserPage";
import MetaAgentPage from "./pages/MetaAgentPage";
import MatrixOasisPage from "./pages/MatrixOasisPage";
import ModelListPage from "./pages/ModelListPage";
import RuntimeOpsPage from "./pages/RuntimeOpsPage";
import StudioHomePage from "./pages/StudioHomePage";
import RagPage from "./pages/RagPage";
import SkillBrowserPage from "./pages/SkillBrowserPage";
import SystemSettingsPage from "./pages/SystemSettingsPage";
import WorkflowClassicPage from "./pages/WorkflowClassicPage";
import WorkflowNativePage from "./pages/WorkflowNativePage";
import XpertChatPage from "./pages/XpertChatPage";
import XpertCreatePage from "./pages/XpertCreatePage";
import XpertStudioIndexPage from "./pages/XpertStudioIndexPage";
import XpertStudioPage from "./pages/XpertStudioPage";
import XpertAppPage from "./pages/XpertAppPage";
import ConversationGoalsPage from "./pages/ConversationGoalsPage";
import KnowledgePipelineCanvasPage from "./pages/KnowledgePipelineCanvasPage";
import KnowledgeEvaluationPage from "./pages/KnowledgeEvaluationPage";
import KnowledgeInboxPage from "./pages/KnowledgeInboxPage";
import AutomationsPage from "./pages/AutomationsPage";
import DataXHomePage from "./pages/DataXHomePage";
import DataXProjectPage from "./pages/DataXProjectPage";
import DataXInboxPage from "./pages/DataXInboxPage";
import ToolsetsPage from "./pages/ToolsetsPage";
import PromptProfilesPage from "./pages/PromptProfilesPage";
import PluginsPage from "./pages/PluginsPage";

export default function App() {
  return (
    <Routes>
      <Route element={<Navigate replace to="/models" />} path="/" />
      <Route element={<ModelListPage />} path="/models" />
      <Route element={<StudioHomePage />} path="/studio" />
      <Route element={<AgentsPage />} path="/agents" />
      <Route element={<MetaAgentPage />} path="/agents/meta-agent" />
      <Route element={<XpertStudioIndexPage />} path="/agents/studio" />
      <Route element={<XpertCreatePage />} path="/agents/studio/new" />
      <Route element={<XpertStudioPage />} path="/agents/studio/:xpertId" />
      <Route element={<XpertChatPage />} path="/agents/xpert/:xpertId/chat" />
      <Route element={<XpertAppPage />} path="/apps/:appSlug" />
      <Route element={<ConversationGoalsPage />} path="/agents/goals" />
      <Route element={<ConversationGoalsPage />} path="/agents/goals/:goalId" />
      <Route element={<AutomationsPage />} path="/agents/automations" />
      <Route element={<DataXHomePage />} path="/datax" />
      <Route element={<DataXInboxPage />} path="/datax/:projectId/inbox" />
      <Route element={<DataXProjectPage />} path="/datax/:projectId" />
      <Route element={<MatrixOasisPage />} path="/matrix-oasis" />
      <Route element={<ExpertTeamPage />} path="/expert-team" />
      <Route element={<McpBrowserPage />} path="/mcps" />
      <Route element={<ToolsetsPage />} path="/toolsets" />
      <Route element={<SkillBrowserPage />} path="/skills" />
      <Route element={<RuntimeOpsPage />} path="/runtime" />
      <Route element={<PromptProfilesPage />} path="/prompts" />
      <Route element={<PluginsPage />} path="/plugins" />
      <Route element={<RagPage />} path="/rag" />
      <Route element={<KnowledgePipelineCanvasPage />} path="/rag/:kbId/pipeline" />
      <Route element={<KnowledgeEvaluationPage />} path="/rag/:kbId/evaluation" />
      <Route element={<KnowledgeInboxPage />} path="/rag/:kbId/inbox" />
      <Route element={<ChatPage />} path="/chat/:modelId" />
      <Route element={<WorkflowClassicPage />} path="/workflow" />
      <Route element={<WorkflowClassicPage />} path="/workflow/:id" />
      <Route element={<WorkflowClassicPage />} path="/workflow/classic" />
      <Route element={<WorkflowClassicPage />} path="/workflow/classic/:id" />
      <Route element={<WorkflowNativePage />} path="/workflow-native" />
      <Route element={<WorkflowNativePage />} path="/workflow-native/:id" />
      <Route element={<SystemSettingsPage />} path="/settings" />
      <Route element={<Navigate replace to="/models" />} path="*" />
    </Routes>
  );
}
