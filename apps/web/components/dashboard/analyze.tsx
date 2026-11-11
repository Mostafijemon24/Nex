"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../lib/api";
import type { Job, Notify, Project, ProjectSettings, Provider, Source } from "./types";
import { Badge, StatusBadge, formatDate, host, message } from "./ui";

const WIZ = ["Source", "Configure", "Run", "Results"];

type SrcType = "website" | "git" | "zip";
const SRC: Record<SrcType, { label: string; hint: string; placeholder: string }> = {
  website: { label: "Website", hint: "A public URL, opened in a real browser", placeholder: "https://example.com" },
  git: { label: "Git repository", hint: "Cloned and read, never executed", placeholder: "https://github.com/owner/repo.git" },
  zip: { label: "ZIP file", hint: "Plugin, theme or any source archive", placeholder: "" },
};

const KIND_LABEL: Record<string, string> = {
  website: "Website", github: "GitHub", git: "Git", zip: "ZIP", wp_plugin: "Plugin", wp_theme: "Theme",
  documentation: "Docs", api_spec: "API spec", recording: "Recording", screenshots: "Images",
};

/** Document types the backend accepts, with plain-language labels. The first group is shown up front. */
const DOC_TYPES: [string, string, string][] = [
  ["getting_started", "Getting started / overview", ""],
  ["feature_guide", "Feature documentation", ""],
  ["user_manual", "User guide", ""],
  ["technical", "Technical notes", "needs code"],
  ["wordpress_plugin", "Plugin documentation", "needs code"],
  ["wordpress_theme", "Theme documentation", "needs code"],
  ["architecture", "Architecture", "needs code"],
  ["installation", "Installation guide", ""],
];
const MORE_DOC_TYPES: [string, string][] = [
  ["tutorial", "Tutorial"], ["api_reference", "API reference"], ["database", "Database"], ["configuration", "Configuration guide"],
  ["troubleshooting", "Troubleshooting"], ["faq", "FAQ"], ["developer", "Developer guide"], ["integration", "Integrations"], ["release_notes", "Release notes"],
];
const FORMATS: [string, string][] = [["markdown", "Markdown"], ["html", "HTML"], ["pdf", "PDF"], ["docx", "DOCX"]];

/** Readable names for the pipeline steps in apps/api/app/services/pipeline.py. */
export const STEP_LABEL: Record<string, string> = {
  validate: "Validate inputs",
  discover: "Discover pages & screenshots",
  analyze_code: "Inspect source code",
  features: "Record features",
  changes: "Compare previous run",
  documents: "Write documents",
  video: "Render video",
  finalize: "Finalize",
};
const STEP_ORDER = Object.keys(STEP_LABEL);
const ACTIVE = ["queued", "running", "retrying"];

type Props = {
  project: Project | null;
  providers: Provider[];
  step: number;
  setStep: (step: number) => void;
  notify: Notify;
  onProjectCreated: (id: string) => Promise<void>;
  reloadProject: () => Promise<void>;
  goResults: () => void;
};

