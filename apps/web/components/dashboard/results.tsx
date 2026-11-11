"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { api, apiText, apiUrl } from "../../lib/api";
import type { ChangeRecord, Destination, DocumentRow, Notify, Page, Project, Provider, Screenshot, Video } from "./types";
import { Badge, StatusBadge, Tone, formatDate, host, message, shortPath } from "./ui";

type Tab = "docs" | "shots" | "video" | "seo" | "changes";
const TABS: [Tab, string][] = [["docs", "Documents"], ["shots", "Screenshots"], ["video", "Video"], ["seo", "SEO article"], ["changes", "Changes"]];

type Data = { pages: Page[]; documents: DocumentRow[]; screenshots: Screenshot[]; videos: Video[]; changes: ChangeRecord[] };

export function Results({ project, providers, notify, goAnalyze }: { project: Project | null; providers: Provider[]; notify: Notify; goAnalyze: () => void }) {
  const [tab, setTab] = useState<Tab>("docs");
  const [data, setData] = useState<Data | null>(null);
  const [loading, setLoading] = useState(false);
  const projectId = project?.id || "";

  const load = useCallback(async () => {
    if (!projectId) {
      setData(null);
      return;
    }
    setLoading(true);
    try {
      const [pages, documents, screenshots, videos, changes] = await Promise.all([
        api<{ pages: Page[] }>(`/api/v1/projects/${projectId}/pages`),
        api<{ documents: DocumentRow[] }>(`/api/v1/projects/${projectId}/documents`),
        api<{ screenshots: Screenshot[] }>(`/api/v1/projects/${projectId}/screenshots`),
        api<{ videos: Video[] }>(`/api/v1/projects/${projectId}/videos`),
        api<{ changes: ChangeRecord[] }>(`/api/v1/projects/${projectId}/changes`),
      ]);
      setData({ pages: pages.pages, documents: documents.documents, screenshots: screenshots.screenshots, videos: videos.videos, changes: changes.changes });
    } catch (reason) {
      notify(message(reason, "Could not load results."), "error");
    } finally {
      setLoading(false);
    }
  }, [projectId, notify]);

  useEffect(() => {
    load();
  }, [load]);

  const hasAny = Boolean(data && (data.pages.length || data.documents.length || data.screenshots.length || data.videos.length));
  const site = data?.pages[0] ? host(data.pages[0].url) : project?.name || "";

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Results</h1>
          <span className="sub">{hasAny ? `${site} · ${data?.pages.length || 0} pages observed` : loading ? "Loading…" : "No analysis has finished yet"}</span>
        </div>
        <div className="segmented small" role="tablist">
          {TABS.map(([key, label]) => (
            <button key={key} type="button" role="tab" aria-selected={tab === key} onClick={() => setTab(key)}>{label}</button>
          ))}
        </div>
      </div>

      {!hasAny && !loading && tab !== "seo" && (
        <div className="glass empty">
          <strong>Nothing here yet</strong>
          <span>Results appear after an analysis finishes. Nex never shows output that was not actually produced.</span>
          <button type="button" className="btn-primary" onClick={goAnalyze}>Start an analysis</button>
        </div>
      )}

      {data && hasAny && tab === "docs" && <Documents documents={data.documents.filter((doc) => doc.doc_type !== "seo_article")} site={site} notify={notify} />}
      {data && hasAny && tab === "shots" && <Shots data={data} />}
      {data && hasAny && tab === "video" && <Videos videos={data.videos} />}
      {tab === "seo" && project && <Seo projectId={project.id} providers={providers} notify={notify} onCreated={load} />}
      {tab === "seo" && !project && <div className="glass empty"><strong>No project selected</strong><span>Create a project on the Analyze screen first.</span></div>}
      {data && hasAny && tab === "changes" && <Changes changes={data.changes} />}
    </>
  );
}

/* ---------- Documents ---------- */

type Section = { heading: string; factType: string; body: string };
type Parsed = { title: string; status: string; sections: Section[] };

/** Parse the Markdown that apps/api/app/documentation/render.py writes (title, status, sections with a fact type). */
function parseDoc(text: string): Parsed {
  const lines = text.split("\n");
  const parsed: Parsed = { title: "", status: "", sections: [] };
  let current: Section | null = null;
  for (const line of lines) {
    if (line.startsWith("# ") && !parsed.title) parsed.title = line.slice(2).trim();
    else if (line.startsWith("Verification status: ") && !current) parsed.status = line.slice(21).trim();
    else if (line.startsWith("## ")) {
      current = { heading: line.slice(3).trim(), factType: "", body: "" };
      parsed.sections.push(current);
    } else if (current && line.startsWith("Fact type: ") && !current.factType) current.factType = line.slice(11).trim();
    else if (current) current.body += `${line}\n`;
  }
  parsed.sections.forEach((item) => { item.body = item.body.trim(); });
  return parsed;
}

