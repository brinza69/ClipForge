"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  LayoutDashboard,
  HardDrive,
  Settings,
  Zap,
  WifiOff,
  ChevronRight,
  TrendingUp,
  Wrench,
} from "lucide-react";

const navItems = [
  { label: "Dashboard",  href: "/",           icon: LayoutDashboard },
  { label: "Campaigns",  href: "/campaigns",  icon: TrendingUp },
  { label: "Utilities",  href: "/utilities",  icon: Wrench },
  { label: "Exports",    href: "/exports",    icon: HardDrive },
  { label: "Settings",   href: "/settings",   icon: Settings },
];

interface SidebarProps {
  onClose?: () => void;
}

export function Sidebar({ onClose }: SidebarProps) {
  const pathname = usePathname();

  return (
    <aside className="flex h-screen w-60 flex-col border-r border-border/40 bg-sidebar select-none">
      {/* Logo */}
      <div className="flex items-center gap-3 px-5 py-5">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-primary to-emerald-400 shadow-lg shadow-primary/20">
          <Zap className="h-5 w-5 text-primary-foreground" />
        </div>
        <div>
          <h1 className="text-base font-bold tracking-tight">ClipForge</h1>
          <p className="text-[10px] font-medium text-muted-foreground">
            AI Clipping Studio
          </p>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 space-y-0.5 px-3 py-2">
        {navItems.map((item) => {
          const active =
            item.href === "/"
              ? pathname === "/" || pathname.startsWith("/projects")
              : pathname.startsWith(item.href);

          return (
            <Link
              key={item.href}
              href={item.href}
              onClick={onClose}
              className={cn(
                "group flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors duration-150",
                active
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground",
              )}
            >
              <item.icon
                className={cn(
                  "h-[18px] w-[18px]",
                  active ? "text-primary" : "text-muted-foreground group-hover:text-foreground",
                )}
              />
              {item.label}
              {active && <ChevronRight className="ml-auto h-3.5 w-3.5 text-primary/50" />}
            </Link>
          );
        })}
      </nav>

      {/* Footer — worker status placeholder */}
      <div className="border-t border-border/20 px-4 py-3">
        <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
          <span className="relative flex h-2 w-2">
            <span className="absolute h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />
            <span className="relative h-2 w-2 rounded-full bg-emerald-500" />
          </span>
          <span className="text-emerald-400/80">Ready</span>
        </div>
      </div>
    </aside>
  );
}
