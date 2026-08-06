import { AppHeader } from "@/components/layout/sidebar";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-[var(--canvas)]">
      <AppHeader />
      <main className="min-h-screen overflow-x-clip pt-[var(--app-header-height,106px)]">
        <div className="flex w-full flex-col gap-5 px-3 py-5 sm:px-4 lg:px-6">{children}</div>
      </main>
    </div>
  );
}
