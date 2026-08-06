"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { FlaskConical, Import, Microscope, Settings, SlidersHorizontal } from "lucide-react";
import { useEffect, useRef } from "react";

import { SessionHeader } from "@/components/layout/session-header";
import { cn } from "@/lib/utils";

const navItems = [
  { href: "/import", label: "Import", icon: Import },
  { href: "/diagnostics", label: "Diagnostics", icon: Microscope },
  { href: "/calibration", label: "Calibration", icon: FlaskConical },
  { href: "/processing", label: "Processing", icon: SlidersHorizontal },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function AppHeader() {
  const pathname = usePathname();
  const router = useRouter();
  const headerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const header = headerRef.current;
    if (!header) return;
    const syncHeight = () => {
      document.documentElement.style.setProperty("--app-header-height", `${header.getBoundingClientRect().height}px`);
    };
    syncHeight();
    const observer = new ResizeObserver(syncHeight);
    observer.observe(header);
    window.addEventListener("resize", syncHeight);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", syncHeight);
    };
  }, []);

  return (
    <header ref={headerRef} className="fixed inset-x-0 top-0 z-40 border-b border-slate-200 bg-white/95 shadow-sm backdrop-blur">
      <div className="mx-auto flex min-h-14 max-w-[1680px] items-center gap-3 px-3 sm:px-4 lg:px-6">
        <Link href="/import" className="group flex min-w-0 items-center gap-2.5" aria-label="IRMS Results Analyzer home">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-md bg-blue-700 font-mono text-[11px] font-semibold text-white shadow-sm">
            IR
          </span>
          <span className="min-w-0">
            <span className="block truncate font-display text-sm font-semibold leading-tight text-slate-950">IRMS Results Analyzer</span>
            <span className="hidden truncate font-mono text-[10px] text-slate-500 sm:block">Isotope measurement workspace</span>
          </span>
        </Link>

        <nav className="ml-auto hidden items-center gap-1 lg:flex" aria-label="Primary navigation">
          {navItems.map(({ href, label, icon: Icon }) => {
            const active = pathname === href;
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex h-8 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400",
                  active
                    ? "bg-blue-50 text-blue-800 shadow-[inset_0_0_0_1px_rgb(191_219_254)]"
                    : "text-slate-600 hover:bg-slate-100 hover:text-slate-950",
                )}
              >
                <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                {label}
              </Link>
            );
          })}
        </nav>

        <label className="ml-auto flex min-w-0 items-center gap-2 lg:hidden">
          <span className="sr-only">Current section</span>
          <select
            className="h-9 max-w-[10.5rem] rounded-md border border-slate-300 bg-white px-2.5 text-sm font-medium text-slate-800 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100"
            value={navItems.find((item) => item.href === pathname)?.href ?? "/import"}
            onChange={(event) => router.push(event.target.value)}
          >
            {navItems.map((item) => (
              <option key={item.href} value={item.href}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <SessionHeader />
    </header>
  );
}

export const Sidebar = AppHeader;
