import { NavLink } from "react-router-dom";
import clsx from "clsx";
import { ModeToggle } from "./ModeToggle";
import { ProviderInline } from "./ProviderInline";
import { HeartbeatPulse } from "./HeartbeatPulse";

interface NavItem { to: string; label: string; icon: React.ReactNode; }

const NAV: NavItem[] = [
  { to: "/",        label: "Overview", icon: <Icon path="M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z" /> },
  { to: "/chat",    label: "Chat",     icon: <Icon path="M3 12c0-4.4 4-8 9-8s9 3.6 9 8-4 8-9 8a10 10 0 0 1-3.4-.6L4 21l1.4-4A8 8 0 0 1 3 12Z" /> },
  { to: "/live",    label: "Live",     icon: <Icon path="M12 2v4M12 18v4M22 12h-4M6 12H2M19 19l-2.5-2.5M7.5 7.5 5 5M19 5l-2.5 2.5M7.5 16.5 5 19" /> },
  { to: "/traces",  label: "Traces",   icon: <Icon path="M3 12h3l2-7 4 14 2-7h7" /> },
  { to: "/tasks",   label: "Tasks",    icon: <Icon path="M9 11l3 3L22 4M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" /> },
  { to: "/skills",  label: "Skills",   icon: <Icon path="M12 2 4 6v6c0 5 3.5 9 8 10 4.5-1 8-5 8-10V6l-8-4Z" /> },
  { to: "/memory",  label: "Memory",   icon: <Icon path="M21 12a9 9 0 1 1-9-9M21 12h-6M12 3v6M16.5 7.5 12 12" /> },
  { to: "/logs",    label: "Logs",     icon: <Icon path="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6ZM14 2v6h6M8 13h8M8 17h8M8 9h2" /> },
];

export function Sidebar() {
  return (
    <aside
      className="fixed top-0 left-0 bottom-0 z-30 flex flex-col"
      style={{
        width: 220,
        background: "var(--color-surface-1)",
        borderRight: "1px solid var(--color-border)",
      }}
    >
      {/* Brand + heartbeat pulse */}
      <div className="px-4 pt-4 pb-5 flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div
            className="w-6 h-6 rounded-[5px] grid place-items-center"
            style={{ background: "var(--color-accent)", color: "#001815", fontWeight: 700, fontSize: 13 }}
          >
            H
          </div>
          <span
            className="text-[14px] font-semibold tracking-tight"
            style={{ color: "var(--color-text)", fontFamily: "var(--font-display)" }}
          >
            Homunculus
          </span>
        </div>
        <HeartbeatPulse />
      </div>

      {/* Nav */}
      <nav className="flex-1 px-2 flex flex-col gap-0.5">
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            className={({ isActive }) =>
              clsx(
                "flex items-center gap-2.5 px-2.5 h-8 rounded-[6px] text-[13px] transition-colors",
                isActive
                  ? "bg-[var(--color-surface-3)] text-[var(--color-text)]"
                  : "text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface-2)]",
              )
            }
          >
            <span style={{ width: 14, height: 14, display: "inline-flex" }}>{item.icon}</span>
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>

      {/* Footer: mode toggle + provider */}
      <div className="px-3 pb-4 pt-3 border-t border-[var(--color-border)] flex flex-col gap-3">
        <ModeToggle />
        <ProviderInline />
      </div>
    </aside>
  );
}

function Icon({ path }: { path: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="14"
      height="14"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={path} />
    </svg>
  );
}
