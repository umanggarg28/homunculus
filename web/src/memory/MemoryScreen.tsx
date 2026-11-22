import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useNavigationType, useSearchParams } from "react-router-dom";
import { useUndo } from "@/hooks/useUndo";
import { useJobsToday } from "@/hooks/useJobsToday";
import { StaleError, memoryApi, type MemoryAbout, type MemoryLogEntry, type MemoryPage, type MemoryPageInfo } from "@/lib/api";
import { Presence } from "@/presence/Presence";
import { SCREEN_STATE } from "@/shell/Shell";
import { TabBar, TopBar, useLaterNote } from "@/shell/Nav";
import { voiceOf } from "@/shell/voice";
import { useAgentState } from "@/state/agentState";
import { ChangeLog, Claims, Editor, PageList, Section, SourceList, drafts, pageHref, sourceHref, when } from "./views";
import { confirmGuess, describeSave, numberSources, parsePage, removeLine, withHistory, type Claim } from "./wiki";

type Where =
  | { view: "about" }
  | { view: "list" }
  | { view: "changes" }
  | { view: "page"; path: string }
  | { view: "source"; path: string };

/** The screen an address shows. Every change of place is an address, so Back works. */
function where(pathname: string): Where {
  const rest = decodeURI(pathname.replace(/^\/memory\/?/, ""));
  if (rest === "pages") return { view: "list" };
  if (rest === "changes") return { view: "changes" };
  if (rest.startsWith("wiki/")) return { view: "page", path: rest };
  if (rest.startsWith("raw/")) return { view: "source", path: rest };
  return { view: "about" };
}

const quoted = (title: string) => `“${title}”`;
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** What the agent knows about the operator: the page list, About you, each page with
 *  its sources, the raw sources and the change log. Everything shown is read from the
 *  memory folder through the server; every change is a save of a page's markdown. */
