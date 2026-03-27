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
    <aside className="flex h-screen w-[18.75rem] flex-col border-r border-slate-200/80 bg-[linear-gradient(180deg,#eaf3ff_0%,#f7fbff_66%)] px-5 py-6">
      <div className="mb-8">
        <div className="text-[0.72rem] font-semibold uppercase tracking-[0.2em] text-sky-700">Gas Isotope Ratio Mass Spectrometer</div>
        <div className="mt-2 font-serif text-[2rem] font-semibold leading-[1.02] text-slate-900">IRMS Results</div>
        <div className="font-serif text-[2rem] font-semibold leading-[1.02] text-slate-900">Analyzer</div>
      </div>
      <nav className="space-y-1.5">
        {navItems.map(({ href, label, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            className={cn(
              "flex items-center gap-2.5 rounded-lg border border-transparent px-3.5 py-2.5 text-[0.95rem] font-medium tracking-[0.01em] transition-colors",
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
    </aside>
  );
}
