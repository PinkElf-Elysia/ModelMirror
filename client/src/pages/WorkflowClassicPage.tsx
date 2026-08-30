import { useEffect, useMemo } from "react";
import { useParams } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import WorkflowEditor from "../components/workflow/WorkflowEditor";

function workflowIdFromParam(value: string | undefined) {
  if (!value || value === "new") return "draft";
  return value;
}

export default function WorkflowClassicPage() {
  const { id } = useParams();
  const workflowId = useMemo(() => workflowIdFromParam(id), [id]);

  useEffect(() => {
    document.title = "模镜 - 经典工作流";
  }, []);

  return (
    <PageContainer
      activeResource="agents"
      hideSidebar
      maxWidthClassName="max-w-[1920px]"
      showSystemCapabilityBar={false}
    >
      <WorkflowEditor key={workflowId} workflowId={workflowId} />
    </PageContainer>
  );
}