export function Analyze({ project, providers, step, setStep, notify, onProjectCreated, reloadProject, goResults }: Props) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const sources = project?.sources || [];
  const projectId = project?.id || "";

  const loadJobs = useCallback(async () => {
    if (!projectId) {
      setJobs([]);
      setJob(null);
      return;
    }
    const body = await api<{ jobs: Job[] }>(`/api/v1/projects/${projectId}/jobs`);
    setJobs(body.jobs);
    setJob(body.jobs[0] || null);
  }, [projectId]);

  useEffect(() => {
    loadJobs().catch((reason) => notify(message(reason, "Could not load jobs."), "error"));
  }, [loadJobs, notify]);

  // Poll the active job until the worker reports a final state.
  useEffect(() => {
    if (!job || !ACTIVE.includes(job.status)) return;
    const timer = setInterval(() => {
      api<Job>(`/api/v1/jobs/${job.id}`)
        .then((next) => {
          setJob(next);
          if (!ACTIVE.includes(next.status)) loadJobs().catch(() => undefined);
        })
        .catch((reason) => notify(message(reason, "Lost contact with the job."), "error"));
    }, 2000);
    return () => clearInterval(timer);
  }, [job, loadJobs, notify]);

  const hasResults = jobs.some((item) => item.status === "completed" || item.status === "completed_with_warnings");
  const current = job && (job.status === "completed" || job.status === "completed_with_warnings") && step === 3 ? 4 : step;

  function go(n: number) {
    if (n === 4) {
      if (hasResults) goResults();
      else notify("Results appear after a run finishes.");
      return;
    }
    if (n > 1 && !sources.length) {
      notify("Add at least one source first.");
      return;
    }
    setStep(n);
  }

  return (
    <>
      <div className="hero">
        <h1>Turn any website or codebase into <span className="gradient-text">clear documentation</span></h1>
        <p>Nex visits the real site, reads the real code, and writes only what it actually observed.</p>
      </div>

      <div className="wizard" aria-label="Steps">
        {WIZ.map((label, i) => {
          const n = i + 1;
          const done = n < current;
          const on = n === current;
          return (
            <div className="wizard-item" key={label}>
              <button type="button" className={`wizard-step${done ? " done" : ""}${on ? " on" : ""}`} aria-current={on ? "step" : undefined} onClick={() => go(n)}>
                <span className="wizard-dot">{done ? "✓" : n}</span>
                <span className="wizard-label">{label}</span>
              </button>
              {i < WIZ.length - 1 && <div className={`wizard-line${done ? " done" : ""}`} />}
            </div>
          );
        })}
      </div>

      {step === 1 && (
        <SourceStep project={project} sources={sources} notify={notify} onProjectCreated={onProjectCreated} reloadProject={reloadProject} next={() => go(2)} />
      )}
      {step === 2 && project && (
        <ConfigureStep key={project.id} project={project} providers={providers} notify={notify} reloadProject={reloadProject} back={() => setStep(1)} next={() => setStep(3)} />
      )}
      {step === 3 && project && (
        <RunStep project={project} providers={providers} job={job} jobs={jobs} setJob={setJob} reloadJobs={loadJobs} notify={notify} back={() => setStep(2)} goResults={goResults} />
      )}
    </>
  );
}

