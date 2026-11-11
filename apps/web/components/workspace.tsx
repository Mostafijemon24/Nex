"use client";

import { FormEvent, useEffect, useState } from "react";
import { api } from "../lib/api";

type Session = { user: { email: string; role: string; display_name: string }; organization: { name: string } };
type Project = { id: string; name: string; description: string; settings: Record<string, unknown>; sources?: Source[] };
type Source = { id: string; kind: string; name: string; status: string; status_detail: string; config: { accounts?: { username: string; role: string }[] }; has_secret: boolean };
type Job = {
  id: string;
  status: string;
  progress: number;
  current_step: string;
  error_detail: string;
  retry_count: number;
  actions: Record<string, boolean>;
  steps: { name: string; status: string; detail: string }[];
};
type DiscoveredModel = { id: string; name: string; billing: string; chat?: boolean };
type Provider = {
  id: string;
  provider: string;
  display_name: string;
  model: string;
  status: string;
  status_detail: string;
  error_category: string;
  api_key_hint: string;
  endpoint: string;
  capabilities: Record<string, unknown>;
  free_models?: DiscoveredModel[];
  free_model_count?: number;
  discovered_models?: DiscoveredModel[];
};

const TABS = ["Inputs", "AI", "Configure", "Run", "Results", "Requirements", "Settings"] as const;

