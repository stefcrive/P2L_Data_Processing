import { Sidebar } from "@/components/sidebar";
import { AuthGuard } from "@/components/auth-guard";
import { UserMenu } from "@/components/user-menu";

export default function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const BYPASS =
    process.env.NEXT_PUBLIC_BYPASS_AUTH === "true" ||
    process.env.NEXT_PUBLIC_BYPASS_AUTH === "1";

  const Shell = (
    <div className="flex">
      <Sidebar />
      <main className="flex-1 p-6">
        <div className="flex items-center mb-6">
          <div className="text-sm text-muted-foreground">
            {BYPASS ? "Dev mode (auth bypassed)" : "Signed in"}
          </div>
          <UserMenu />
        </div>
        {children}
      </main>
    </div>
  );

  if (BYPASS) return Shell;
  return <AuthGuard>{Shell}</AuthGuard>;
}
