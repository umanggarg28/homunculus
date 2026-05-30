import { useEffect } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { ModeToggle } from "./ModeToggle";
import { ProviderInline } from "./ProviderInline";
import { SidebarBrand } from "./SidebarBrand";
import { SidebarRobot } from "@/components/robot/SidebarRobot";
import { SoundToggle } from "./SoundToggle";

interface NavItem { to: string; label: string; kbd?: string; }

const NAV_GROUPS: { title: string; items: NavItem[] }[] = [
  {
    title: "WORK",
    items: [
      { to: "/",         label: "HOME",     kbd: "H" },
      { to: "/overview", label: "OVERVIEW", kbd: "O" },
      { to: "/chat",     label: "CHAT",     kbd: "C" },
    ],
  },
  {
    title: "STATE",
    items: [
      { to: "/tasks",  label: "TASKS",  kbd: "T" },
      { to: "/memory", label: "MEMORY", kbd: "M" },
      { to: "/tools",  label: "TOOLS",  kbd: "X" },
    ],
  },
  {
    title: "LOGS",
    items: [
      { to: "/traces", label: "TRACES", kbd: "R" },
      { to: "/logs",   label: "LOGS",   kbd: "L" },
    ],
  },
];

const KBD_MAP: Record<string, string> = {};
NAV_GROUPS.forEach(g => g.items.forEach(i => { if (i.kbd) KBD_MAP[i.kbd.toLowerCase()] = i.to; }));

export function Sidebar() {
  const navigate = useNavigate();

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
        width: 220,
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
      `}</style>

      <SidebarBrand />

      {/* Nav groups */}
      <nav className="flex-1 px-3 pt-3 pb-2 flex flex-col gap-3 overflow-y-auto">
        {NAV_GROUPS.map((group) => (
          <div key={group.title}>
            <div
              className="text-[9px] uppercase tracking-[0.22em] mb-1 px-1"
              style={{ color: "var(--color-text-faint)" }}
            >
              ── {group.title}
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
                      <span style={{ flex: 1 }}>{item.label}</span>
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

      <SidebarRobot />

      {/* Footer */}
      <div className="px-3 pb-2 pt-2 flex flex-col gap-1" style={{ borderTop: "1px solid var(--color-border)" }}>
        <SoundToggle />
        <ModeToggle />
        <ProviderInline />
      </div>
    </aside>
  );
}
