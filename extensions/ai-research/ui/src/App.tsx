import { Route, Routes } from "react-router-dom";

import { PageHeader } from "./components/Page";
import { Shell } from "./components/Shell";
import { OverviewPage } from "./pages/OverviewPage";
import { ProjectDetailPage } from "./pages/ProjectDetailPage";
import { ProjectNewPage } from "./pages/ProjectNewPage";
import { ProjectReviewPage } from "./pages/ProjectReviewPage";
import { ProjectSourcesPage } from "./pages/ProjectSourcesPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { RunDetailPage } from "./pages/RunDetailPage";
import { RunEventsPage } from "./pages/RunEventsPage";
import { RunEvidencePage } from "./pages/RunEvidencePage";
import { RunsPage } from "./pages/RunsPage";
import { SystemPage } from "./pages/SystemPage";

function NotFoundPage() {
  return <div className="page"><PageHeader eyebrow="404" title="页面不存在" description="该地址不属于模镜科研控制台。" /><a className="button mt-6" href="/">返回总览</a></div>;
}

export function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<OverviewPage />} />
        <Route path="projects" element={<ProjectsPage />} />
        <Route path="projects/new" element={<ProjectNewPage />} />
        <Route path="projects/:projectId" element={<ProjectDetailPage />} />
        <Route path="projects/:projectId/sources" element={<ProjectSourcesPage />} />
        <Route path="projects/:projectId/review" element={<ProjectReviewPage />} />
        <Route path="runs" element={<RunsPage />} />
        <Route path="runs/:runId" element={<RunDetailPage />} />
        <Route path="runs/:runId/events" element={<RunEventsPage />} />
        <Route path="runs/:runId/evidence" element={<RunEvidencePage />} />
        <Route path="system" element={<SystemPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
