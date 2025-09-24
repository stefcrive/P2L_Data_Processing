"use client";

import { createSupabaseClient } from "@/src/lib/supabase/client";
import { Button } from "@/src/components/ui/button";

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

