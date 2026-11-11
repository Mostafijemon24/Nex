"use client";

import { FormEvent, useEffect, useState } from "react";
import { api } from "../../lib/api";
import type { Destination, Monitor, Notify, Project } from "./types";
import { Badge, StatusBadge, formatDate, message } from "./ui";

const INTERVALS: [number, string][] = [[60, "Every hour"], [360, "Every 6 hours"], [720, "Every 12 hours"], [1440, "Once a day"]];

export function Settings({ project, isAdmin, notify }: { project: Project | null; isAdmin: boolean; notify: Notify }) {
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <span className="sub">WordPress publishing, automatic rescans, storage and team.</span>
        </div>
      </div>
      {!isAdmin && <div className="note amber">Some settings need an organization admin. You can view them, but saving may be refused.</div>}
      <div className="grid-auto" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))" }}>
        <WordPress notify={notify} />
        <div className="stack" style={{ gap: 18 }}>
          <Rescan project={project} notify={notify} />
          <Storage notify={notify} />
          <Users notify={notify} />
        </div>
      </div>
    </>
  );
}

function WordPress({ notify }: { notify: Notify }) {
  const [destinations, setDestinations] = useState<Destination[]>([]);
  const [busy, setBusy] = useState(false);
  const current = destinations.find((item) => item.status === "connected") || destinations.at(-1);

  async function load() {
    const body = await api<{ destinations: Destination[] }>("/api/v1/publishing/destinations");
    setDestinations(body.destinations);
  }
  useEffect(() => {
    load().catch(() => undefined);
  }, []);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setBusy(true);
    try {
      const saved = await api<Destination>("/api/v1/publishing/destinations", { method: "POST", body: JSON.stringify(Object.fromEntries(form)) });
      const tested = await api<Destination>(`/api/v1/publishing/destinations/${saved.id}/test`, { method: "POST" });
      notify(tested.status === "connected" ? "WordPress replied — drafts can be created." : `${tested.status}: ${tested.status_detail}`, tested.status === "connected" ? "ok" : "error");
      formElement.reset();
      await load();
    } catch (reason) {
      notify(message(reason, "WordPress connection failed."), "error");
    } finally {
      setBusy(false);
    }
  }

  async function retest() {
    if (!current) return;
    setBusy(true);
    try {
      const tested = await api<Destination>(`/api/v1/publishing/destinations/${current.id}/test`, { method: "POST" });
      notify(`${tested.status}: ${tested.status_detail}`, tested.status === "connected" ? "ok" : "error");
      await load();
    } catch (reason) {
      notify(message(reason, "Test failed."), "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="glass panel" style={{ padding: 26, gap: 14 }} onSubmit={save}>
      <div className="row-between">
        <h2>WordPress</h2>
        {current ? <StatusBadge status={current.status} /> : <Badge tone="gray">Not connected</Badge>}
      </div>
      {current && (
        <div className="note gray">
          {current.site_url} · {current.username}
          <br />
          {current.status_detail}
        </div>
      )}
      <label className="field">Site URL<input name="site_url" type="url" required placeholder="https://yoursite.com" /></label>
      <label className="field">Username<input name="username" required placeholder="editor" autoComplete="off" /></label>
      <label className="field">Application password<input name="application_password" type="password" required placeholder="xxxx xxxx xxxx xxxx" autoComplete="new-password" style={{ fontFamily: "var(--font-mono)" }} /></label>
      <button type="submit" className="btn-primary" style={{ minHeight: 46 }} disabled={busy}>{busy ? "Testing…" : current ? "Save new connection and test" : "Save and test connection"}</button>
      {current && <button type="button" className="btn-secondary" style={{ minHeight: 46 }} disabled={busy} onClick={retest}>Test saved connection</button>}
      <span className="muted">Posts are sent as drafts only, and counted only after WordPress confirms them. The same document is never sent twice to one site.</span>
    </form>
  );
}

function Rescan({ project, notify }: { project: Project | null; notify: Notify }) {
  const [monitor, setMonitor] = useState<Monitor | null>(null);
  const projectId = project?.id || "";

  useEffect(() => {
    if (!projectId) {
      setMonitor(null);
      return;
    }
    api<Monitor>(`/api/v1/projects/${projectId}/monitor`).then(setMonitor).catch(() => setMonitor(null));
  }, [projectId]);

  async function save(next: { enabled: boolean; interval_minutes: number }) {
    if (!projectId) return;
    try {
      setMonitor(await api<Monitor>(`/api/v1/projects/${projectId}/monitor`, { method: "PUT", body: JSON.stringify(next) }));
    } catch (reason) {
      notify(message(reason, "Could not save the schedule."), "error");
    }
  }

  const text = !project
    ? "Select or create a project first."
    : !monitor?.enabled
      ? "Off. Only manual runs will happen."
      : `On · next rescan ${formatDate(monitor.next_run_at)}${monitor.last_status ? ` · last: ${monitor.last_status}` : ""}`;

  return (
    <div className="glass panel" style={{ padding: 26 }}>
      <h2>Automatic rescan</h2>
      <label className="check" style={{ fontWeight: 600 }}>
        <input type="checkbox" disabled={!project || !monitor} checked={Boolean(monitor?.enabled)} onChange={(event) => save({ enabled: event.target.checked, interval_minutes: monitor?.interval_minutes || 1440 })} />
        Rescan {project ? `“${project.name}”` : "this project"} automatically
      </label>
      {monitor?.enabled && (
        <label className="field">How often
          <select value={monitor.interval_minutes} onChange={(event) => save({ enabled: true, interval_minutes: Number(event.target.value) })}>
            {INTERVALS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            {!INTERVALS.some(([value]) => value === monitor.interval_minutes) && <option value={monitor.interval_minutes}>Every {monitor.interval_minutes} minutes</option>}
          </select>
        </label>
      )}
      <span className="muted">{text}</span>
      {monitor?.last_error && <div className="note red">{monitor.last_error}</div>}
    </div>
  );
}

function Storage({ notify }: { notify: Notify }) {
  const [backend, setBackend] = useState("");
  useEffect(() => {
    api<{ backend: string }>("/api/v1/system/storage").then((body) => setBackend(body.backend)).catch(() => undefined);
  }, []);
  return (
    <div className="glass panel" style={{ padding: 26 }}>
      <div className="row-between"><h2>Storage</h2><Badge tone="gray">{backend || "unknown"}</Badge></div>
      <span className="muted">Screenshots, documents and videos are kept here.</span>
      <button
        type="button"
        className="btn-outline"
        onClick={() => api("/api/v1/system/storage/test", { method: "POST" }).then(() => notify("Storage probe wrote and read a file.")).catch((reason) => notify(message(reason, "Storage test failed."), "error"))}
      >
        Test storage
      </button>
    </div>
  );
}

function Users({ notify }: { notify: Notify }) {
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    try {
      await api("/api/v1/users", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(formElement))) });
      notify("User created.");
      formElement.reset();
    } catch (reason) {
      notify(message(reason, "Could not create the user."), "error");
    }
  }
  return (
    <details className="glass panel" style={{ padding: 26 }}>
      <summary style={{ cursor: "pointer", fontWeight: 800, fontSize: 18 }}>Add a team member</summary>
      <form className="stack" style={{ gap: 12, marginTop: 14 }} onSubmit={submit}>
        <label className="field">Email<input name="email" type="email" required /></label>
        <label className="field">Password<input name="password" type="password" minLength={12} required autoComplete="new-password" /></label>
        <label className="field">Name<input name="display_name" required /></label>
        <label className="field">Role<select name="role"><option value="member">Member</option><option value="admin">Admin</option></select></label>
        <button type="submit" className="btn-primary" style={{ minHeight: 46 }}>Create user</button>
      </form>
    </details>
  );
}
