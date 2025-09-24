"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { createSupabaseClient, hasSupabaseEnv } from "@/lib/supabase/client";

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const BYPASS =
    process.env.NEXT_PUBLIC_BYPASS_AUTH === "true" ||
    process.env.NEXT_PUBLIC_BYPASS_AUTH === "1";

  if (BYPASS) {
    // Development bypass: no auth required
    return <>{children}</>;
  }

  useEffect(() => {
    if (!hasSupabaseEnv()) return;
    const supabase = createSupabaseClient();
    supabase.auth.getSession().then(({ data }) => {
      if (!data.session) {
        router.replace("/login");
      } else {
        setReady(true);
      }
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_event, session) => {
      if (!session) router.replace("/login");
      else setReady(true);
    });
    return () => {
      sub.subscription.unsubscribe();
    };
  }, [router]);

  if (!hasSupabaseEnv()) {
    return (
      <div className="p-6 text-sm">
        Missing Supabase config. Set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY in
        apps/web/.env.local (see apps/web/.env.example).
      </div>
    );
  }

  if (!ready) return null;
  return <>{children}</>;
}
