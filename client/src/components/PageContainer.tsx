import { type ReactNode } from "react";
import { type ResourceKey } from "../theme/resources";
import ResourceNav from "./ResourceNav";
import SystemCapabilityBar from "./SystemCapabilityBar";

interface PageContainerProps {
  activeResource?: ResourceKey;
  children: ReactNode;
  className?: string;
  contentClassName?: string;
  hideSidebar?: boolean;
  maxWidthClassName?: string;
  sidebar?: ReactNode;
}

export default function PageContainer({
  activeResource,
  children,
  className = "",
  contentClassName = "",
  hideSidebar = false,
  maxWidthClassName = "max-w-[1480px]",
  sidebar,
}: PageContainerProps) {
  const sidebarContent = (
    <div className="space-y-5">
      {sidebar ? (
        <>
          {sidebar}
          <div className="border-t border-white/10" />
        </>
      ) : null}
      <SystemCapabilityBar />
    </div>
  );

  return (
    <main
      className={`museum-grid relative min-h-screen overflow-hidden pb-28 pt-5 text-slate-100 lg:pt-24 ${className}`}
    >
      <ResourceNav activeResource={activeResource} />

      <div
        className={`relative mx-auto w-full ${maxWidthClassName} px-4 py-6 sm:px-6 lg:px-8 lg:py-8`}
      >
        {!hideSidebar ? (
          <div className="mb-5 xl:hidden">
            <SystemCapabilityBar compact />
          </div>
        ) : null}

        {hideSidebar ? (
          <div className={`min-w-0 ${contentClassName}`}>{children}</div>
        ) : sidebar ? (
          <div className="grid min-w-0 gap-6 xl:grid-cols-[260px_minmax(0,1fr)]">
            <aside className="hidden xl:block">
              <div className="surface-panel sticky top-28 rounded-lg p-4">
                {sidebarContent}
              </div>
            </aside>
            <div className={`min-w-0 ${contentClassName}`}>
              {children}
            </div>
          </div>
        ) : (
          <div className="grid min-w-0 gap-6 xl:grid-cols-[260px_minmax(0,1fr)]">
            <aside className="hidden xl:block">
              <div className="surface-panel sticky top-28 rounded-lg p-4">
                {sidebarContent}
              </div>
            </aside>
            <div className={`min-w-0 ${contentClassName}`}>
              {children}
            </div>
          </div>
        )}
      </div>
    </main>
  );
}
