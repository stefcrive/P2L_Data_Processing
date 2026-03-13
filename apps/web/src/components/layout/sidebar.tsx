"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { FlaskConical, LayoutDashboard, Microscope, Settings2 } from "lucide-react";

import { cn } from "@/lib/utils";

const navItems = [
  { href: "/import", label: "Data Import", icon: LayoutDashboard },
  { href: "/diagnostics", label: "Diagnostics", icon: Microscope },
  { href: "/calibration", label: "Calibration", icon: FlaskConical },
  { href: "/processing", label: "Data Processing", icon: Settings2 },
];

export function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="flex h-screen w-[18.75rem] flex-col border-r border-slate-200/80 bg-[linear-gradient(180deg,#eaf3ff_0%,#f7fbff_66%)] px-5 py-7">
      <div className="mb-10">
        <div className="text-[0.83rem] font-semibold uppercase tracking-[0.25em] text-sky-700">IRMS Dashboard</div>
        <div className="mt-3 font-serif text-[2.1rem] font-semibold leading-none text-slate-900">P2L Analyzer</div>
      </div>
      <nav className="space-y-3">
        {navItems.map(({ href, label, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            className={cn(
              "flex items-center gap-3 rounded-lg border border-transparent px-4 py-3 text-[0.95rem] font-medium tracking-[0.01em] transition-colors",
              pathname === href
                ? "border-slate-800 bg-slate-900 text-white"
                : "text-slate-700 hover:border-slate-200 hover:bg-white/75 hover:text-slate-900",
            )}
          >
            <Icon className="h-4 w-4 shrink-0" />
            {label}
          </Link>
        ))}
      </nav>
      <div className="mt-auto rounded-xl border border-slate-200 bg-[linear-gradient(180deg,rgba(255,255,255,0.95),rgba(241,247,255,0.9))] p-4 text-xs leading-relaxed text-slate-500">
        Python remains the domain source of truth. The frontend renders Plotly JSON from the IRMS API.
      </div>
    </aside>
  );
}
