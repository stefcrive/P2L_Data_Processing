import { SessionHeader } from "@/components/layout/session-header";
import { Sidebar } from "@/components/layout/sidebar";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <main className="flex-1">
        <div className="flex w-full flex-col gap-6 px-8 py-8">
          <SessionHeader />
          {children}
        </div>
      </main>
    </div>
  );
}
