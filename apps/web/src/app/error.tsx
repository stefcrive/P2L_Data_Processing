"use client";

import Link from "next/link";
import { useTranslation } from "@/components/layout/language-provider";
import { Button } from "@/components/ui/button";

export default function PageError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  const tr = useTranslation();
  return <div role="alert" className="mx-auto my-10 max-w-xl rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
    <h1 className="font-display text-xl font-semibold">{tr("This page could not be loaded")}</h1><p className="my-4 text-sm text-slate-600">{tr("Try again, or return to Import to open a session.")}</p><div className="flex flex-wrap gap-2"><Button onClick={reset}>{tr("Try again")}</Button><Button asChild variant="outline"><Link href="/import">{tr("Return to Import")}</Link></Button></div>
  </div>;
}
