"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  LayoutDashboard,
  Users,
  FlaskConical,
  BookOpen,
  Boxes,
  Bot,
  BookMarked,
  ShieldCheck,
} from "lucide-react";

const navItems = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/clients", label: "Clients", icon: Users },
  { href: "/analyses", label: "Analyses", icon: FlaskConical },
  { href: "/lab-book", label: "Lab Book", icon: BookOpen },
  { href: "/inventory", label: "Inventory", icon: Boxes },
  { href: "/agents", label: "Agents", icon: Bot },
  { href: "/knowledge", label: "Knowledge", icon: BookMarked },
  { href: "/admin", label: "Admin", icon: ShieldCheck },
];

export function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="w-64 border-r bg-card min-h-screen p-4">
      <div className="text-xl font-bold mb-4">IRMS Lab</div>
      <nav className="space-y-1">
        {navItems.map((item) => {
          const Icon = item.icon;
          const active = pathname === item.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-2 rounded-md px-3 py-2 text-sm hover:bg-accent",
                active && "bg-accent"
              )}
            >
              <Icon className="h-4 w-4" />
              {item.label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
