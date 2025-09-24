import { Sidebar } from "@/src/components/sidebar";
import { AuthGuard } from "@/src/components/auth-guard";
import { UserMenu } from "@/src/components/user-menu";

export default function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AuthGuard>
      <div className="flex">
        <Sidebar />
        <main className="flex-1 p-6">
          <div className="flex items-center mb-6">
            <div className="text-sm text-muted-foreground">Signed in</div>
            <UserMenu />
          </div>
          {children}
        </main>
      </div>
    </AuthGuard>
  );
}
