"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "../../lib/api";
import type { Notify, Project, Requirement } from "./types";
import { StatusBadge, formatDate, message, statusTone } from "./ui";

type Run = { id: string; code_revision: string; created_at: string; matches_current_revision: boolean; verified_requirements?: number; total_requirements?: number };

function refsText(refs: unknown): string {
  if (!refs) return "—";
  if (Array.isArray(refs)) return refs.map((item) => (typeof item === "string" ? item : JSON.stringify(item))).join(", ") || "—";
  if (typeof refs === "string") return refs;
  return JSON.stringify(refs);
}

function blockerText(blocker: unknown): string {
  if (typeof blocker === "string") return blocker;
  if (blocker && typeof blocker === "object") {
    const b = blocker as Record<string, unknown>;
    return String(b.reason || b.detail || b.description || b.summary || JSON.stringify(b));
  }
  return String(blocker);
}

export function Requirements({ project, notify }: { project: Project | null; notify: Notify }) {
  const [scope, setScope] = useState<"platform" | "project">("platform");
  const [rows, setRows] = useState<Requirement[]>([]);
  const [runs, setRuns] = useState<{ note?: string; runs: Run[] }>({ runs: [] });
  const [filter, setFilter] = useState("All");
  const [open, setOpen] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const query = scope === "project" && project ? `scope=project&project_id=${project.id}` : "scope=platform";
    setLoading(true);
    api<{ requirements: Requirement[] }>(`/api/v1/requirements?${query}`)
      .then((body) => setRows(body.requirements))
      .catch((reason) => notify(message(reason, "Could not load requirements."), "error"))
      .finally(() => setLoading(false));
  }, [scope, project, notify]);

  useEffect(() => {
    api<{ note?: string; runs: Run[] }>("/api/v1/verification-runs").then(setRuns).catch(() => undefined);
  }, []);

  const statuses = useMemo(() => ["All", ...Array.from(new Set(rows.map((row) => row.status)))], [rows]);
  const visible = rows.filter((row) => filter === "All" || row.status === filter);
  const verified = rows.filter((row) => row.status === "verified").length;
  const open_items = rows.filter((row) => Array.isArray(row.blockers) && row.blockers.length > 0);
  const latestRun = runs.runs[0];

  const banner = !rows.length
    ? { text: "No requirements are stored for this scope yet.", cls: "note gray" }
    : verified === rows.length
      ? { text: `All ${rows.length} requirements are verified with recorded evidence.`, cls: "note green" }
      : { text: `${verified} of ${rows.length} requirements verified in this database. ${open_items.length ? `${open_items.length} have open blockers.` : ""} Release readiness depends on every mandatory criterion passing.`, cls: "note amber" };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Requirements</h1>
          <span className="sub">A requirement is Verified only when real tests ran and left evidence. It cannot be ticked by hand.</span>
        </div>
        <div className="segmented small">
          <button type="button" aria-selected={scope === "platform"} onClick={() => { setScope("platform"); setFilter("All"); }}>Platform</button>
          <button type="button" aria-selected={scope === "project"} disabled={!project} onClick={() => { setScope("project"); setFilter("All"); }}>This project</button>
        </div>
      </div>

      <div className={`banner ${banner.cls}`}>{banner.text}</div>
      {latestRun && (
        <span className="muted">
          Latest imported test run: {formatDate(latestRun.created_at)} · revision {latestRun.code_revision || "unknown"} · {latestRun.matches_current_revision ? "matches the running code" : "does not match the running code"}. {runs.note}
        </span>
      )}

      <div className="row" style={{ gap: 6 }}>
        {statuses.map((item) => (
          <button key={item} type="button" className="filter-chip" aria-pressed={filter === item} onClick={() => setFilter(item)}>
            {item === "All" ? "All" : statusTone(item).label} · {item === "All" ? rows.length : rows.filter((row) => row.status === item).length}
          </button>
        ))}
      </div>

      <div className="glass req-list">
        {loading && <p className="muted" style={{ padding: 14 }}>Loading…</p>}
        {!loading && !visible.length && <p className="muted" style={{ padding: 14 }}>No requirements match this filter.</p>}
        {visible.map((row) => {
          const isOpen = open === row.id;
          const passed = new Set(row.test_executions.filter((item) => item.result === "passed").map((item) => item.criterion_id));
          return (
            <div className="req" key={row.id} data-open={isOpen}>
              <button type="button" className="req-head" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : row.id)}>
                <code>{row.key}</code>
                <strong>{row.title}</strong>
                <StatusBadge status={row.status} />
                <span className="req-caret">{isOpen ? "–" : "+"}</span>
              </button>
              {isOpen && (
                <div className="req-body">
                  <div>
                    <h4>Acceptance</h4>
                    <span>{row.description}</span>
                    <ul>
                      {row.acceptance_criteria.map((item) => (
                        <li key={item.id}>
                          <StatusBadge status={item.status} small /> {item.description}
                          {item.mandatory ? "" : " (optional)"}
                          {passed.has(item.id) ? " · test passed" : ""}
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <h4>Code</h4>
                    <code style={{ fontSize: 12, overflowWrap: "anywhere" }}>{refsText(row.implementation_refs)}</code>
                    <h4 style={{ marginTop: 6 }}>Priority</h4>
                    <span>{row.priority || "—"} · {row.category || "—"}</span>
                  </div>
                  <div>
                    <h4>Evidence</h4>
                    {row.evidence.length ? (
                      <ul>{row.evidence.map((item) => <li key={item.id}>{item.kind}: {item.summary}</li>)}</ul>
                    ) : (
                      <span>No evidence recorded yet.</span>
                    )}
                    {row.test_executions.length > 0 && (
                      <span className="muted">Last test: {formatDate(row.test_executions.at(-1)?.executed_at)} · {row.test_executions.at(-1)?.environment}</span>
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {open_items.length > 0 && (
        <div className="glass panel" style={{ gap: 4 }}>
          <h2 style={{ fontSize: 17 }}>Still open before release</h2>
          <span className="muted" style={{ marginBottom: 8 }}>Requirements with recorded blockers. These stay open even when every catalog test passes.</span>
          {open_items.map((row) => (
            <div className="gap-row" key={row.id}>
              <code>{row.key}</code>
              <StatusBadge status={row.status} />
              <span style={{ color: "var(--color-text-muted)" }}>{row.blockers.map(blockerText).join(" · ")}</span>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
