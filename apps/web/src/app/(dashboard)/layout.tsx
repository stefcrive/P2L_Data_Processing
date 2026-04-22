import { SessionHeader } from "@/components/layout/session-header";
import { Sidebar } from "@/components/layout/sidebar";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen">
      <Sidebar />
      <main className="ml-[18.75rem] min-h-screen bg-[linear-gradient(180deg,rgba(255,255,255,0.5),rgba(239,246,255,0.92))]">
        <div className="flex w-full flex-col gap-7 px-6 py-8 lg:px-8 2xl:px-10">
          <SessionHeader />
          {children}
        </div>
      </main>
    </div>
  );
}