export function MemoryScreen() {
  const location = useLocation();
  const navigate = useNavigate();
  const navigation = useNavigationType();
  const [params, setParams] = useSearchParams();
  const at = where(location.pathname);
  const state = useAgentState();
  const today = useJobsToday();
  const later = useLaterNote();
  const undo = useUndo();

  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((r) => r + 1), []);
  const [pages, setPages] = useState<MemoryPageInfo[]>([]);
  const [about, setAbout] = useState<MemoryAbout | null>(null);
  const [log, setLog] = useState<MemoryLogEntry[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const page = usePage(at.view === "page" ? at.path : null, revision);
  const [chosen, setChosen] = useState<string | null>(null);
  useEffect(() => setChosen(null), [location.pathname]);

  useEffect(() => {
    memoryApi.pages().then(setPages, () => setPages([]));
    memoryApi.about().then(setAbout, () => setAbout(null));
    memoryApi.log().then(setLog, () => setLog([]));
  }, [revision]);

  // The status dot and the presence follow the agent here too.
  const screenState = SCREEN_STATE[state.kind];
  useEffect(() => {
    const root = document.documentElement;
    root.dataset.state = screenState;
  }, [screenState]);
  useEffect(() => {
    const root = document.documentElement;
    root.dataset.page = "memory";
    // The opening belongs to Now.
    if (root.dataset.intro !== "done") root.dataset.intro = "done";
    return () => {
      delete root.dataset.page;
    };
  }, []);

  // A message belongs to the place it was said.
  useEffect(() => setMessage(null), [location.pathname]);

  // Arriving on a place: focus its heading, or on Back, the row you left from.
  const leftFrom = useRef(new Map<string, string>());
  useEffect(() => {
    const remember = () => {
      const key = (document.activeElement as HTMLElement | null)?.closest<HTMLElement>("[data-key]")?.dataset.key;
      if (key) leftFrom.current.set(location.pathname, key);
    };
    document.addEventListener("click", remember, true);
    return () => document.removeEventListener("click", remember, true);
  }, [location.pathname]);
  useEffect(() => {
    const key = navigation === "POP" ? leftFrom.current.get(location.pathname) : undefined;
    const target = key ? document.querySelector<HTMLElement>(`[data-key="${CSS.escape(key)}"]`) : null;
    (target ?? document.querySelector<HTMLElement>("#mem-h"))?.focus({ preventScroll: !!target });
  }, [location.pathname, navigation]);

  /** Save a page as the operator's edit, with an Undo that puts the earlier text back. */
  const save = useCallback(
    async (from: MemoryPage, text: string, title: string, said: (saved: MemoryPage) => string) => {
      const saved = await memoryApi.save(from.path, text, from.version);
      refresh();
      setMessage(said(saved));
      undo.push(`your change to ${quoted(title)}`, async () => {
        try {
          await memoryApi.save(from.path, from.text, saved.version);
          return `${quoted(title)} is back as it was.`;
        } catch (e) {
          if (e instanceof StaleError) return `${quoted(title)} changed since, so I left it as it is.`;
          throw e;
        } finally {
          refresh();
        }
      });
      return saved;
    },
    [refresh, undo],
  );

  const guessActions = (page: MemoryPage, title: string) => ({
    confirm: (claim: Claim) =>
      save(page, confirmGuess(page.text, claim.line), title, () => `Confirmed ${quoted(claim.text)} on ${quoted(title)}.`).catch(stale),
    remove: (claim: Claim) =>
      save(page, removeLine(page.text, claim.line), title, () => `Removed ${quoted(claim.text)} from ${quoted(title)}.`).catch(stale),
  });
  const stale = (e: unknown) => {
    refresh();
    setMessage(e instanceof StaleError ? "That page changed since you opened it. Here it is now; try again." : String(e));
  };

  const doUndo = async () => {
    try {
      setMessage(await undo.undo());
    } catch (e) {
      setMessage(String(e));
    }
  };

  const sheet = params.get("panel");
  const closeSheet = () => navigate(-1);
  const sub = at.view === "list" ? "list" : at.view === "page" || at.view === "source" ? "page" : "about";

  let say = "";
  let meta: string | null = null;
  let attn = false;
  let main: JSX.Element;
  let side: JSX.Element | null = null;

  const guesses = pages.reduce((n, p) => n + p.guesses, 0);
  const sourceCount = about?.sources.length ?? 0;
  if (at.view === "page") {
    const parsed = page ? parsePage(page.text) : null;
    const numbers = parsed ? numberSources(parsed) : [];
    main = (
      <PageView
        key={at.path}
        path={at.path}
        page={page}
        chosen={chosen}
        onChoose={(s) => setChosen(chosen === s ? null : s)}
        onSave={save}
        actions={guessActions}
        openSheet={() => setParams({ panel: "sources" })}
      />
    );
    side = (
      <aside className={`pg-side panel mem-side${sheet === "sources" ? " is-sheet" : ""}`} aria-label="Sources">
        <div className="panel-head">
          <h2>Sources</h2>
          {sheet === "sources" && (
            <button type="button" className="btn btn--quiet" onClick={closeSheet}>
              Close
            </button>
          )}
        </div>
        <SourceList numbers={numbers} chosen={chosen} onChoose={(s) => setChosen(chosen === s ? null : s)} />
      </aside>
    );
    if (parsed) {
      const facts = parsed.items.filter((i) => i.type === "claim" && (i.kind === "fact" || i.kind === "mine")).length;
      const current = numbers.filter((src) =>
        parsed.items.some((i) => i.type === "claim" && i.kind !== "old" && i.cites.some((c) => parsed.footnotes[c] === src)),
      ).length;
      const pageGuesses = parsed.items.filter((i) => i.type === "claim" && i.kind === "guess").length;
      say = `${parsed.title || at.path}: ${plural(facts, "fact")}${current ? `, from ${plural(current, "source")}` : ""}.`;
      attn = pageGuesses > 0;
      meta = pageGuesses ? `${plural(pageGuesses, "guess", "guesses")} to check.` : "Every line here is yours or names its source.";
    }
  } else if (at.view === "source") {
    main = <SourceView path={at.path} added={about?.sources.find((s) => s.path === at.path)?.added ?? null} />;
  } else if (at.view === "changes") {
    main = (
      <div className="pg-scroll">
        <div className="pg-head">
          <h1 id="mem-h" tabIndex={-1}>
            Recent changes
          </h1>
        </div>
        <ChangeLog entries={log} pages={pages} />
      </div>
    );
  } else {
    main = <AboutView about={about} pages={pages} revision={revision} onSave={save} actions={guessActions} />;
    side = (
      <aside className="pg-side panel mem-side mem-side--about" aria-label="Recent changes">
        <div className="panel-head">
          <h2>Recent changes</h2>
        </div>
        <ChangeLog entries={log} pages={pages} limit={12} />
      </aside>
    );
  }
  if (at.view !== "page") {
    say = `I keep ${plural(pages.length, "page")} about you, from ${plural(sourceCount, "source")}.`;
    attn = guesses > 0;
    meta = guesses ? `${plural(guesses, "guess", "guesses")} to check.` : "Nothing waits for you to check.";
  }

  const pill = voiceOf(state, today, new Date()).pill;
  return (
    <>
      <TopBar current="memory" pill={pill} onLater={later.show} />
      <div className="pg pg-mem" data-sub={sub} data-sheet={sheet ? "true" : undefined}>
        <PageList pages={pages} current={at.view === "page" ? at.path : at.view === "about" ? "about" : ""} drafts={new Set(drafts.keys())} />
        <main className="pg-main panel" aria-labelledby="mem-h">
          <div className="stage">
            <Presence />
            <div className="voice">
              <p className="say">{say || " "}</p>
              <p className={`say-meta${attn && !message && !undo.top ? " is-attn" : ""}`} aria-live="polite">
                {message && <span>{message} </span>}
                {undo.top ? (
                  <button ref={undo.button} type="button" className="linkbtn" onClick={doUndo}>
                    Undo {undo.top.label}
                  </button>
                ) : (
                  !message && meta
                )}
              </p>
            </div>
          </div>
          <div className="pg-body">{main}</div>
        </main>
        {side}
        {sheet && <div className="pg-scrim" onClick={closeSheet} aria-hidden="true" />}
        <TabBar current="memory" onLater={later.show} />
      </div>
      {later.view}
    </>
  );
}

