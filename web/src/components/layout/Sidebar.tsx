import { useEffect, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { InstallBadge } from "./InstallBadge";
import { KillSwitch } from "./KillSwitch";
import { ModeToggle } from "./ModeToggle";
import { ProviderInline } from "./ProviderInline";
import { SidebarBrand } from "./SidebarBrand";
import { SidebarRobot } from "@/components/robot/SidebarRobot";
import { SidebarTelemetry } from "./SidebarTelemetry";
import { SoundToggle } from "./SoundToggle";

interface NavItem { to: string; label: string; short: string; kbd?: string; }

const NAV_GROUPS: { title: string; items: NavItem[] }[] = [
  {
    title: "WORK",
    items: [
      { to: "/",         label: "HOME",     short: "HOME", kbd: "H" },
      { to: "/overview", label: "OVERVIEW", short: "OVR",  kbd: "O" },
      { to: "/chat",     label: "CHAT",     short: "CHAT", kbd: "C" },
    ],
  },
  {
    title: "STATE",
    items: [
      { to: "/tasks",  label: "TASKS",  short: "TASK", kbd: "T" },
      { to: "/memory", label: "MEMORY", short: "MEM",  kbd: "M" },
      { to: "/tools",  label: "TOOLS",  short: "TOOL", kbd: "X" },
    ],
  },
  {
    title: "LOGS",
    items: [
      { to: "/traces", label: "TRACES", short: "TRC", kbd: "R" },
      { to: "/logs",   label: "LOGS",   short: "LOG", kbd: "L" },
    ],
  },
];

const KBD_MAP: Record<string, string> = {};
NAV_GROUPS.forEach(g => g.items.forEach(i => { if (i.kbd) KBD_MAP[i.kbd.toLowerCase()] = i.to; }));

export function Sidebar() {
  const navigate = useNavigate();

  // Pending skill proposals → a badge on OVERVIEW so a self-authored
  // skill awaiting approval is discoverable, not buried on the page.
  const [pendingProposals, setPendingProposals] = useState(0);
  useEffect(() => {
    let cancelled = false;
    const load = () =>
      api.proposals("pending")
        .then((ps) => { if (!cancelled) setPendingProposals(ps.length); })
        .catch(() => undefined);
    load();
    const t = setInterval(load, 30_000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const tag = (e.target as HTMLElement).tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || (e.target as HTMLElement).isContentEditable) return;
      const dest = KBD_MAP[e.key.toLowerCase()];
      if (dest) navigate(dest);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [navigate]);

  return (
    <aside
      className="fixed top-0 left-0 bottom-0 z-30 flex flex-col brut-sidebar"
      style={{
        background: "var(--color-surface-1)",
        borderRight: "1px solid var(--color-border)",
        fontFamily: "var(--font-mono)",
      }}
    >
      <style>{`
        .brut-sidebar .nav-row {
          color: var(--color-text-dim);
          background: transparent;
          border: 1px solid transparent;
          position: relative;
          overflow: hidden;
          transition: color 0.15s, border-color 0.15s;
        }
        /* sweep shimmer on hover */
        .brut-sidebar .nav-row::after {
          content: "";
          position: absolute;
          inset: 0;
          background: linear-gradient(90deg, transparent, rgba(124,254,0,0.10), transparent);
          transform: translateX(-100%);
          transition: transform 0.5s ease;
          pointer-events: none;
        }
        .brut-sidebar .nav-row:hover::after { transform: translateX(100%); }
        .brut-sidebar .nav-row:hover { color: var(--color-text); }
        .brut-sidebar .nav-row.active {
          color: var(--color-accent);
          border-color: var(--color-accent);
        }
        .brut-sidebar .nav-row.active:hover { color: var(--color-accent); }
        .brut-sidebar .nav-row .marker {
          color: var(--color-text-faint);
          opacity: 0;
          transform: translateX(-3px);
          transition: opacity 0.15s, transform 0.15s;
        }
        .brut-sidebar .nav-row:hover .marker { opacity: 1; transform: none; color: var(--color-text-muted); }
        .brut-sidebar .nav-row.active .marker { opacity: 1; transform: none; color: var(--color-accent); }
        .brut-sidebar {
          width: 220px;
        }
        @media (max-width: 760px) {
          .brut-sidebar {
            right: 0;
            bottom: auto;
            width: auto;
            height: 58px;
            border-right: none !important;
            border-bottom: 1px solid var(--color-border);
            overflow: hidden;
            overflow-y: hidden;
          }
          /* Mobile top-bar shows only the <nav>. The previous position-
             based selectors (:first-child, :nth-last-child(N)) broke
             after React injected a <style> tag as the actual first
             child — :first-child was hiding the style tag (no-op) and
             SidebarBrand stayed visible.  :not(nav) is stable against
             child-order changes. */
          .brut-sidebar > *:not(nav) {
            display: none;
          }
          .brut-sidebar nav {
            flex: none;
            height: 58px;
            padding: 6px 10px;
            flex-direction: row;
            align-items: stretch;
            gap: 4px;
            width: 100%;
            min-width: 0;
            max-width: 100vw;
            overflow-x: auto;
            overflow-y: hidden;
            scrollbar-width: none;
          }
          .brut-sidebar nav::-webkit-scrollbar {
            display: none;
          }
          .brut-sidebar nav > div {
            display: flex;
            align-items: stretch;
            gap: 2px;
            flex: 0 0 auto;
          }
          .brut-sidebar nav > div > div:first-child {
            display: none;
          }
          .brut-sidebar nav > div > div:last-child {
            display: flex;
            align-items: center;
            gap: 4px;
          }
          .brut-sidebar .nav-row {
            display: flex !important;
            align-items: center;
            height: 44px;
            padding: 0 8px !important;
            white-space: nowrap;
          }
          .brut-sidebar .nav-label-full {
            display: none;
          }
          .brut-sidebar .nav-label-short {
            display: inline;
          }
          .brut-sidebar .nav-row .marker,
          .brut-sidebar .nav-row .kbd {
            display: none !important;
          }
        }
        @media (min-width: 761px) {
          .brut-sidebar .nav-label-short {
            display: none;
          }
        }
      `}</style>

      <SidebarBrand />

      {/* Nav groups */}
      <nav className="flex-1 px-3 pt-3 pb-2 flex flex-col gap-3 overflow-y-auto">
        {NAV_GROUPS.map((group) => (
          <div key={group.title}>
            {/* Group header — the visual separation between sections
                (gap-3 wrap above) already signals grouping; the leading
                "── " was redundant operator-noise. */}
            <div
              className="text-[9px] uppercase tracking-[0.22em] mb-1 px-1"
              style={{ color: "var(--color-text-faint)" }}
            >
              {group.title}
            </div>
            <div>
              {group.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.to === "/"}
                  className={({ isActive }) =>
                    `nav-row block px-1 py-[4px] text-[11px] uppercase tracking-[0.05em] ${isActive ? "active" : ""}`
                  }
                >
                  {({ isActive }) => (
                    <span className="flex items-center gap-2">
                      <span className="marker" style={{ width: 16, display: "inline-block" }}>
                        {isActive ? ">" : " "}
                      </span>
                      <span className="nav-label-full" style={{ flex: 1 }}>{item.label}</span>
                      <span className="nav-label-short" style={{ flex: 1 }}>{item.short}</span>
                      {item.to === "/overview" && pendingProposals > 0 && (
                        <span
                          title={`${pendingProposals} skill proposal${pendingProposals > 1 ? "s" : ""} awaiting approval`}
                          style={{
                            fontSize: 9,
                            lineHeight: 1,
                            padding: "2px 5px",
                            borderRadius: 2,
                            color: "var(--color-bg)",
                            background: "var(--color-warning)",
                            boxShadow: "0 0 8px color-mix(in srgb, var(--color-warning) 60%, transparent)",
                            fontVariantNumeric: "tabular-nums",
                          }}
                        >
                          {pendingProposals}
                        </span>
                      )}
                      {item.kbd && (
                        <span
                          className="kbd"
                          style={{
                            fontSize: 9,
                            color: "var(--color-text-faint)",
                            opacity: 0.7,
                          }}
                        >
                          {item.kbd}
                        </span>
                      )}
                    </span>
                  )}
                </NavLink>
              ))}
            </div>
          </div>
        ))}
      </nav>

      {/* Ambient operator telemetry — fills the previously empty middle
          column with at-a-glance signals (next fire, heartbeat age,
          budget, current model). Hidden on the mobile top-bar layout
          via the .brut-sidebar media query. */}
      <SidebarTelemetry />

      <SidebarRobot />

      {/* Footer */}
      <div className="px-3 pb-2 pt-2 flex flex-col gap-1" style={{ borderTop: "1px solid var(--color-border)" }}>
        <SoundToggle />
        <ModeToggle />
        <KillSwitch />
        <ProviderInline />
        <InstallBadge />
      </div>
    </aside>
  );
}
