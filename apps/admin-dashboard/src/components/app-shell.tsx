"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth/auth-context";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/restaurants", label: "Restaurants" },
  { href: "/ingestion", label: "Ingestion" },
  { href: "/review-queue", label: "Review Queue" },
  { href: "/agent-runs", label: "Agent Runs" },
  { href: "/audit-log", label: "Audit Log" },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { user, logout } = useAuth();

  return (
    <div className="flex min-h-screen">
      <aside className="border-line-strong bg-bg-soft flex w-56 shrink-0 flex-col border-r px-3 py-4">
        <div className="px-2 pb-4 text-[13px] font-bold tracking-tight text-ink">hungrX Admin</div>
        <nav className="flex flex-col gap-0.5">
          {NAV_ITEMS.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "rounded-md px-2.5 py-1.5 text-[12.5px] font-medium text-ink-soft hover:bg-white hover:text-ink",
                pathname.startsWith(item.href) && "bg-white text-ink shadow-sm",
              )}
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="border-line-strong mt-auto flex flex-col gap-2 border-t pt-3">
          {user && (
            <div className="px-2 text-[11.5px] text-ink-faint">
              <div className="font-medium text-ink-soft">{user.full_name || user.email}</div>
              <div>{user.role}</div>
            </div>
          )}
          <Button variant="outline" size="sm" onClick={() => void logout()}>
            Sign out
          </Button>
        </div>
      </aside>
      <main className="flex-1 overflow-y-auto">{children}</main>
    </div>
  );
}
