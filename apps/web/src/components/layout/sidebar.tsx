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
    <aside className="flex h-screen w-72 flex-col border-r border-stone-200 bg-[linear-gradient(180deg,#f7f4ef,white)] px-5 py-6">
      <div className="mb-8">
        <div className="text-xs font-semibold uppercase tracking-[0.2em] text-amber-700">IRMS Dashboard</div>
        <div className="mt-2 font-serif text-2xl font-semibold text-stone-900">P2L Analyzer</div>
      </div>
      <nav className="space-y-2">
        {navItems.map(({ href, label, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            className={cn(
              "flex items-center gap-3 rounded-xl px-4 py-3 text-sm font-medium transition-colors",
              pathname === href ? "bg-stone-900 text-white" : "text-stone-700 hover:bg-stone-100",
            )}
          >
            <Icon className="h-4 w-4" />
            {label}
          </Link>
        ))}
      </nav>
      <div className="mt-auto rounded-xl border border-stone-200 bg-white p-4 text-xs text-stone-500">
        Python remains the domain source of truth. The frontend renders Plotly JSON from the IRMS API.
      </div>
    </aside>
  );
}
