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
    <>
      <aside className="fixed left-0 top-0 z-30 hidden h-screen w-64 flex-col overflow-hidden border-r border-slate-200 bg-white px-4 py-5 lg:flex">
        <BrandBlock />
        <NavItems pathname={pathname} />
      </aside>
      <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/95 px-4 py-3 backdrop-blur lg:hidden">
        <div className="mb-3 flex items-center justify-between gap-3">
          <BrandBlock compact />
        </div>
        <NavItems pathname={pathname} mobile />
      </header>
    </>
  );
}

function BrandBlock({ compact = false }: { compact?: boolean }) {
  return (
    <div className={compact ? "" : "mb-7"}>
      <div className="text-xs font-semibold uppercase tracking-normal text-sky-700">Gas Isotope Ratio Mass Spectrometer</div>
      <div className={cn("font-serif font-semibold leading-tight tracking-normal text-slate-900", compact ? "text-lg" : "mt-1 text-2xl")}>
        IRMS Results Analyzer
      </div>
    </div>
  );
}

function NavItems({ pathname, mobile = false }: { pathname: string; mobile?: boolean }) {
  return (
    <nav className={mobile ? "flex gap-1 overflow-x-auto pb-1" : "space-y-1"}>
      {navItems.map(({ href, label, icon: Icon }) => (
        <Link
          key={href}
          href={href}
          className={cn(
            "flex shrink-0 items-center gap-2 rounded-lg border border-transparent px-3 py-2 text-sm font-medium tracking-normal transition-colors",
            pathname === href
              ? "border-slate-800 bg-slate-900 text-white"
              : "text-slate-700 hover:border-slate-200 hover:bg-slate-50 hover:text-slate-900",
          )}
        >
          <Icon className="h-4 w-4 shrink-0" />
          {label}
        </Link>
      ))}
    </nav>
  );
}