function SourceStep({ project, sources, notify, onProjectCreated, reloadProject, next }: {
  project: Project | null;
  sources: Source[];
  notify: Notify;
  onProjectCreated: (id: string) => Promise<void>;
  reloadProject: () => Promise<void>;
  next: () => void;
}) {
  const [type, setType] = useState<SrcType>("website");
  const [url, setUrl] = useState("");
  const [zipKind, setZipKind] = useState("zip");
  const [projectName, setProjectName] = useState("");
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  async function ensureProject(fallbackName: string) {
    if (project) return project.id;
    const name = projectName.trim() || fallbackName || "My project";
    const created = await api<Project>("/api/v1/projects", { method: "POST", body: JSON.stringify({ name, description: "" }) });
    await onProjectCreated(created.id);
    return created.id;
  }

  async function addUrl() {
    const value = url.trim();
    if (!/^https?:\/\/[^\s/]+\.[^\s]+/.test(value)) {
      notify("Enter a full address starting with https://", "error");
      return;
    }
    setBusy(true);
    try {
      const id = await ensureProject(host(value));
      const kind = type === "website" ? "website" : /github\.com/i.test(value) ? "github" : "git";
      // The server rejects private, internal and metadata addresses (SSRF protection).
      await api(`/api/v1/projects/${id}/sources`, {
        method: "POST",
        body: JSON.stringify({ kind, name: host(value) || value, password: "", config: { url: value } }),
      });
      setUrl("");
      if (project) await reloadProject();
      else await onProjectCreated(id);
      notify("Source added.");
    } catch (reason) {
      notify(message(reason, "Could not add the source."), "error");
    } finally {
      setBusy(false);
    }
  }

  async function addZip(file: File) {
    setBusy(true);
    try {
      const id = await ensureProject(file.name.replace(/\.zip$/i, ""));
      const form = new FormData();
      form.set("kind", zipKind);
      form.set("name", file.name);
      form.set("upload", file);
      await api(`/api/v1/projects/${id}/sources/upload`, { method: "POST", body: form });
      if (project) await reloadProject();
      else await onProjectCreated(id);
      notify("Archive uploaded. It will be read with static analysis only.");
    } catch (reason) {
      notify(message(reason, "Upload failed."), "error");
    } finally {
      setBusy(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function remove(source: Source) {
    try {
      await api(`/api/v1/sources/${source.id}`, { method: "DELETE" });
      await reloadProject();
    } catch (reason) {
      notify(message(reason, "Could not remove the source."), "error");
    }
  }

  async function test(source: Source) {
    try {
      await api(`/api/v1/sources/${source.id}/test`, { method: "POST" });
      await reloadProject();
    } catch (reason) {
      notify(message(reason, "Access test failed."), "error");
    }
  }

  return (
    <section className="glass card">
      <div className="card-head">
        <h2>What should Nex analyze?</h2>
        <span>Add one or more sources. You can mix a website with its code.</span>
      </div>

      {!project && (
        <label className="field">Project name
          <input value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder="Defaults to the site or file name" />
        </label>
      )}

      <div className="type-grid">
        {(Object.keys(SRC) as SrcType[]).map((key) => (
          <button key={key} type="button" className="type-card" aria-pressed={type === key} onClick={() => { setType(key); setUrl(""); }}>
            <strong>{SRC[key].label}</strong>
            <span>{SRC[key].hint}</span>
          </button>
        ))}
      </div>

      {type !== "zip" ? (
        <form className="row" onSubmit={(event) => { event.preventDefault(); addUrl(); }}>
          <label className="sr-only" htmlFor="source-url">{SRC[type].label} URL</label>
          <input id="source-url" className="input-lg" type="url" value={url} onChange={(event) => setUrl(event.target.value)} placeholder={SRC[type].placeholder} />
          <button type="submit" className="btn-primary" style={{ minHeight: 50, padding: "0 24px" }} disabled={busy}>{busy ? "Adding…" : "Add"}</button>
        </form>
      ) : (
        <div className="stack">
          <div className="row">
            {[["zip", "Source code"], ["wp_plugin", "WordPress plugin"], ["wp_theme", "WordPress theme"]].map(([value, label]) => (
              <label key={value} className="chip-check"><input type="radio" name="zip-kind" checked={zipKind === value} onChange={() => setZipKind(value)} />{label}</label>
            ))}
          </div>
          <div
            className="dropzone"
            role="button"
            tabIndex={0}
            onClick={() => fileInput.current?.click()}
            onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") fileInput.current?.click(); }}
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault();
              const file = event.dataTransfer.files?.[0];
              if (file) addZip(file);
            }}
          >
            <strong>{busy ? "Uploading…" : "Drop a .zip here or click to choose"}</strong>
            <span>Read with static analysis only. Uploaded code is never executed.</span>
          </div>
          <input ref={fileInput} type="file" accept=".zip,application/zip" hidden onChange={(event) => { const file = event.target.files?.[0]; if (file) addZip(file); }} />
        </div>
      )}

      <div className="stack">
        {sources.map((source) => (
          <div className="source-row" key={source.id}>
            <span className="source-kind">{KIND_LABEL[source.kind] || source.kind}</span>
            <div className="source-body">
              <strong>{source.config.url || source.name}</strong>
              <span>{source.status_detail || (source.last_checked_at ? `Checked ${formatDate(source.last_checked_at)}` : "Not checked yet")}</span>
            </div>
            <StatusBadge status={source.status} small />
            <button type="button" className="btn-link" onClick={() => test(source)}>Test access</button>
            <button type="button" className="btn-danger-link" onClick={() => remove(source)}>Remove</button>
          </div>
        ))}
        {!sources.length && <span className="muted" style={{ textAlign: "center", padding: 8, fontSize: 14 }}>No sources added yet.</span>}
      </div>

      <div className="row" style={{ justifyContent: "flex-end" }}>
        <button type="button" className="btn-primary" style={{ opacity: sources.length ? 1 : 0.45 }} onClick={next}>Continue</button>
      </div>
    </section>
  );
}

function ConfigureStep({ project, providers, notify, reloadProject, back, next }: {
  project: Project;
  providers: Provider[];
  notify: Notify;
  reloadProject: () => Promise<void>;
  back: () => void;
  next: () => void;
}) {
  const [s, setS] = useState<ProjectSettings>(project.settings);
  const [saving, setSaving] = useState(false);
  const hasCode = (project.sources || []).some((source) => ["zip", "wp_plugin", "wp_theme", "github", "git"].includes(source.kind));
  const connected = providers.filter((item) => item.status === "connected");

  function set<K extends keyof ProjectSettings>(key: K, value: ProjectSettings[K]) {
    setS((prev) => ({ ...prev, [key]: value }));
  }
  function toggle(list: "doc_types" | "output_formats", value: string) {
    setS((prev) => {
      const items = prev[list] || [];
      return { ...prev, [list]: items.includes(value) ? items.filter((item) => item !== value) : [...items, value] };
    });
  }

  async function save() {
    if (!s.output_formats.length) {
      notify("Choose at least one export format.", "error");
      return;
    }
    setSaving(true);
    try {
      await api(`/api/v1/projects/${project.id}/settings`, { method: "PUT", body: JSON.stringify({ settings: s }) });
      await reloadProject();
      next();
    } catch (reason) {
      notify(message(reason, "Could not save settings."), "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="glass card">
      <div className="card-head">
        <h2>What should Nex create?</h2>
        <span>The defaults work well. Change only what you need.</span>
      </div>
      <div className="two-col">
        <div className="stack" style={{ gap: 10 }}>
          <span className="eyebrow">Documents</span>
          {DOC_TYPES.map(([value, label, note]) => (
            <label key={value} className="check">
              <input type="checkbox" checked={s.doc_types.includes(value)} onChange={() => toggle("doc_types", value)} />
              <span style={{ flex: 1 }}>{label}</span>
              {note && <span className="muted" style={{ fontSize: 12, color: hasCode ? undefined : "var(--amber-fg)" }}>{note}</span>}
            </label>
          ))}
          <details className="advanced" style={{ borderTop: 0, paddingTop: 0 }}>
            <summary>More document types</summary>
            <div className="stack" style={{ marginTop: 10, gap: 10 }}>
              {MORE_DOC_TYPES.map(([value, label]) => (
                <label key={value} className="check"><input type="checkbox" checked={s.doc_types.includes(value)} onChange={() => toggle("doc_types", value)} />{label}</label>
              ))}
            </div>
          </details>
          <span className="eyebrow" style={{ marginTop: 10 }}>Export formats</span>
          <div className="row" style={{ gap: 8 }}>
            {FORMATS.map(([value, label]) => (
              <label key={value} className="chip-check"><input type="checkbox" checked={s.output_formats.includes(value)} onChange={() => toggle("output_formats", value)} />{label}</label>
            ))}
          </div>
        </div>

        <div className="stack" style={{ gap: 14 }}>
          <span className="eyebrow">Capture</span>
          <div className="note gray">Screenshots are lossless PNG at the browser viewport the server uses (1280×720). Higher resolutions are not available yet.</div>
          <label className="check"><input type="checkbox" checked={s.enable_video} onChange={(event) => set("enable_video", event.target.checked)} />Render an MP4 walkthrough from the screenshots</label>
          <label className={`check${s.tts_provider ? "" : " disabled"}`}>
            <input type="checkbox" disabled={!s.tts_provider} checked={s.enable_voiceover && Boolean(s.tts_provider)} onChange={(event) => set("enable_voiceover", event.target.checked)} />
            {s.tts_provider ? "Voiceover" : "Voiceover — no speech provider chosen (see Advanced)"}
          </label>
          <label className="field">Pages to crawl: up to {s.max_pages}
            <input type="range" min={1} max={30} step={1} value={s.max_pages} onChange={(event) => set("max_pages", Number(event.target.value))} />
          </label>
          <label className="field">AI provider for documents
            <select value={s.ai_provider_id || ""} onChange={(event) => { set("ai_provider_id", event.target.value); set("use_ai_documentation", Boolean(event.target.value)); }}>
              <option value="">None — observed facts only</option>
              {connected.map((item) => <option key={item.id} value={item.id}>{item.display_name || item.provider} · {item.model || "auto"}</option>)}
            </select>
          </label>
        </div>
      </div>

      <details className="advanced">
        <summary>Advanced</summary>
        <div className="grid">
          <label className="field">Analysis mode
            <select value={s.analysis_mode} onChange={(event) => set("analysis_mode", event.target.value)}>
              <option value="standard">Website and code</option>
              <option value="website_only">Website only</option>
              <option value="code_only">Code only</option>
            </select>
          </label>
          <label className="field">Safety level
            <select value={s.safety_level} onChange={(event) => set("safety_level", event.target.value)}>
              <option value="safe">Safe — no destructive clicks</option>
              <option value="recorded">Recorded — still no destructive clicks</option>
            </select>
          </label>
          <label className="field">Language<input value={s.documentation_language} onChange={(event) => set("documentation_language", event.target.value)} /></label>
          <label className="field">Audience<input value={s.target_audience} onChange={(event) => set("target_audience", event.target.value)} /></label>
          <label className="field" style={{ gridColumn: "1 / -1" }}>Allowed domains, comma separated
            <input value={(s.allowed_domains || []).join(", ")} onChange={(event) => set("allowed_domains", event.target.value.split(",").map((item) => item.trim()).filter(Boolean))} />
          </label>
          <label className="field">Maximum link depth<input type="number" min={0} max={3} value={s.max_depth} onChange={(event) => set("max_depth", Number(event.target.value))} /></label>
          <label className="field">Maximum seconds<input type="number" min={10} max={180} value={s.max_seconds} onChange={(event) => set("max_seconds", Number(event.target.value))} /></label>
          <label className="field">Speech provider
            <select value={s.tts_provider || ""} onChange={(event) => set("tts_provider", event.target.value)}>
              <option value="">None — silent video</option>
              <option value="openai">OpenAI speech</option>
              <option value="openai_compatible">OpenAI-compatible speech</option>
            </select>
          </label>
          <label className="field">Speech model<input value={s.tts_model} onChange={(event) => set("tts_model", event.target.value)} /></label>
          <label className="field">Voice<input value={s.tts_voice} onChange={(event) => set("tts_voice", event.target.value)} /></label>
        </div>
        <p className="muted" style={{ marginTop: 12 }}>Without a speech provider, a requested voiceover stays silent and is not marked as narrated. Slideshows are not browser interaction recordings.</p>
      </details>

      <div className="row-between">
        <button type="button" className="btn-secondary" onClick={back}>Back</button>
        <button type="button" className="btn-primary" onClick={save} disabled={saving}>{saving ? "Saving…" : "Continue"}</button>
      </div>
    </section>
  );
}

function RunStep({ project, providers, job, jobs, setJob, reloadJobs, notify, back, goResults }: {
  project: Project;
  providers: Provider[];
  job: Job | null;
  jobs: Job[];
  setJob: (job: Job) => void;
  reloadJobs: () => Promise<void>;
  notify: Notify;
  back: () => void;
  goResults: () => void;
}) {
  const s = project.settings;
  const status = job?.status || "idle";
  const done = status === "completed" || status === "completed_with_warnings";
  const failed = ["failed", "blocked", "cancelled"].includes(status);
  const active = ACTIVE.includes(status) || status === "paused";
  const provider = providers.find((item) => item.id === s.ai_provider_id && item.status === "connected");

  const view = {
    idle: { title: "Ready to run", sub: "Review, then start. You can pause at any time." },
    queued: { title: "Queued…", sub: "Waiting for a background worker to pick up the job." },
    running: { title: "Analyzing…", sub: `Nex is working through ${STEP_ORDER.length} steps in the background.` },
    retrying: { title: "Retrying…", sub: "The job is running again after a failure." },
    paused: { title: "Paused", sub: "The job stopped before the next step. Nothing is lost." },
    completed: { title: "Analysis complete", sub: "Every step finished. Your results are ready." },
    completed_with_warnings: { title: "Complete, with warnings", sub: "The run finished, but some steps reported limits. Read the notes below." },
    failed: { title: "Analysis failed", sub: "The job stopped and is not counted as complete." },
    blocked: { title: "Blocked", sub: "Something the job needs is missing. Fix it, then retry." },
    cancelled: { title: "Cancelled", sub: "The job was stopped before it finished." },
  }[status] || { title: status, sub: "" };

  async function start() {
    try {
      setJob(await api<Job>(`/api/v1/projects/${project.id}/jobs`, { method: "POST", body: JSON.stringify({ job_type: "analysis" }) }));
      await reloadJobs();
    } catch (reason) {
      notify(message(reason, "Could not start the analysis."), "error");
    }
  }
  async function act(id: string, action: "pause" | "resume" | "cancel" | "retry") {
    try {
      setJob(await api<Job>(`/api/v1/jobs/${id}/${action}`, { method: "POST" }));
      if (action === "retry") await reloadJobs();
    } catch (reason) {
      notify(message(reason, "Could not update the job."), "error");
    }
  }

  const stepStatus = new Map((job?.steps || []).map((item) => [item.name, item]));
  const stepNames = job?.steps.length ? job.steps.map((item) => item.name) : STEP_ORDER;
  const finished = (job?.steps || []).filter((item) => item.status === "completed" || item.status === "skipped" || item.status === "warning").length;
  const currentName = job?.current_step && ACTIVE.includes(status) ? job.current_step : "";

  return (
    <>
      <section className="glass card">
        <div className="row-between" style={{ alignItems: "flex-start" }}>
          <div className="card-head"><h2>{view.title}</h2><span>{view.sub}</span></div>
          {job ? <StatusBadge status={status} /> : <Badge tone="gray">Not started</Badge>}
        </div>
        <div className="summary-grid">
          <div><span>Sources</span><strong>{(project.sources || []).map((item) => KIND_LABEL[item.kind] || item.kind).join(" + ") || "None"}</strong></div>
          <div><span>Output</span><strong>{s.doc_types.length} doc types · {s.output_formats.length} formats{s.enable_video ? " · video" : ""}</strong></div>
          <div><span>AI</span><strong>{provider ? `${provider.display_name || provider.provider} connected` : "Not connected — observed facts only"}</strong></div>
        </div>

        {job && (
          <>
            <div className="stack">
              <div className="row-between" style={{ fontSize: 14 }}>
                <span style={{ color: "var(--color-text-muted)" }}>
                  {done ? `${finished} of ${stepNames.length} steps finished` : currentName ? `Step ${stepNames.indexOf(currentName) + 1} of ${stepNames.length} · ${STEP_LABEL[currentName] || currentName}` : `Job ${job.id.slice(0, 8)}`}
                </span>
                <span style={{ fontWeight: 800, color: "var(--color-primary)" }}>{job.progress}%</span>
              </div>
              <div className="progress-track" role="progressbar" aria-valuenow={job.progress} aria-valuemin={0} aria-valuemax={100}>
                <div className="progress-fill" style={{ width: `${job.progress}%` }} />
              </div>
            </div>
            <div className="pipeline">
              {stepNames.map((name, i) => {
                const st = stepStatus.get(name);
                const cls = st?.status === "failed" ? "bad" : st && ["completed", "skipped", "warning"].includes(st.status) ? "done" : name === currentName ? "on" : "";
                return (
                  <div key={name} className={`pipe-step ${cls}`} title={st?.detail || undefined}>
                    <span className="pipe-mark">{cls === "done" ? "✓" : cls === "bad" ? "!" : i + 1}</span>
                    {STEP_LABEL[name] || name}
                  </div>
                );
              })}
            </div>
            {job.error_detail && <div className={`note ${failed ? "red" : "amber"}`}>{job.error_detail}</div>}
          </>
        )}

        <div className="row-between">
          <button type="button" className="btn-secondary" onClick={back}>Back</button>
          <div className="row">
            {job?.actions.pause && <button type="button" className="btn-secondary" onClick={() => act(job.id, "pause")}>Pause</button>}
            {job?.actions.resume && <button type="button" className="btn-secondary" onClick={() => act(job.id, "resume")}>Resume</button>}
            {job?.actions.cancel && <button type="button" className="btn-secondary" onClick={() => act(job.id, "cancel")}>Cancel</button>}
            {job?.actions.retry && <button type="button" className="btn-secondary" onClick={() => act(job.id, "retry")}>Retry</button>}
            {!active && <button type="button" className="btn-primary" onClick={start}>{job ? "Run again" : "Start analysis"}</button>}
            {done && <button type="button" className="btn-primary" onClick={goResults}>View results</button>}
          </div>
        </div>
      </section>

      {jobs.length > 1 && (
        <section className="stack">
          <span className="muted" style={{ fontWeight: 700, padding: "0 6px" }}>Previous runs</span>
          {jobs.slice(1, 8).map((item) => (
            <div className="history-row" key={item.id}>
              <code style={{ fontSize: 13, width: 84 }}>{item.id.slice(0, 8)}</code>
              <span style={{ flex: 1, minWidth: 150 }}>{formatDate(item.started_at)}</span>
              <span className="muted" style={{ width: 90, fontSize: 14 }}>{item.job_type === "rescan" ? "Rescan" : "Manual"}</span>
              <span className="muted" style={{ width: 60, fontSize: 14 }}>{item.steps.filter((x) => x.status === "completed").length} / {item.steps.length || STEP_ORDER.length}</span>
              <StatusBadge status={item.status} />
              {item.actions.retry && <button type="button" className="btn-link" onClick={() => act(item.id, "retry")}>Retry</button>}
            </div>
          ))}
        </section>
      )}
    </>
  );
}
