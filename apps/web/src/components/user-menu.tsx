"use client";

import { createSupabaseClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";

export function UserMenu() {
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
