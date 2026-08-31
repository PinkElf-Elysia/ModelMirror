import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import "./index.css";

const MainApplication = lazy(() => import("./MainApplication"));
const WorkflowFormPage = lazy(() => import("./pages/WorkflowFormPage"));
const isPublicWorkflowFormPath = /^\/forms\/[^/]+\/?$/.test(window.location.pathname);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Suspense fallback={<main aria-label="正在加载页面" className="min-h-screen bg-ink-950" role="status" />}>
        {isPublicWorkflowFormPath ? (
          <Routes>
            <Route element={<WorkflowFormPage />} path="/forms/:formId" />
          </Routes>
        ) : (
          <MainApplication />
        )}
      </Suspense>
    </BrowserRouter>
  </StrictMode>,
);