function factTag(factType: string): { tone: Tone; label: string } {
  const t = factType.toLowerCase();
  if (t === "observed") return { tone: "green", label: "Observed" };
  if (t === "source_code") return { tone: "green", label: "Source code" };
  if (t === "blocked") return { tone: "red", label: "Blocked — not observed" };
  if (t.includes("ai") || t.includes("infer")) return { tone: "amber", label: "AI inference · not verified" };
  if (t.includes("template")) return { tone: "amber", label: "Template text" };
  return { tone: "gray", label: factType || "Unlabeled" };
}

function Documents({ documents, site, notify }: { documents: DocumentRow[]; site: string; notify: Notify }) {
  const [selected, setSelected] = useState(0);
  const [doc, setDoc] = useState<Parsed | null>(null);
  const [loading, setLoading] = useState(false);
  const current = documents[selected];
  const latestRun = current?.versions.at(-1)?.run_id;
  const latest = current ? current.versions.filter((version) => version.run_id === latestRun) : [];
  const markdown = latest.find((version) => version.format === "markdown");

  useEffect(() => {
    if (!markdown) {
      setDoc(null);
      return;
    }
    setLoading(true);
    apiText(`/api/v1/document-versions/${markdown.id}/file`)
      .then((text) => setDoc(parseDoc(text)))
      .catch((reason) => { setDoc(null); notify(message(reason, "Could not open the document."), "error"); })
      .finally(() => setLoading(false));
  }, [markdown?.id, notify]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!documents.length) {
    return <div className="glass empty"><strong>No documents yet</strong><span>The last run did not write any documents. Check the run notes on the Analyze screen.</span></div>;
  }

  const runs = (row: DocumentRow) => new Set(row.versions.map((version) => version.run_id)).size;

  return (
    <div className="docs-layout">
      <div className="stack" style={{ gap: 6 }} role="listbox" aria-label="Documents">
        {documents.map((row, i) => (
          <button key={row.id} type="button" role="option" className="doc-pick" aria-selected={i === selected} onClick={() => setSelected(i)}>
            <div className="row-between" style={{ gap: 8 }}><strong>{row.title}</strong><span className="muted" style={{ fontSize: 11, fontWeight: 700 }}>v{runs(row)}</span></div>
            <span>{row.doc_type.replace(/_/g, " ")} · {formatDate(row.versions.at(-1)?.created_at)}</span>
          </button>
        ))}
      </div>
      <article className="glass article">
        <div className="article-bar">
          <span className="muted">Download</span>
          <div className="row" style={{ gap: 6 }}>
            {latest.map((version) => (
              <a key={version.id} className="btn-sm" href={apiUrl(`/api/v1/document-versions/${version.id}/file`)} target="_blank" rel="noreferrer">{version.format.toUpperCase()}</a>
            ))}
          </div>
        </div>
        <div className="article-body">
          <h2>{doc?.title || current?.title}</h2>
          <div className="row" style={{ gap: 8 }}>
            <span className="muted" style={{ fontSize: 14 }}>{site} · {formatDate(latest[0]?.created_at)}</span>
            {doc?.status && <StatusBadge status={doc.status} small />}
          </div>
          {loading && <p className="muted">Loading document…</p>}
          {!loading && !markdown && <p className="muted">No Markdown version was produced, so a preview is not available. Use a download above.</p>}
          {doc?.sections.map((item, i) => {
            const tag = factTag(item.factType);
            return (
              <div className="block" key={`${item.heading}-${i}`}>
                <h3>{item.heading}</h3>
                <p>{item.body}</p>
                <div className="row" style={{ gap: 8 }}><Badge tone={tag.tone} small>{tag.label}</Badge></div>
              </div>
            );
          })}
        </div>
      </article>
    </div>
  );
}

/* ---------- Screenshots ---------- */

