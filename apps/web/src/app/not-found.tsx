"use client";

import Link from "next/link";
import { AppHeader } from "@/components/layout/sidebar";
import { useTranslation } from "@/components/layout/language-provider";
import { Button } from "@/components/ui/button";

export default function NotFound() {
  const tr = useTranslation();
  return <div className="min-h-screen bg-[var(--canvas)]"><AppHeader /><main className="mx-auto max-w-xl px-4 pb-10 pt-[calc(var(--app-header-height)+4rem)]">
    <div className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm"><span className="font-mono text-xs text-blue-700">404</span><h1 className="mt-2 font-display text-2xl font-semibold">{tr("Page not found")}</h1><p className="my-4 text-sm text-slate-600">{tr("This address does not match a page. Use the navigation above or return to Import.")}</p><Button asChild><Link href="/import">{tr("Return to Import")}</Link></Button></div>
  </main></div>;
}
