"use client";

import { createSupabaseClient, hasSupabaseEnv } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";

export function UserMenu() {
  const BYPASS =
    process.env.NEXT_PUBLIC_BYPASS_AUTH === "true" ||
    process.env.NEXT_PUBLIC_BYPASS_AUTH === "1";

  if (BYPASS || !hasSupabaseEnv()) {
    return (
      <div className="ml-auto text-xs text-muted-foreground">Dev mode (auth bypassed)</div>
    );
  }

  const supabase = createSupabaseClient();
  return (
    <div className="ml-auto">
      <Button
        variant="outline"
        onClick={async () => {
          await supabase.auth.signOut();
        }}
      >
        Sign out
      </Button>
    </div>
  );
}