function Shots({ data }: { data: Data }) {
  const [onlyNew, setOnlyNew] = useState(false);
  const [open, setOpen] = useState<Screenshot | null>(null);
  const [compare, setCompare] = useState(false);

  const pageById = useMemo(() => new Map(data.pages.map((page) => [page.id, page])), [data.pages]);
  const latestChange = data.changes.at(-1);
  const added = new Set(latestChange?.summary.pages_added || []);
  const latestRun = data.pages.at(-1)?.run_id;
  const runOf = (shot: Screenshot) => (shot.page_id ? pageById.get(shot.page_id)?.run_id : undefined);
  const titleOf = (shot: Screenshot) => (shot.page_id ? pageById.get(shot.page_id)?.title : "") || shortPath(shot.url);

  // Show the latest run's captures; fall back to everything if run info is missing.
  const latest = data.screenshots.filter((shot) => !latestRun || runOf(shot) === latestRun);
  const pool = latest.length ? latest : data.screenshots;
  const shots = onlyNew ? pool.filter((shot) => added.has(shot.url)) : pool;
  const previous = open ? data.screenshots.filter((shot) => shot.url === open.url && shot.id !== open.id && runOf(shot) !== runOf(open)).at(-1) : undefined;

  if (!data.screenshots.length) {
    return <div className="glass empty"><strong>No screenshots</strong><span>No pages were captured in the browser. Add a website source and run the analysis again.</span></div>;
  }

  return (
    <>
      <div className="row">
        <span className="muted" style={{ fontSize: 14 }}>{pool.length} screenshots · lossless PNG</span>
        <label className="check" style={{ marginLeft: "auto", fontWeight: 600 }}>
          <input type="checkbox" checked={onlyNew} onChange={() => setOnlyNew(!onlyNew)} disabled={!latestChange} />
          New since last run
        </label>
      </div>
      {!shots.length && <p className="muted">No new pages in the latest comparison.</p>}
      <div className="shot-grid">
        {shots.map((shot) => (
          <button key={shot.id} type="button" className="shot" onClick={() => { setOpen(shot); setCompare(false); }}>
            <div className="shot-img">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={apiUrl(`/api/v1/screenshots/${shot.id}/file`)} alt={`Screenshot of ${shot.url}`} loading="lazy" />
              {added.has(shot.url) && <span className="shot-flag">New</span>}
            </div>
            <div className="shot-meta"><strong>{titleOf(shot)}</strong><span>{shortPath(shot.url)} · {shot.kind}</span></div>
          </button>
        ))}
      </div>

      {open && (
        <div className="lightbox" onClick={() => setOpen(null)} role="dialog" aria-modal="true" aria-label={titleOf(open)}>
          <div className="lightbox-box" onClick={(event) => event.stopPropagation()}>
            <div className="lightbox-bar">
              <div className="stack" style={{ gap: 0, minWidth: 0 }}>
                <strong>{titleOf(open)}</strong>
                <code className="muted" style={{ overflowWrap: "anywhere" }}>{open.url}</code>
              </div>
              <div className="row" style={{ gap: 8 }}>
                <button type="button" className="btn-sm" disabled={!previous} title={previous ? "" : "No earlier capture of this page"} style={{ background: compare ? "var(--brand-bg)" : "#fff" }} onClick={() => setCompare(!compare)}>Compare with last run</button>
                <a className="btn-primary" style={{ minHeight: 42, padding: "9px 14px", borderRadius: 10, display: "inline-flex", alignItems: "center" }} href={apiUrl(`/api/v1/screenshots/${open.id}/file`)} download>Download PNG</a>
                <button type="button" className="btn-sm" style={{ background: "#F1F5F9", border: 0 }} onClick={() => setOpen(null)}>Close</button>
              </div>
            </div>
            <div className="lightbox-grid" style={{ gridTemplateColumns: compare && previous ? "1fr 1fr" : "1fr" }}>
              {compare && previous && (
                <div className="stack">
                  <span className="muted" style={{ fontWeight: 700 }}>Previous run</span>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={apiUrl(`/api/v1/screenshots/${previous.id}/file`)} alt="Previous capture" />
                </div>
              )}
              <div className="stack">
                <span className="muted" style={{ fontWeight: 700 }}>Latest run · sha256 {open.sha256.slice(0, 10)}…</span>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={apiUrl(`/api/v1/screenshots/${open.id}/file`)} alt="Latest capture" />
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

/* ---------- Video ---------- */

function Videos({ videos }: { videos: Video[] }) {
  const video = [...videos].reverse().find((item) => item.status.startsWith("completed")) || videos.at(-1);
  if (!video) {
    return <div className="glass empty"><strong>No video</strong><span>Turn on “Render an MP4 walkthrough” in the Configure step, then run again.</span></div>;
  }
  const playable = video.status.startsWith("completed");
  const duration = video.duration_seconds ? `${Math.floor(video.duration_seconds / 60)}:${String(Math.round(video.duration_seconds % 60)).padStart(2, "0")}` : "—";
  return (
    <div className="video-layout">
      <div className="video-frame">
        {playable ? <video controls preload="metadata" src={apiUrl(`/api/v1/videos/${video.id}/file`)} /> : <span>Video not rendered · {video.status}</span>}
      </div>
      <div className="glass panel" style={{ fontSize: 14 }}>
        <h2>Website walkthrough</h2>
        <span className="note amber">Screenshot slideshow — not a live browser recording</span>
        <div className="kv"><span>Status</span><span><StatusBadge status={video.status} small /></span></div>
        <div className="kv"><span>Duration</span><span>{duration}</span></div>
        <div className="kv"><span>Format</span><span>MP4 (ffmpeg)</span></div>
        {video.limitations.map((item) => <div key={item} className="note gray">{item}</div>)}
        {playable && <a className="btn-primary" style={{ textAlign: "center", marginTop: "auto" }} href={apiUrl(`/api/v1/videos/${video.id}/file`)} download>Download MP4</a>}
      </div>
    </div>
  );
}

/* ---------- SEO ---------- */

type Article = {
  generation_mode: string;
  provider: string;
  model: string;
  fallback_reason?: string;
  title: string;
  seo_title: string;
  meta_description: string;
  slug: string;
  headings: string[];
  body: string;
};

function Seo({ projectId, providers, notify, onCreated }: { projectId: string; providers: Provider[]; notify: Notify; onCreated: () => Promise<void> }) {
  const connected = providers.filter((item) => item.status === "connected");
  const [article, setArticle] = useState<Article | null>(null);
  const [versionId, setVersionId] = useState("");
  const [busy, setBusy] = useState(false);
  const [destinations, setDestinations] = useState<Destination[]>([]);

  useEffect(() => {
    api<{ destinations: Destination[] }>("/api/v1/publishing/destinations").then((body) => setDestinations(body.destinations)).catch(() => undefined);
  }, []);

  async function generate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    try {
      const saved = await api<{ article: Article; version_id: string }>(`/api/v1/projects/${projectId}/seo-articles`, {
        method: "POST",
        body: JSON.stringify({
          primary_keyword: form.get("primary_keyword"),
          topic: form.get("topic"),
          content_brief: form.get("content_brief") || "",
          secondary_keywords: String(form.get("secondary_keywords") || "").split(",").map((item) => item.trim()).filter(Boolean),
          allow_template: form.get("allow_template") === "on",
          provider_id: form.get("provider_id") || "",
        }),
      });
      setArticle(saved.article);
      setVersionId(saved.version_id);
      notify(saved.article.generation_mode === "ai" ? `Draft written by ${saved.article.provider}.` : "Template draft created — not AI-written.");
      await onCreated();
    } catch (reason) {
      notify(message(reason, "Could not create the article."), "error");
    } finally {
      setBusy(false);
    }
  }

  async function publish() {
    const destination = destinations.find((item) => item.status === "connected");
    if (!destination) {
      notify("Connect and test WordPress in Settings first.", "error");
      return;
    }
    if (!versionId) return;
    try {
      const result = await api<{ status: string; remote_id: string; remote_url: string }>("/api/v1/publishing/posts", {
        method: "POST",
        body: JSON.stringify({ destination_id: destination.id, document_version_id: versionId, status: "draft" }),
      });
      notify(`WordPress confirmed a ${result.status} (ID ${result.remote_id}).`);
    } catch (reason) {
      notify(message(reason, "WordPress did not accept the draft."), "error");
    }
  }

  const isAi = article?.generation_mode === "ai";

  return (
    <div className="seo-layout">
      <form className="glass panel" style={{ gap: 14 }} onSubmit={generate}>
        <label className="field">Focus keyword<input name="primary_keyword" required maxLength={120} /></label>
        <label className="field">Topic<input name="topic" required maxLength={180} /></label>
        <label className="field">Secondary keywords<input name="secondary_keywords" placeholder="comma separated" /></label>
        <label className="field">Brief<textarea name="content_brief" /></label>
        <label className="field">AI provider
          <select name="provider_id" defaultValue={connected[0]?.id || ""}>
            <option value="">Auto — first connected provider</option>
            {connected.map((item) => <option key={item.id} value={item.id}>{item.display_name || item.provider} · {item.model || "auto"}</option>)}
          </select>
        </label>
        <label className="check" style={{ fontSize: 13 }}><input name="allow_template" type="checkbox" defaultChecked={!connected.length} />Allow labeled template output if no AI is used</label>
        <button type="submit" className="btn-primary" style={{ minHeight: 46 }} disabled={busy}>{busy ? "Writing…" : "Generate draft"}</button>
        <button type="button" className="btn-secondary" style={{ minHeight: 46 }} disabled={!versionId} onClick={publish}>Send to WordPress as draft</button>
      </form>
      <article className="glass article" style={{ padding: "32px 40px", gap: 16 }}>
        {!article ? (
          <div className="empty" style={{ padding: 24 }}>
            <strong>No draft yet</strong>
            <span>Drafts are built only from pages recorded in this project. Without a connected AI provider, the output is clearly labeled as template text.</span>
          </div>
        ) : (
          <>
            <span style={{ alignSelf: "flex-start" }}>
              {isAi ? <Badge tone="brand">AI-generated · {article.provider}{article.model ? ` · ${article.model}` : ""}</Badge> : <Badge tone="amber">Template output — not AI-generated</Badge>}
            </span>
            <div className="meta-box">
              <span className="muted" style={{ fontWeight: 600, fontSize: 12 }}>Meta title · {article.seo_title.length} characters</span>
              <span style={{ color: "var(--brand-fg)", fontWeight: 700, fontSize: 17 }}>{article.seo_title}</span>
              <span className="muted" style={{ fontWeight: 600, fontSize: 12, marginTop: 8 }}>Meta description · {article.meta_description.length} characters</span>
              <span style={{ fontSize: 14, color: "#334155" }}>{article.meta_description}</span>
              <span className="muted" style={{ fontSize: 12, marginTop: 8 }}>Slug: <code>{article.slug}</code></span>
            </div>
            <h2 style={{ fontSize: 24, fontWeight: 800 }}>{article.title}</h2>
            <p style={{ fontSize: 17, lineHeight: 1.7, color: "#334155", whiteSpace: "pre-wrap", maxWidth: 640 }}>{article.body}</p>
            {article.fallback_reason && !isAi && <span className="muted">{article.fallback_reason}</span>}
          </>
        )}
      </article>
    </div>
  );
}

/* ---------- Changes ---------- */

function Changes({ changes }: { changes: ChangeRecord[] }) {
  const latest = changes.at(-1);
  if (!latest) {
    return <div className="glass empty"><strong>No comparison yet</strong><span>The first run is the baseline. Changes appear after the next run of this project.</span></div>;
  }
  const s = latest.summary;
  const pages: [string, string, Tone][] = [
    ...(s.pages_added || []).map((url): [string, string, Tone] => ["New", url, "green"]),
    ...(s.pages_removed || []).map((url): [string, string, Tone] => ["Removed", url, "red"]),
  ];
  const files: [string, string, Tone][] = [
    ...(s.changed_files || []).map((path): [string, string, Tone] => ["Modified", path, "amber"]),
    ...(s.added_files || []).map((path): [string, string, Tone] => ["Added", path, "green"]),
    ...(s.removed_files || []).map((path): [string, string, Tone] => ["Removed", path, "red"]),
  ];
  const kpis: [number, string][] = [
    [s.pages_added?.length || 0, "New pages"],
    [s.pages_removed?.length || 0, "Removed pages"],
    [s.changed_files?.length || 0, "Modified files"],
    [(s.added_files?.length || 0) + (s.removed_files?.length || 0), "Added or removed files"],
  ];
  return (
    <>
      <div className="kpis">
        {kpis.map(([n, label]) => <div key={label} className="glass kpi"><strong>{n}</strong><span>{label}</span></div>)}
      </div>
      <div className="grid-auto">
        <div className="glass panel" style={{ gap: 6 }}>
          <h2 style={{ fontSize: 17, marginBottom: 8 }}>Pages · previous run → latest</h2>
          {pages.map(([kind, url, tone]) => <div className="change-row" key={kind + url}><Badge tone={tone} small>{kind}</Badge><code>{shortPath(url)}</code></div>)}
          {!pages.length && <span className="muted">No pages were added or removed.</span>}
        </div>
        <div className="glass panel" style={{ gap: 6 }}>
          <h2 style={{ fontSize: 17, marginBottom: 8 }}>Source files · compared by SHA-256</h2>
          {files.map(([kind, path, tone]) => <div className="change-row" key={kind + path}><Badge tone={tone} small>{kind}</Badge><code>{path}</code></div>)}
          {!files.length && <span className="muted">No source files changed.</span>}
        </div>
      </div>
      {changes.length > 1 && <span className="muted">{changes.length} comparisons stored for this project. Showing the latest.</span>}
    </>
  );
}
