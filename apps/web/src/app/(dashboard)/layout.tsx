import { SessionHeader } from "@/components/layout/session-header";
import { Sidebar } from "@/components/layout/sidebar";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-slate-100">
      <Sidebar />
      <main className="min-h-screen lg:ml-64">
        <div className="flex w-full flex-col gap-5 px-4 py-5 sm:px-5 lg:px-6 2xl:px-8">
          <SessionHeader />
          {children}
        </div>
      </main>
    </div>
  );
}
