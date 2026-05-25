import { NavLink } from "react-router-dom";
import { ModeToggle } from "./ModeToggle";
import { ProviderInline } from "./ProviderInline";
import { SidebarBrand } from "./SidebarBrand";
// Canvas robot kept in tree (SidebarRobot.tsx) — character now lives
// inside SidebarBrand (top of sidebar), rendered as ASCII face.

interface NavItem { to: string; label: string; }

/** Brutalist nav. `>` prefix on the active item, mono uppercase labels,
 *  no rounded corners. Active row inverts to accent-on-black. Hover
 *  shifts the label color toward accent. Groups separated by ASCII
 *  section labels — like a directory listing. */
const NAV_GROUPS: { title: string; items: NavItem[] }[] = [
  {
    title: "WORK",
    items: [
      { to: "/",         label: "HOME" },
      { to: "/overview", label: "OVERVIEW" },
      { to: "/chat",     label: "CHAT" },
    ],
  },
  {
    title: "STATE",
    items: [
      { to: "/tasks",  label: "TASKS" },
      { to: "/memory", label: "MEMORY" },
      { to: "/skills", label: "SKILLS" },
    ],
  },
  {
    title: "LOGS",
    items: [
      { to: "/traces", label: "TRACES" },
      { to: "/logs",   label: "LOGS" },
    ],
  },
];

export function Sidebar() {
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
          transition: color 0.08s, background 0.08s;
        }
        .brut-sidebar .nav-row:hover { color: var(--color-accent); }
        .brut-sidebar .nav-row.active {
          color: var(--color-bg);
          background: var(--color-accent);
        }
        .brut-sidebar .nav-row.active:hover { color: var(--color-bg); }
        .brut-sidebar .nav-row .marker { color: var(--color-text-faint); }
        .brut-sidebar .nav-row.active .marker { color: var(--color-bg); }
      `}</style>

      <SidebarBrand />

      {/* Nav groups */}
      <nav className="flex-1 px-3 pt-4 pb-2 flex flex-col gap-5 overflow-y-auto">
        {NAV_GROUPS.map((group) => (
          <div key={group.title}>
            <div
              className="text-[9px] uppercase tracking-[0.22em] mb-2 px-1"
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
                    `nav-row block px-1 py-[5px] text-[12px] uppercase tracking-[0.05em] ${isActive ? "active" : ""}`
                  }
                >
                  {({ isActive }) => (
                    <span className="flex items-center gap-2">
                      <span className="marker" style={{ width: 16, display: "inline-block" }}>
                        {isActive ? ">" : " "}
                      </span>
                      <span>{item.label}</span>
                    </span>
                  )}
                </NavLink>
              ))}
            </div>
          </div>
        ))}
      </nav>

      {/* Footer */}
      <div className="px-3 pb-3 pt-3 flex flex-col gap-2" style={{ borderTop: "1px solid var(--color-border)" }}>
        <ModeToggle />
        <ProviderInline />
      </div>
    </aside>
  );
}