function usePage(path: string | null, revision: number): MemoryPage | null | undefined {
  // undefined while loading; null when there is no such page.
  const [page, setPage] = useState<MemoryPage | null | undefined>(undefined);
  useEffect(() => {
    if (path === null) return;
    let live = true;
    memoryApi.page(path).then(
      (p) => live && setPage(p),
      () => live && setPage(null),
    );
    return () => {
      live = false;
    };
  }, [path, revision]);
  return page;
}

type SaveFn = (from: MemoryPage, text: string, title: string, said: (saved: MemoryPage) => string) => Promise<MemoryPage>;
type GuessActions = (page: MemoryPage, title: string) => { confirm: (c: Claim) => void; remove: (c: Claim) => void };

function PageView({
  path,
  page,
  chosen,
  onChoose,
  onSave,
  actions,
  openSheet,
}: {
  path: string;
  page: MemoryPage | null | undefined;
  chosen: string | null;
  onChoose: (source: string) => void;
  onSave: SaveFn;
  actions: GuessActions;
  openSheet: () => void;
}) {
  const navigate = useNavigate();
  const location = useLocation();
  const [editing, setEditing] = useState(() => drafts.has(path));
  const [showOld, setShowOld] = useState(false);
  const edit = useRef<HTMLButtonElement>(null);

  if (page === undefined) return <div className="pg-scroll" />;
  if (page === null) {
    return (
      <div className="pg-scroll">
        <div className="pg-head">
          <h1 id="mem-h" tabIndex={-1}>
            {path}
          </h1>
        </div>
        <p className="sec-note">There is no page here. It may have been moved or deleted.</p>
      </div>
    );
  }
  const parsed = parsePage(page.text);
  const title = parsed.title || path;
  const numbers = numberSources(parsed);
  const olds = parsed.items.filter((i) => i.type === "claim" && i.kind === "old").length;
  const guessCount = parsed.items.filter((i) => i.type === "claim" && i.kind === "guess").length;

  const back = () => (location.key !== "default" ? navigate(-1) : navigate("/memory/pages"));

  return (
    <div className="pg-scroll mem-page">
      <div className="mem-head">
        <button type="button" className="btn btn--quiet only-narrow" onClick={back}>
          Back
        </button>
        <h1 id="mem-h" tabIndex={-1}>
          {title}
        </h1>
        <p className="sec-note">
          <span className="mono">{path}</span>
          {parsed.summary && <> · {parsed.summary}</>}
        </p>
        {!editing && (
          <div className="row-acts">
            <button ref={edit} type="button" className="btn btn--quiet" onClick={() => setEditing(true)}>
              Edit
            </button>
          </div>
        )}
      </div>
      {editing ? (
        <Editor
          label={title}
          page={page}
          onSave={async (text, from) => {
            const saved = withHistory(from.text, text);
            if (saved.text === from.text) {
              setEditing(false);
              return;
            }
            await onSave(from, saved.text, title, () => `Saved your edits to ${quoted(title)}: ${describeSave(saved)}.`);
            setEditing(false);
          }}
          onClose={() => {
            setEditing(false);
            requestAnimationFrame(() => edit.current?.focus());
          }}
        />
      ) : (
        <>
          {guessCount > 0 && <p className="sec-note">Lines marked Guess are ones I inferred. You never said them.</p>}
          <Claims
            page={parsed}
            path={path}
            numbers={numbers}
            showOld={showOld}
            chosen={chosen}
            onCite={onChoose}
            actions={actions(page, title)}
          />
          {parsed.seeAlso.length > 0 && (
            <Section title="See also">
              <ul className="list links">
                {parsed.seeAlso.map((link) => {
                  const target = path.split("/").slice(0, -1).concat(link.href).join("/");
                  return (
                    <li key={link.href}>
                      <Link to={pageHref(target.replace(/[^/]+\/\.\.\//g, ""))}>{link.title}</Link>
                    </li>
                  );
                })}
              </ul>
            </Section>
          )}
          <div className="page-foot">
            {olds > 0 && (
              <button type="button" className="btn btn--quiet" aria-expanded={showOld} onClick={() => setShowOld(!showOld)}>
                {showOld ? "Hide earlier lines" : `Show earlier lines (${olds})`}
              </button>
            )}
            <button type="button" className="btn btn--quiet src-open" onClick={openSheet}>
              Sources ({numbers.length})
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function AboutView({
  about,
  pages,
  revision,
  onSave,
  actions,
}: {
  about: MemoryAbout | null;
  pages: MemoryPageInfo[];
  revision: number;
  onSave: SaveFn;
  actions: GuessActions;
}) {
  const [withGuesses, setWithGuesses] = useState<MemoryPage[]>([]);
  const [editing, setEditing] = useState(false);
  useEffect(() => {
    const paths = pages.filter((p) => p.guesses > 0).map((p) => p.path);
    Promise.all(paths.map((p) => memoryApi.page(p))).then(setWithGuesses, () => setWithGuesses([]));
  }, [pages, revision]);

  const guesses = withGuesses.flatMap((page) => {
    const parsed = parsePage(page.text);
    const title = parsed.title || page.path;
    return parsed.items
      .filter((i): i is Claim => i.type === "claim" && i.kind === "guess")
      .map((claim) => ({ claim, page, title, sources: claim.cites.map((c) => parsed.footnotes[c]).filter(Boolean) }));
  });
  const profile = about?.profile;
  const parsedProfile = profile ? parsePage(profile.text) : null;

  return (
    <div className="pg-scroll mem-about">
      <div className="pg-head">
        <h1 id="mem-h" tabIndex={-1}>
          About you
        </h1>
        <div className="row-acts only-narrow">
          <Link className="btn btn--quiet only-phone" to="/memory/pages">
            Pages
          </Link>
          <Link className="btn btn--quiet" to="/memory/changes">
            Recent changes
          </Link>
        </div>
      </div>

      <Section title="Guesses to check" attn={guesses.length > 0}>
        <p className="sec-note">I inferred these. You never said them, so they stay out of your profile until you confirm them.</p>
        {guesses.length ? (
          <ul className="list guesses">
            {guesses.map(({ claim, page, title, sources }) => {
              const act = actions(page, title);
              return (
                <li key={`${page.path}:${claim.line}`}>
                  <span className="name">{claim.text}</span>
                  <span className="line">
                    From {sources.map((s, i) => (
                      <span key={s}>
                        {i > 0 && ", "}
                        <Link className="mono" to={sourceHref(s)}>
                          {s.replace(/^raw\//, "")}
                        </Link>
                      </span>
                    ))}
                    , on <Link to={pageHref(page.path)}>{title}</Link>
                  </span>
                  <span className="g-acts">
                    <button type="button" className="btn btn--ink btn--quiet" aria-label={`Confirm “${claim.text}”`} onClick={() => act.confirm(claim)}>
                      Confirm
                    </button>
                    <button type="button" className="btn btn--quiet" aria-label={`Remove “${claim.text}”`} onClick={() => act.remove(claim)}>
                      Remove
                    </button>
                  </span>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="sec-note">No guesses to check.</p>
        )}
      </Section>

      <Section
        title="Your profile"
        note={profile ? `Always in my prompt. ${profile.text.length.toLocaleString("en-GB")} of ${profile.cap.toLocaleString("en-GB")} characters.` : undefined}
      >
        {profile && parsedProfile && (editing ? (
          <Editor
            label="Your profile"
            page={profile}
            cap={profile.cap}
            onSave={async (text, from) => {
              const saved = withHistory(from.text, text);
              if (saved.text !== from.text) {
                await onSave(from, saved.text, "your profile", () => `Saved your profile: ${describeSave(saved)}.`);
              }
              setEditing(false);
            }}
            onClose={() => setEditing(false)}
          />
        ) : (
          <>
            {parsedProfile.items.length ? (
              <Claims page={parsedProfile} path="profile.md" numbers={numberSources(parsedProfile)} showOld={false} chosen={null} />
            ) : (
              <p className="sec-note">Empty so far.</p>
            )}
            <div className="row-acts">
              <button type="button" className="btn btn--quiet" onClick={() => setEditing(true)}>
                Edit
              </button>
            </div>
          </>
        ))}
      </Section>

      <Section title="What I read" note={about ? plural(about.sources.length, "source") : undefined}>
        {about && about.sources.length ? (
          <ul className="list srcs">
            {about.sources.map((source) => (
              <li key={source.path}>
                <Link className="name mono" to={sourceHref(source.path)} data-key={source.path}>
                  {source.path}
                </Link>
                <span className="line">{source.added ? `Added ${when(source.added.slice(0, 16).replace("T", " "))}` : "Not recorded yet"}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="sec-note">Nothing yet.</p>
        )}
      </Section>

      <Section title="Where your data can go">
        <p className="sec-note">Nothing else leaves this machine.</p>
        <ul className="list egress">
          {about?.egress.map((route) => (
            <li key={route.to}>
              <span className="name">
                {route.by} to <span className="mono">{route.to}</span>
              </span>
              <span className="line">{route.what}.</span>
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}

function SourceView({ path, added }: { path: string; added: string | null }) {
  const [text, setText] = useState<string | null | undefined>(undefined);
  const navigate = useNavigate();
  const location = useLocation();
  useEffect(() => {
    memoryApi.source(path).then(
      (s) => setText(s.text),
      () => setText(null),
    );
  }, [path]);
  return (
    <div className="pg-scroll mem-page">
      <div className="mem-head">
        <button
          type="button"
          className="btn btn--quiet only-narrow"
          onClick={() => (location.key !== "default" ? navigate(-1) : navigate("/memory/about"))}
        >
          Back
        </button>
        <h1 id="mem-h" tabIndex={-1}>
          {path.split("/").pop()}
        </h1>
        <p className="sec-note">
          <span className="mono">{path}</span>
          {added && <> · Added {when(added.slice(0, 16).replace("T", " "))}</>}. A source is kept as it is; nothing changes it.
        </p>
      </div>
      {text === null ? <p className="sec-note">There is no source here.</p> : <pre className="pv-wrap">{text ?? ""}</pre>}
    </div>
  );
}
