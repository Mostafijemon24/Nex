"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { Analyze } from "./dashboard/analyze";
import { ApiProviders } from "./dashboard/providers";
import { Requirements } from "./dashboard/requirements";
import { Results } from "./dashboard/results";
import { Settings } from "./dashboard/settings";
import type { Notify, Project, Provider, Screen, Session } from "./dashboard/types";
import { message } from "./dashboard/ui";

const NAV: [Screen, string][] = [
  ["analyze", "Analyze"],
  ["results", "Results"],
  ["api", "API"],
  ["reqs", "Requirements"],
  ["settings", "Settings"],
];

const GUIDE: [string, string][] = [
  ["Add a source", "Give Nex a website URL, a Git repository or a ZIP file. You can add more than one."],
  ["Choose what to create", "Pick the documents, formats and limits. The defaults are a good start."],
  ["Run the analysis", "Nex opens the site in a real browser and reads the code. You can pause, resume or retry at any time."],
  ["Read your results", "Documents, screenshots, video and SEO drafts appear under Results. Every section shows where it was observed."],
];
const GUIDE_KEY = "nex-guide-seen-v2";

export function Workspace() {
  const [session, setSession] = useState<Session | null>(null);
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);
  const [bootError, setBootError] = useState("");
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [project, setProject] = useState<Project | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [screen, setScreen] = useState<Screen>("analyze");
  const [step, setStep] = useState(1);
  const [guide, setGuide] = useState<number | null>(null);
  const [toast, setToast] = useState<{ text: string; kind: "ok" | "error" } | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const notify: Notify = useCallback((text, kind = "ok") => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast({ text, kind });
    toastTimer.current = setTimeout(() => setToast(null), kind === "error" ? 6000 : 3500);
  }, []);

  async function refreshSession() {
    const status = await api<{ needs_setup: boolean }>("/api/v1/setup/status");
    setNeedsSetup(status.needs_setup);
    if (status.needs_setup) return;
    try {
      setSession(await api<Session>("/api/v1/auth/me"));
    } catch {
      setSession(null);
    }
  }

  const loadProject = useCallback(async (id: string) => {
    if (!id) {
      setProject(null);
      return;
    }
    setProject(await api<Project>(`/api/v1/projects/${id}`));
  }, []);

  const refreshProjects = useCallback(async (selected?: string) => {
    const body = await api<{ projects: Project[] }>("/api/v1/projects");
    setProjects(body.projects);
    const next = body.projects.find((item) => item.id === selected)?.id || (selected === "" ? "" : body.projects[0]?.id || "");
    setProjectId(next);
    await loadProject(next);
    return next;
  }, [loadProject]);

  const refreshProviders = useCallback(async () => {
    const body = await api<{ providers: Provider[] }>("/api/v1/ai-providers");
    setProviders(body.providers);
  }, []);

  useEffect(() => {
    refreshSession().catch((reason) => setBootError(message(reason, "Could not reach the API.")));
  }, []);

  useEffect(() => {
    if (!session) return;
    refreshProjects().catch((reason) => notify(message(reason, "Could not load projects."), "error"));
    refreshProviders().catch(() => undefined);
    let seen = false;
    try {
      seen = localStorage.getItem(GUIDE_KEY) === "1";
    } catch {
      seen = false;
    }
    if (!seen) setGuide(0);
  }, [session, refreshProjects, refreshProviders, notify]);

  function closeGuide() {
    setGuide(null);
    try {
      localStorage.setItem(GUIDE_KEY, "1");
    } catch {
      /* storage unavailable: the guide just shows again next time */
    }
  }

  if (needsSetup === null) {
    return (
      <main className="auth">
        <div className="logo"><div className="logo-mark">N</div><span className="logo-name">Nex</span></div>
        {bootError ? (
          <div className="glass panel">
            <h1>API not connected</h1>
            <p className="note red">{bootError}</p>
            <p className="muted">This site is only the frontend. Deploy the API (Postgres, Redis, workers), set <code>NEXT_PUBLIC_API_URL</code> to its public origin, and redeploy.</p>
          </div>
        ) : (
          <p className="muted" style={{ textAlign: "center" }}>Checking setup…</p>
        )}
      </main>
    );
  }

  if (!session) return <Auth needsSetup={needsSetup} onDone={refreshSession} />;

  const g = guide !== null ? GUIDE[guide] : null;

  return (
    <>
      <header className="topbar">
        <div className="logo"><div className="logo-mark">N</div><span className="logo-name">Nex</span></div>
        <nav className="segmented" aria-label="Main">
          {NAV.map(([id, label]) => (
            <button key={id} type="button" aria-selected={screen === id} onClick={() => setScreen(id)}>{label}</button>
          ))}
        </nav>
        <div className="top-actions">
          <label className="sr-only" htmlFor="project-select">Project</label>
          <select
            id="project-select"
            className="project-select"
            value={projectId}
            onChange={(event) => {
              const id = event.target.value;
              setProjectId(id);
              setStep(1);
              if (!id) setScreen("analyze");
              loadProject(id).catch((reason) => notify(message(reason, "Could not load the project."), "error"));
            }}
          >
            {projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            <option value="">+ New project</option>
          </select>
          <button type="button" className="btn-tint" onClick={() => setGuide(0)}>Guide</button>
          <button
            type="button"
            className="btn-quiet"
            title={`${session.user.email} · ${session.organization.name}`}
            onClick={() => api("/api/v1/auth/logout", { method: "POST" }).then(() => setSession(null)).catch((reason) => notify(message(reason, "Sign out failed."), "error"))}
          >
            Sign out
          </button>
        </div>
      </header>

      <main className="main">
        {screen === "analyze" && (
          <Analyze
            project={project}
            providers={providers}
            step={step}
            setStep={setStep}
            notify={notify}
            onProjectCreated={async (id) => { await refreshProjects(id); }}
            reloadProject={() => loadProject(project?.id || "")}
            goResults={() => setScreen("results")}
          />
        )}
        {screen === "results" && <Results project={project} providers={providers} notify={notify} goAnalyze={() => { setScreen("analyze"); setStep(1); }} />}
        {screen === "api" && <ApiProviders providers={providers} reload={refreshProviders} notify={notify} />}
        {screen === "reqs" && <Requirements project={project} notify={notify} />}
        {screen === "settings" && <Settings project={project} isAdmin={session.user.role === "admin" || session.user.role === "owner"} notify={notify} />}
      </main>

      {g && guide !== null && (
        <div className="overlay" role="dialog" aria-modal="true" aria-labelledby="guide-title">
          <div className="guide">
            <div className="row-between">
              <span className="eyebrow" style={{ textTransform: "none", letterSpacing: 0, fontSize: 13 }}>Step {guide + 1} of {GUIDE.length}</span>
              <button type="button" className="btn-danger-link" onClick={closeGuide}>Skip guide</button>
            </div>
            <div className="guide-num">{guide + 1}</div>
            <h2 id="guide-title">{g[0]}</h2>
            <p>{g[1]}</p>
            <div className="row-between">
              <div className="dots">{GUIDE.map((_, i) => <span key={i} className={i === guide ? "on" : ""} />)}</div>
              <div className="row" style={{ gap: 8 }}>
                {guide > 0 && <button type="button" className="btn-secondary" style={{ minHeight: 46, padding: "11px 18px" }} onClick={() => setGuide(guide - 1)}>Back</button>}
                <button
                  type="button"
                  className="btn-primary"
                  style={{ minHeight: 46, padding: "11px 24px" }}
                  onClick={() => (guide === GUIDE.length - 1 ? closeGuide() : setGuide(guide + 1))}
                >
                  {guide === GUIDE.length - 1 ? "Get started" : "Next"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {toast && <div className={`toast${toast.kind === "error" ? " error" : ""}`} role="status">{toast.text}</div>}
    </>
  );
}

function Auth({ needsSetup, onDone }: { needsSetup: boolean; onDone: () => Promise<void> }) {
  const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      await api(needsSetup ? "/api/v1/setup" : "/api/v1/auth/login", { method: "POST", body: JSON.stringify(Object.fromEntries(form)) });
      await onDone();
    } catch (reason) {
      setError(message(reason, "Request failed."));
    }
  }
  return (
    <main className="auth">
      <div className="logo"><div className="logo-mark">N</div><span className="logo-name">Nex</span></div>
      <h1>{needsSetup ? "Create the administrator" : "Sign in"}</h1>
      <form className="glass" onSubmit={submit}>
        {error && <p className="note red">{error}</p>}
        <label className="field">Email<input name="email" type="email" autoComplete="username" required /></label>
        <label className="field">Password<input name="password" type="password" autoComplete={needsSetup ? "new-password" : "current-password"} minLength={needsSetup ? 12 : 1} required /></label>
        {needsSetup && (
          <>
            <label className="field">Name<input name="display_name" required /></label>
            <label className="field">Organization<input name="organization_name" required /></label>
          </>
        )}
        <button className="btn-primary" type="submit">{needsSetup ? "Create administrator" : "Sign in"}</button>
      </form>
    </main>
  );
}
