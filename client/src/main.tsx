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
      <Suspense fallback={<main className="min-h-screen bg-slate-50" aria-label="正在载入页面" />}>
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