export function Workspace() {
  const [session, setSession] = useState<Session | null>(null);
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [tab, setTab] = useState<(typeof TABS)[number]>("Inputs");
  const [detail, setDetail] = useState<Project | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [results, setResults] = useState<Record<string, unknown>>({});
  const [requirements, setRequirements] = useState<Record<string, unknown>[]>([]);
  const [scope, setScope] = useState("platform");
  const [navOpen, setNavOpen] = useState(false);

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

  async function refreshProjects(selected = projectId) {
    const body = await api<{ projects: Project[] }>("/api/v1/projects");
    setProjects(body.projects);
    const next = body.projects.find((item) => item.id === selected)?.id || body.projects[0]?.id || "";
    setProjectId(next);
    if (next) setDetail(await api<Project>(`/api/v1/projects/${next}`));
  }

  useEffect(() => {
    refreshSession().catch((reason: Error) => setError(reason.message));
  }, []);

  useEffect(() => {
    if (!session) return;
    refreshProjects().catch((reason: Error) => setError(reason.message));
    api<{ providers: Provider[] }>("/api/v1/ai-providers").then((body) => setProviders(body.providers)).catch(() => undefined);
  }, [session]);

  useEffect(() => {
    if (!job || !["queued", "running", "retrying"].includes(job.status)) return;
    const timer = setInterval(() => {
      api<Job>(`/api/v1/jobs/${job.id}`).then(setJob).catch((reason: Error) => setError(reason.message));
    }, 2000);
    return () => clearInterval(timer);
  }, [job]);

  async function onAuth(event: FormEvent<HTMLFormElement>, path: string) {
    event.preventDefault();
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      await api(path, { method: "POST", body: JSON.stringify(Object.fromEntries(form)) });
      await refreshSession();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Request failed.");
    }
  }

  if (needsSetup === null) {
    return (
      <main className="shell auth-shell">
        {error ? (
          <>
            <h1>API not connected</h1>
            <p className="error">{error}</p>
            <p className="muted">This Vercel site is only the frontend. Deploy the API (Postgres, Redis, workers), then set <code>NEXT_PUBLIC_API_URL</code> on the nex project to that public API origin and redeploy.</p>
          </>
        ) : (
          <p>Checking setup…</p>
        )}
      </main>
    );
  }
  if (!session) {
    return (
      <main className="shell auth-shell">
        <h1>{needsSetup ? "Create the administrator" : "Sign in"}</h1>
        {error && <p className="error">{error}</p>}
        <form className="panel" onSubmit={(event) => onAuth(event, needsSetup ? "/api/v1/setup" : "/api/v1/auth/login")}>
          <label>Email<input name="email" type="email" autoComplete="username" required /></label>
          <label>Password<input name="password" type="password" autoComplete={needsSetup ? "new-password" : "current-password"} minLength={needsSetup ? 12 : 1} required /></label>
          {needsSetup && (
            <>
              <label>Name<input name="display_name" required /></label>
              <label>Organization<input name="organization_name" required /></label>
            </>
          )}
          <button className="primary" type="submit">{needsSetup ? "Create administrator" : "Sign in"}</button>
        </form>
      </main>
    );
  }

  const project = detail;

  return (
    <main className="shell">
      <header className="top">
        <div className="identity">
          <h1 className="brand">Documentation platform</h1>
          <div className="muted">{session.organization.name} · {session.user.email}</div>
        </div>
        <div className="top-actions">
          <label className="project-field">
            Project
            <select value={projectId} onChange={(event) => {
              setProjectId(event.target.value);
              api<Project>(`/api/v1/projects/${event.target.value}`).then(setDetail).catch((reason: Error) => setError(reason.message));
            }}>
              {projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          <button className="secondary sign-out" type="button" onClick={() => api("/api/v1/auth/logout", { method: "POST" }).then(() => setSession(null))}>Sign out</button>
        </div>
      </header>
      {error && <p className="error">{error}</p>}
      {notice && <p className="ok">{notice}</p>}
      <section className="panel project-create" aria-labelledby="create-project-heading">
        <h2 id="create-project-heading">Create a project</h2>
        <form className="project-form" onSubmit={async (event) => {
          event.preventDefault();
          const formElement = event.currentTarget;
          const form = new FormData(formElement);
          try {
            await api("/api/v1/projects", { method: "POST", body: JSON.stringify({ name: form.get("name"), description: form.get("description") || "" }) });
            formElement.reset();
            await refreshProjects();
          } catch (reason) {
            setError(reason instanceof Error ? reason.message : "Could not create the project.");
          }
        }}>
          <label>
            Project name
            <input name="name" required autoComplete="off" />
          </label>
          <label>
            Description
            <input name="description" autoComplete="off" />
          </label>
          <div className="form-actions">
            <button className="primary" type="submit">Create project</button>
          </div>
        </form>
      </section>
      <div className="nav-bar">
        <button className="menu secondary" type="button" aria-expanded={navOpen} aria-controls="workspace-nav" onClick={() => setNavOpen((open) => !open)}>{navOpen ? "Close menu" : "Menu"}</button>
        <nav id="workspace-nav" className="tabs" data-open={navOpen} aria-label="Workspace">
          {TABS.map((item) => (
            <button key={item} type="button" aria-selected={tab === item} onClick={() => { setTab(item); setNavOpen(false); }}>{item}</button>
          ))}
        </nav>
      </div>
      <div className="workspace-body">
      {project && tab === "Inputs" && <Inputs project={project} onChange={async () => setDetail(await api<Project>(`/api/v1/projects/${project.id}`))} onError={setError} />}
      {project && tab === "AI" && <AI providers={providers} onSaved={async () => setProviders((await api<{ providers: Provider[] }>("/api/v1/ai-providers")).providers)} onError={setError} onNotice={setNotice} />}
      {project && tab === "Configure" && <Configure project={project} providers={providers} onSaved={async () => setDetail(await api<Project>(`/api/v1/projects/${project.id}`))} onError={setError} />}
      {project && tab === "Run" && <Run projectId={project.id} job={job} setJob={setJob} onError={setError} />}
      {project && tab === "Results" && <Results projectId={project.id} providers={providers} results={results} setResults={setResults} onError={setError} />}
      {tab === "Requirements" && <Requirements projectId={projectId} scope={scope} setScope={setScope} rows={requirements} setRows={setRequirements} onError={setError} />}
      {tab === "Settings" && <Settings onError={setError} onNotice={setNotice} />}
      </div>
    </main>
  );
}

function Inputs({ project, onChange, onError }: { project: Project; onChange: () => Promise<void>; onError: (value: string) => void }) {
  const [kind, setKind] = useState("website");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
      if (["website", "github", "git"].includes(kind)) {
        const extraName = String(form.get("extra_name") || "");
        const extraValue = String(form.get("extra_value") || "");
        await api(`/api/v1/projects/${project.id}/sources`, {
          method: "POST",
          body: JSON.stringify({
            kind,
            name: form.get("name"),
            password: form.get("password") || "",
            config: {
              url: form.get("url") || "",
              login_url: form.get("login_url") || "",
              username: form.get("username") || "",
              role: form.get("role") || "default",
              extra_fields: extraName ? { [extraName]: extraValue } : {},
            },
          }),
        });
      } else {
        await api(`/api/v1/projects/${project.id}/sources/upload`, { method: "POST", body: form });
      }
      formElement.reset();
      await onChange();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Could not save the source.");
    }
  }
  const sources = project.sources || [];
  return (
    <section className="panel" aria-labelledby="add-source-heading">
      <h2 id="add-source-heading">Add a source</h2>
      <form onSubmit={submit}>
        <div className="grid">
          <label>Source type
            <select name="kind" value={kind} onChange={(event) => setKind(event.target.value)}>
              <option value="website">Website</option>
              <option value="github">GitHub repository</option>
              <option value="git">Git repository</option>
              <option value="zip">Source ZIP</option>
              <option value="wp_plugin">WordPress plugin ZIP</option>
              <option value="wp_theme">WordPress theme ZIP</option>
              <option value="documentation">Existing documentation</option>
              <option value="api_spec">API specification</option>
              <option value="recording">Workflow recording</option>
            </select>
          </label>
          <label>Name<input name="name" required /></label>
          {["website", "github", "git"].includes(kind) && <label className="span-2">URL<input name="url" type="url" required /></label>}
          {kind === "website" && (
            <>
              <label>Login URL<input name="login_url" type="url" /></label>
              <label>Username or email<input name="username" autoComplete="off" /></label>
              <label>Password<input name="password" type="password" autoComplete="new-password" /></label>
              <label>Role<input name="role" defaultValue="default" /></label>
              <label>Additional field name<input name="extra_name" /></label>
              <label>Additional field value<input name="extra_value" type="password" autoComplete="off" /></label>
            </>
          )}
          {!["website", "github", "git"].includes(kind) && <label className="span-2">File<input name="upload" type="file" required /></label>}
        </div>
        <div className="form-actions">
          <button className="primary" type="submit">Add source</button>
        </div>
      </form>
      <div className="record-list">
        <h2>Sources</h2>
        {sources.length === 0 && <p className="muted">No sources yet. Add a website, repository, or file above.</p>}
        <div className="list">
        {sources.map((source) => (
          <article className="item" key={source.id}>
            <strong>{source.name}</strong> <span className="muted">{source.kind}</span>
            <div className={`status ${source.status}`}>{source.status}</div>
            <p>{source.status_detail || "Not tested yet."}</p>
            <div className="actions">
              <button type="button" onClick={() => api(`/api/v1/sources/${source.id}/test`, { method: "POST" }).then(onChange).catch((reason: Error) => onError(reason.message))}>Test access</button>
              <button className="danger" type="button" onClick={() => api(`/api/v1/sources/${source.id}`, { method: "DELETE" }).then(onChange).catch((reason: Error) => onError(reason.message))}>Remove</button>
            </div>
            {source.kind === "website" && (
              <>
                <ul>{(source.config.accounts || []).map((account, index) => <li key={`${account.username}-${index}`}>{account.username} · {account.role} <button className="danger" type="button" onClick={() => api(`/api/v1/sources/${source.id}/accounts/${index}`, { method: "DELETE" }).then(onChange).catch((reason: Error) => onError(reason.message))}>Remove account</button></li>)}</ul>
                <form onSubmit={async (event) => {
                  event.preventDefault();
                  const formElement = event.currentTarget;
                  const form = new FormData(formElement);
                  try {
                    await api(`/api/v1/sources/${source.id}/accounts`, { method: "POST", body: JSON.stringify(Object.fromEntries(form)) });
                    formElement.reset();
                    await onChange();
                  } catch (reason) {
                    onError(reason instanceof Error ? reason.message : "Could not add the account.");
                  }
                }}>
                  <label>Account username<input name="username" required /></label>
                  <label>Account password<input name="password" type="password" autoComplete="new-password" /></label>
                  <label>Account role<input name="role" defaultValue="default" /></label>
                  <div className="form-actions">
                    <button type="submit">Add account</button>
                  </div>
                </form>
              </>
            )}
          </article>
        ))}
        </div>
      </div>
    </section>
  );
}

function AI({ providers, onSaved, onError, onNotice }: { providers: Provider[]; onSaved: () => Promise<void>; onError: (value: string) => void; onNotice: (value: string) => void }) {
  const [provider, setProvider] = useState("openrouter");
  const [models, setModels] = useState<DiscoveredModel[]>([]);
  const managed = provider !== "openai_compatible";
  const needsEndpoint = provider === "openai_compatible";
  const keyPlaceholder = provider === "oxion" ? "cc_…" : provider === "gemini" ? "Google AI Studio key" : provider === "openrouter" ? "sk-or-…" : undefined;
  const freeFromList = models.filter((model) => model.billing === "free" && model.chat !== false);
  return (
    <section className="panel ai-panel">
      <h2 className="ai-heading">AI provider</h2>
      <p className="muted">Keys stay on the server. For OpenRouter, paste an <code>sk-or-…</code> key and click Connect — the official endpoint is set automatically. OpenRouter defaults to the Free Models Router (<code>openrouter/free</code>), which routes across every free chat model. You can also pick any discovered free model; generation falls back only to other free models so requests stay zero-cost. A model is labeled free only when pricing fields are zero.</p>
      <form onSubmit={async (event) => {
        event.preventDefault();
        const formElement = event.currentTarget;
        const form = new FormData(formElement);
        const selected = String(form.get("provider") || provider);
        try {
          if (selected !== "openai_compatible") {
            const connected = await api<Provider & { models?: DiscoveredModel[]; connected?: boolean }>("/api/v1/ai-providers/connect", {
              method: "POST",
              body: JSON.stringify({
                provider: selected,
                display_name: form.get("display_name") || "",
                api_key: form.get("api_key"),
                fallback_provider_id: form.get("fallback_provider_id") || "",
                allow_paid_fallback: form.get("allow_paid_fallback") === "on",
              }),
            });
            setModels(connected.models || connected.discovered_models || []);
            if (connected.connected) {
              const freeNote = connected.free_model_count ? ` ${connected.free_model_count} free chat model(s) available.` : "";
              onNotice(`Connected. ${connected.status_detail}${connected.model ? ` Model: ${connected.model}.` : ""}${freeNote}`);
              formElement.reset();
              setProvider(selected);
            } else {
              onError(`${connected.status}: ${connected.status_detail}${connected.error_category ? ` (${connected.error_category})` : ""}`);
            }
            await onSaved();
            return;
          }
          const saved = await api<Provider>("/api/v1/ai-providers", { method: "POST", body: JSON.stringify({
            provider: selected,
            display_name: form.get("display_name") || "",
            model: form.get("model") || "",
            api_key: form.get("api_key"),
            endpoint: form.get("endpoint") || "",
            fallback_provider_id: form.get("fallback_provider_id") || "",
            allow_paid_fallback: form.get("allow_paid_fallback") === "on",
          }) });
          onNotice(`Saved. Status: ${saved.status}. ${saved.status_detail}`);
          formElement.reset();
          setProvider(selected);
          await onSaved();
        } catch (reason) {
          onError(reason instanceof Error ? reason.message : "Could not connect the provider.");
        }
      }}>
        <div className="grid">
          <label>Provider
            <select name="provider" value={provider} onChange={(event) => setProvider(event.target.value)}>
              <option value="openrouter">OpenRouter (all free models)</option>
              <option value="gemini">Google Gemini</option>
              <option value="oxion">Oxion AI</option>
              <option value="openai">OpenAI</option>
              <option value="openai_compatible">OpenAI-compatible (custom endpoint)</option>
              <option value="anthropic">Anthropic</option>
            </select>
          </label>
          <label>Display name<input name="display_name" placeholder="Optional" autoComplete="off" /></label>
          {!managed && <label>Model ID<input name="model" placeholder="Type a model ID if discovery is unavailable" autoComplete="off" /></label>}
          <label className="span-2">API key<input name="api_key" type="password" required autoComplete="off" placeholder={keyPlaceholder} /></label>
          {needsEndpoint && <label className="span-2">API endpoint<input name="endpoint" type="url" required placeholder="https://example.com/v1" /></label>}
          <label className="span-2"><input name="allow_paid_fallback" type="checkbox" /> Allow fallback to another saved provider after a temporary failure</label>
          <label>Fallback provider ID<input name="fallback_provider_id" placeholder="Only used when fallback is allowed" /></label>
        </div>
        <div className="form-actions">
          <button className="ai" type="submit">{managed ? "Connect" : "Save provider"}</button>
        </div>
      </form>
      <div className="record-list">
        <h2>Saved providers</h2>
        {providers.length === 0 && <p className="muted">No providers saved yet.</p>}
        <div className="list">
        {providers.map((item) => {
          const freeModels = (item.free_models && item.free_models.length > 0)
            ? item.free_models
            : (item.discovered_models || []).filter((model) => model.billing === "free" && model.chat !== false);
          const freeOptions = [...freeModels];
          if (item.model && !freeOptions.some((model) => model.id === item.model)) {
            freeOptions.unshift({ id: item.model, name: item.model, billing: "free", chat: true });
          }
          return (
          <article className="item" key={item.id}>
            <strong>{item.display_name || item.provider}</strong> <span className="muted">{item.provider}</span> <span className={`status ${item.status}`}>{item.status}</span>
            <p>{item.status_detail}</p>
            {item.error_category && <p className="muted">Error category: {item.error_category}</p>}
            <p className="muted">Key {item.api_key_hint || "not shown"} {item.model && `· ${item.model}`}{typeof item.free_model_count === "number" && item.free_model_count > 0 ? ` · ${item.free_model_count} free` : ""}</p>
            {item.provider === "openrouter" && item.status === "connected" && freeOptions.length > 0 && (
              <label className="span-2">
                Free OpenRouter model
                <select
                  value={item.model || "openrouter/free"}
                  onChange={(event) => {
                    const model = event.target.value;
                    api<Provider>(`/api/v1/ai-providers/${item.id}/model`, {
                      method: "POST",
                      body: JSON.stringify({ model }),
                    }).then(async (result) => {
                      onNotice(result.status_detail || `Selected ${result.model}.`);
                      await onSaved();
                    }).catch((reason: Error) => onError(reason.message));
                  }}
                >
                  {freeOptions.map((model) => (
                    <option key={model.id} value={model.id}>
                      {model.id === "openrouter/free" ? "Free Models Router (all free)" : `${model.name || model.id}`}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <div className="actions">
              {item.status === "failed" ? (
                <p className="muted">Failed entries keep the old key. Remove this row, paste a fresh API key above, and click Connect.</p>
              ) : (
                <>
                  <button type="button" onClick={() => api<Provider>(`/api/v1/ai-providers/${item.id}/test`, { method: "POST" }).then(async (result) => {
                    onNotice(`${result.status}: ${result.status_detail}`);
                    await onSaved();
                  }).catch((reason: Error) => onError(reason.message))}>Test connection</button>
                  <button type="button" onClick={() => api<{ message: string; models: DiscoveredModel[]; free_models?: DiscoveredModel[] }>(`/api/v1/ai-providers/${item.id}/models`, { method: "POST" }).then(async (result) => {
                    setModels(result.free_models?.length ? result.free_models : result.models || []);
                    onNotice(result.message);
                    await onSaved();
                  }).catch((reason: Error) => onError(reason.message))}>Discover models</button>
                </>
              )}
              <button className="danger" type="button" onClick={() => api(`/api/v1/ai-providers/${item.id}`, { method: "DELETE" }).then(onSaved).catch((reason: Error) => onError(reason.message))}>Remove</button>
            </div>
          </article>
          );
        })}
        </div>
      </div>
      {models.length > 0 && (
        <div className="model-catalog">
          <h2>Discovered models</h2>
          <p className="muted">{freeFromList.length > 0 ? `${freeFromList.length} free chat model(s) shown first.` : "Recent discovery results."}</p>
          <ul>
            {(freeFromList.length > 0 ? freeFromList : models).map((model) => (
              <li key={model.id}>{model.id} · {model.billing}{model.chat === false ? " · non-chat" : ""}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function Configure({ project, providers, onSaved, onError }: { project: Project; providers: Provider[]; onSaved: () => Promise<void>; onError: (value: string) => void }) {
  const settings = project.settings;
  return (
    <section className="panel">
      <form onSubmit={async (event) => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        const next = {
          ...settings,
          analysis_mode: form.get("analysis_mode"),
          documentation_language: form.get("documentation_language"),
          target_audience: form.get("target_audience"),
          safety_level: form.get("safety_level"),
          max_pages: Number(form.get("max_pages")),
          max_depth: Number(form.get("max_depth")),
          max_seconds: Number(form.get("max_seconds")),
          enable_video: form.get("enable_video") === "on",
          enable_voiceover: form.get("enable_voiceover") === "on",
          use_ai_documentation: form.get("use_ai_documentation") === "on",
          ai_provider_id: form.get("ai_provider_id") || "",
          tts_provider: form.get("tts_provider") || "",
          tts_model: form.get("tts_model") || "tts-1",
          tts_voice: form.get("tts_voice") || "alloy",
          doc_types: form.getAll("doc_types"),
          output_formats: form.getAll("output_formats"),
          allowed_domains: String(form.get("allowed_domains") || "").split(",").map((item) => item.trim()).filter(Boolean),
        };
        try {
          await api(`/api/v1/projects/${project.id}/settings`, { method: "PUT", body: JSON.stringify({ settings: next }) });
          await onSaved();
        } catch (reason) {
          onError(reason instanceof Error ? reason.message : "Could not save settings.");
        }
      }}>
        <div className="grid">
          <label>Analysis mode
            <select name="analysis_mode" defaultValue={String(settings.analysis_mode)}>
              <option value="standard">Website and code</option>
              <option value="website_only">Website only</option>
              <option value="code_only">Code only</option>
            </select>
          </label>
          <label>Safety level
            <select name="safety_level" defaultValue={String(settings.safety_level)}>
              <option value="safe">Safe — no destructive clicks</option>
              <option value="recorded">Recorded — still no destructive clicks</option>
            </select>
          </label>
          <label>Language<input name="documentation_language" defaultValue={String(settings.documentation_language)} /></label>
          <label>Audience<input name="target_audience" defaultValue={String(settings.target_audience)} /></label>
          <label className="span-2">Allowed domains, comma separated<input name="allowed_domains" defaultValue={(settings.allowed_domains as string[]).join(", ")} /></label>
        </div>
        <fieldset>
          <legend>Documentation</legend>
          {["getting_started", "feature_guide", "technical", "user_manual", "tutorial", "architecture", "api_reference", "database", "installation", "configuration", "troubleshooting", "faq", "developer", "wordpress_plugin", "wordpress_theme", "integration", "release_notes"].map((item) => (
            <label key={item}><input type="checkbox" name="doc_types" value={item} defaultChecked={(settings.doc_types as string[]).includes(item)} /> {item}</label>
          ))}
        </fieldset>
        <fieldset>
          <legend>Formats</legend>
          {["markdown", "html", "pdf", "docx"].map((item) => (
            <label key={item}><input type="checkbox" name="output_formats" value={item} defaultChecked={(settings.output_formats as string[]).includes(item)} /> {item}</label>
          ))}
        </fieldset>
        <details>
          <summary>Limits and video</summary>
          <label>Maximum pages<input name="max_pages" type="number" min={1} max={30} defaultValue={Number(settings.max_pages)} /></label>
          <label>Maximum depth<input name="max_depth" type="number" min={0} max={3} defaultValue={Number(settings.max_depth)} /></label>
          <label>Maximum seconds<input name="max_seconds" type="number" min={10} max={180} defaultValue={Number(settings.max_seconds)} /></label>
          <label><input name="enable_video" type="checkbox" defaultChecked={Boolean(settings.enable_video)} /> Render a video from screenshots</label>
          <label><input name="enable_voiceover" type="checkbox" defaultChecked={Boolean(settings.enable_voiceover)} /> Request voiceover</label>
          <label>Speech provider
            <select name="tts_provider" defaultValue={String(settings.tts_provider || "")}>
              <option value="">None — silent video</option>
              <option value="openai">OpenAI speech</option>
              <option value="openai_compatible">OpenAI-compatible speech</option>
            </select>
          </label>
          <label>Speech model<input name="tts_model" defaultValue={String(settings.tts_model || "tts-1")} /></label>
          <label>Voice<input name="tts_voice" defaultValue={String(settings.tts_voice || "alloy")} /></label>
          <label>AI provider for this project
            <select name="ai_provider_id" defaultValue={String(settings.ai_provider_id || "")}>
              <option value="">None</option>
              {providers.filter((item) => item.status === "connected").map((item) => (
                <option key={item.id} value={item.id}>{item.display_name || item.provider} · {item.model || "auto"}</option>
              ))}
            </select>
          </label>
          <label><input name="use_ai_documentation" type="checkbox" defaultChecked={Boolean(settings.use_ai_documentation)} /> Record the selected provider on documentation runs</label>
          <p className="muted">Without a speech provider, a requested voiceover stays silent and is not marked as narrated. Slideshows are not browser interaction recordings.</p>
        </details>
        <div className="form-actions">
          <button className="primary" type="submit">Save configuration</button>
        </div>
      </form>
      <form className="section-break" onSubmit={async (event) => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        try {
          await api(`/api/v1/projects/${project.id}/monitor`, { method: "PUT", body: JSON.stringify({ enabled: form.get("enabled") === "on", interval_minutes: Number(form.get("interval_minutes") || 60) }) });
        } catch (reason) {
          onError(reason instanceof Error ? reason.message : "Could not save the schedule.");
        }
      }}>
        <h2>Scheduled rescan</h2>
        <label><input name="enabled" type="checkbox" /> Enable</label>
        <label>Interval minutes<input name="interval_minutes" type="number" min={15} max={1440} defaultValue={60} /></label>
        <div className="form-actions">
          <button type="submit">Save schedule</button>
        </div>
      </form>
    </section>
  );
}

function Run({ projectId, job, setJob, onError }: { projectId: string; job: Job | null; setJob: (job: Job) => void; onError: (value: string) => void }) {
  async function start() {
    try {
      setJob(await api<Job>(`/api/v1/projects/${projectId}/jobs`, { method: "POST", body: JSON.stringify({ job_type: "analysis" }) }));
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Could not start analysis.");
    }
  }
  async function act(action: string) {
    if (!job) return;
    try {
      setJob(await api<Job>(`/api/v1/jobs/${job.id}/${action}`, { method: "POST" }));
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Could not update the job.");
    }
  }
  return (
    <section className="panel">
      <div className="actions">
        <button className="primary" type="button" onClick={start}>Start analysis</button>
        {job?.actions.pause && <button type="button" onClick={() => act("pause")}>Pause</button>}
        {job?.actions.resume && <button type="button" onClick={() => act("resume")}>Resume</button>}
        {job?.actions.cancel && <button type="button" onClick={() => act("cancel")}>Cancel</button>}
        {job?.actions.retry && <button type="button" onClick={() => act("retry")}>Retry failed job</button>}
      </div>
      {job && (
        <>
          <p><span className={`status ${job.status}`}>{job.status}</span> · step {job.current_step || "not started"} · retry {job.retry_count}</p>
          <progress value={job.progress} max={100} />
          {job.error_detail && <p className="error">{job.error_detail}</p>}
          <ul>{job.steps.map((step) => <li key={step.name}><span className={`status ${step.status}`}>{step.status}</span> {step.name} {step.detail && `— ${step.detail}`}</li>)}</ul>
        </>
      )}
    </section>
  );
}

function Results({ projectId, providers, results, setResults, onError }: { projectId: string; providers: Provider[]; results: Record<string, unknown>; setResults: (value: Record<string, unknown>) => void; onError: (value: string) => void }) {
  const [article, setArticle] = useState<Record<string, unknown> | null>(null);
  useEffect(() => {
    Promise.all([
      api(`/api/v1/projects/${projectId}/pages`),
      api(`/api/v1/projects/${projectId}/features`),
      api(`/api/v1/projects/${projectId}/documents`),
      api(`/api/v1/projects/${projectId}/screenshots`),
      api(`/api/v1/projects/${projectId}/videos`),
      api(`/api/v1/projects/${projectId}/changes`),
    ]).then(([pages, features, documents, screenshots, videos, changes]) => {
      setResults({ pages, features, documents, screenshots, videos, changes });
    }).catch((reason: Error) => onError(reason.message));
  }, [projectId]);
  return (
    <section className="panel">
      <form onSubmit={async (event) => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        try {
          const saved = await api<{ article: Record<string, unknown> }>(`/api/v1/projects/${projectId}/seo-articles`, {
            method: "POST",
            body: JSON.stringify({
              primary_keyword: form.get("primary_keyword"),
              topic: form.get("topic"),
              content_brief: form.get("content_brief"),
              search_intent: form.get("search_intent"),
              secondary_keywords: String(form.get("secondary_keywords") || "").split(",").map((item) => item.trim()).filter(Boolean),
              allow_template: form.get("allow_template") === "on",
              provider_id: form.get("provider_id") || "",
            }),
          });
          setArticle(saved.article);
        } catch (reason) {
          onError(reason instanceof Error ? reason.message : "Could not create the article.");
        }
      }}>
        <h2 className="ai-heading">SEO article</h2>
        <div className="grid">
          <label>Primary keyword<input name="primary_keyword" required /></label>
          <label>Topic<input name="topic" required /></label>
          <label>Search intent<input name="search_intent" defaultValue="informational" /></label>
          <label>AI provider
            <select name="provider_id" defaultValue={providers.find((item) => item.status === "connected")?.id || ""}>
              <option value="">Auto — first connected provider</option>
              {providers.filter((item) => item.status === "connected").map((item) => (
                <option key={item.id} value={item.id}>{item.display_name || item.provider} · {item.model || "auto"}</option>
              ))}
            </select>
          </label>
          <label className="span-2">Brief<textarea name="content_brief" /></label>
          <label className="span-2">Secondary keywords<input name="secondary_keywords" placeholder="comma separated" /></label>
          <label><input name="allow_template" type="checkbox" /> Allow labeled template output</label>
        </div>
        <div className="form-actions">
          <button type="submit">Create article</button>
        </div>
      </form>
      {article && <pre className="preview">{JSON.stringify(article, null, 2)}</pre>}
      <pre className="preview">{JSON.stringify(results, null, 2)}</pre>
    </section>
  );
}

function Requirements({ projectId, scope, setScope, rows, setRows, onError }: { projectId: string; scope: string; setScope: (value: string) => void; rows: Record<string, unknown>[]; setRows: (rows: Record<string, unknown>[]) => void; onError: (value: string) => void }) {
  const [runs, setRuns] = useState<{ note?: string; runs: Record<string, unknown>[] }>({ runs: [] });
  useEffect(() => {
    const query = scope === "project" ? `scope=project&project_id=${projectId}` : "scope=platform";
    api<{ requirements: Record<string, unknown>[] }>(`/api/v1/requirements?${query}`).then((body) => setRows(body.requirements)).catch((reason: Error) => onError(reason.message));
    api<{ note?: string; runs: Record<string, unknown>[] }>("/api/v1/verification-runs").then(setRuns).catch((reason: Error) => onError(reason.message));
  }, [scope, projectId]);
  return (
    <section className="panel">
      <p className="muted">{runs.note || "Requirement status comes from this database. A test report is separate evidence."}</p>
      {runs.runs[0] && <p>Latest imported run: {String(runs.runs[0].database_name || "unknown database")} · revision {String(runs.runs[0].code_revision || "unknown")} · current match {String(runs.runs[0].matches_current_revision)}</p>}
      <div className="actions">
        <button type="button" aria-selected={scope === "platform"} onClick={() => setScope("platform")}>Platform</button>
        <button type="button" aria-selected={scope === "project"} onClick={() => setScope("project")}>This project</button>
      </div>
      <div className="record-list">
      {rows.length === 0 && <p className="muted">No requirements are stored for this scope.</p>}
      <div className="list">
        {rows.map((row) => (
          <article className="item" key={String(row.id)}>
            <strong>{String(row.key)}</strong> {String(row.title)}
            <div className={`status ${String(row.status)}`}>{String(row.status)}</div>
            <p>{String(row.description)}</p>
            <ul>
              {(row.acceptance_criteria as { key: string; description: string; status: string }[]).map((item) => (
                <li key={item.key}><span className={`status ${item.status}`}>{item.status}</span> {item.description}</li>
              ))}
            </ul>
            {Array.isArray(row.blockers) && row.blockers.length > 0 && <p>Blockers: {JSON.stringify(row.blockers)}</p>}
          </article>
        ))}
      </div>
      </div>
    </section>
  );
}

function Settings({ onError, onNotice }: { onError: (value: string) => void; onNotice: (value: string) => void }) {
  const [storage, setStorage] = useState("");
  useEffect(() => {
    api<{ backend: string }>("/api/v1/system/storage").then((body) => setStorage(body.backend)).catch((reason: Error) => onError(reason.message));
  }, []);
  return (
    <section className="panel">
      <p>Storage backend: <strong>{storage || "unknown"}</strong></p>
      <div className="form-actions">
        <button type="button" onClick={() => api("/api/v1/system/storage/test", { method: "POST" }).then(() => onNotice("Storage probe wrote and read a file.")).catch((reason: Error) => onError(reason.message))}>Test storage</button>
      </div>
      <form className="section-break" onSubmit={async (event) => {
        event.preventDefault();
        const formElement = event.currentTarget;
        const form = new FormData(formElement);
        try {
          await api("/api/v1/users", { method: "POST", body: JSON.stringify(Object.fromEntries(form)) });
          onNotice("User created.");
          formElement.reset();
        } catch (reason) {
          onError(reason instanceof Error ? reason.message : "Could not create the user.");
        }
      }}>
        <h2>Add a user</h2>
        <label>Email<input name="email" type="email" required /></label>
        <label>Password<input name="password" type="password" minLength={12} required /></label>
        <label>Name<input name="display_name" required /></label>
        <label>Role
          <select name="role"><option value="member">Member</option><option value="admin">Admin</option></select>
        </label>
        <div className="form-actions">
          <button className="primary" type="submit">Create user</button>
        </div>
      </form>
      <form className="section-break" onSubmit={async (event) => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        try {
          const saved = await api<{ id: string }>("/api/v1/publishing/destinations", { method: "POST", body: JSON.stringify(Object.fromEntries(form)) });
          const tested = await api<{ status: string; status_detail: string }>(`/api/v1/publishing/destinations/${saved.id}/test`, { method: "POST" });
          onNotice(`${tested.status}: ${tested.status_detail}`);
        } catch (reason) {
          onError(reason instanceof Error ? reason.message : "WordPress connection failed.");
        }
      }}>
        <h2>WordPress</h2>
        <label>Site URL<input name="site_url" type="url" required /></label>
        <label>Username<input name="username" required /></label>
        <label>Application password<input name="application_password" type="password" required /></label>
        <div className="form-actions">
          <button type="submit">Save and test</button>
        </div>
      </form>
    </section>
  );
}
