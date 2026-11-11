"use client";

import { useState } from "react";
import { api } from "../../lib/api";
import type { DiscoveredModel, Notify, Provider } from "./types";
import { Badge, StatusBadge, message } from "./ui";

const PROVIDERS: { id: string; name: string; initial: string; desc: string; placeholder: string }[] = [
  { id: "openrouter", name: "OpenRouter", initial: "O", desc: "Free and paid models", placeholder: "sk-or-…" },
  { id: "gemini", name: "Google Gemini", initial: "G", desc: "Gemini API", placeholder: "Google AI Studio key" },
  { id: "oxion", name: "Oxion AI", initial: "X", desc: "API key only", placeholder: "cc_…" },
  { id: "openai", name: "OpenAI", initial: "A", desc: "Chat completions", placeholder: "sk-…" },
  { id: "anthropic", name: "Anthropic", initial: "C", desc: "Messages API", placeholder: "sk-ant-…" },
  { id: "openai_compatible", name: "Custom endpoint", initial: "+", desc: "OpenAI-compatible URL", placeholder: "API key" },
];

type Msg = { ok: boolean; text: string };

export function ApiProviders({ providers, reload, notify }: { providers: Provider[]; reload: () => Promise<void>; notify: Notify }) {
  const [paid, setPaid] = useState(false);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>API</h1>
          <span className="sub">Connect an AI provider with your own key. Optional — without one, Nex writes from observed facts only.</span>
        </div>
      </div>
      <div className="glass row" style={{ gap: 16, padding: "18px 22px", borderRadius: 18 }}>
        <div style={{ flex: 1, minWidth: 240, display: "flex", flexDirection: "column", gap: 2 }}>
          <strong>Free models first</strong>
          <span className="muted">Nex never switches to a paid model unless you allow it here. This applies to keys you connect after changing it.</span>
        </div>
        <label className="check" style={{ fontWeight: 600 }}><input type="checkbox" checked={paid} onChange={() => setPaid(!paid)} />Allow paid fallback</label>
      </div>
      <div className="grid-auto" style={{ gap: 16 }}>
        {PROVIDERS.map((meta) => (
          <ProviderCard key={meta.id} meta={meta} saved={providers.filter((item) => item.provider === meta.id)} paid={paid} reload={reload} notify={notify} />
        ))}
      </div>
    </>
  );
}

function ProviderCard({ meta, saved, paid, reload, notify }: {
  meta: (typeof PROVIDERS)[number];
  saved: Provider[];
  paid: boolean;
  reload: () => Promise<void>;
  notify: Notify;
}) {
  const row = saved.find((item) => item.status === "connected") || saved[0];
  const [key, setKey] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg | null>(null);
  const custom = meta.id === "openai_compatible";

  async function connect() {
    if (!key.trim()) {
      setMsg({ ok: false, text: "Add an API key first. Nothing was sent." });
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      if (custom) {
        const result = await api<Provider>("/api/v1/ai-providers", {
          method: "POST",
          body: JSON.stringify({ provider: meta.id, display_name: "", model, api_key: key, endpoint, fallback_provider_id: "", allow_paid_fallback: paid }),
        });
        setMsg({ ok: result.status === "connected", text: `${result.status}: ${result.status_detail}` });
      } else {
        const result = await api<Provider & { connected?: boolean }>("/api/v1/ai-providers/connect", {
          method: "POST",
          body: JSON.stringify({ provider: meta.id, display_name: "", api_key: key, fallback_provider_id: "", allow_paid_fallback: paid }),
        });
        const free = result.free_model_count ? ` ${result.free_model_count} free chat models found.` : "";
        setMsg(result.connected
          ? { ok: true, text: `Connected. ${result.status_detail}${result.model ? ` Model: ${result.model}.` : ""}${free}` }
          : { ok: false, text: `${result.status}: ${result.status_detail}${result.error_category ? ` (${result.error_category})` : ""}` });
      }
      setKey("");
      await reload();
    } catch (reason) {
      setMsg({ ok: false, text: message(reason, "Could not connect.") });
    } finally {
      setBusy(false);
    }
  }

  async function test() {
    if (!row) return;
    setBusy(true);
    try {
      const result = await api<Provider>(`/api/v1/ai-providers/${row.id}/test`, { method: "POST" });
      setMsg({ ok: result.status === "connected", text: `${result.status}: ${result.status_detail}` });
      await reload();
    } catch (reason) {
      setMsg({ ok: false, text: message(reason, "Test failed.") });
    } finally {
      setBusy(false);
    }
  }

  async function discover() {
    if (!row) return;
    try {
      const result = await api<{ message: string; models: DiscoveredModel[] }>(`/api/v1/ai-providers/${row.id}/models`, { method: "POST" });
      setMsg({ ok: true, text: result.message });
      await reload();
    } catch (reason) {
      setMsg({ ok: false, text: message(reason, "Model discovery failed.") });
    }
  }

  async function remove() {
    if (!row) return;
    try {
      await api(`/api/v1/ai-providers/${row.id}`, { method: "DELETE" });
      setMsg(null);
      await reload();
    } catch (reason) {
      notify(message(reason, "Could not remove the provider."), "error");
    }
  }

  async function pickModel(value: string) {
    if (!row) return;
    try {
      const result = await api<Provider>(`/api/v1/ai-providers/${row.id}/model`, { method: "POST", body: JSON.stringify({ model: value }) });
      setMsg({ ok: true, text: result.status_detail || `Selected ${result.model}.` });
      await reload();
    } catch (reason) {
      setMsg({ ok: false, text: message(reason, "Could not change the model.") });
    }
  }

  const freeModels = row ? (row.free_models?.length ? row.free_models : (row.discovered_models || []).filter((item) => item.billing === "free" && item.chat !== false)) : [];
  const options = [...freeModels];
  if (row?.model && !options.some((item) => item.id === row.model)) options.unshift({ id: row.model, name: row.model, billing: "free", chat: true });

  const info = !row
    ? "No key added."
    : row.status === "connected"
      ? `Key ${row.api_key_hint || "saved"} · ${row.model || "auto"}${row.free_model_count ? ` · ${row.free_model_count} free models found` : ""}`
      : row.status_detail || `Key ${row.api_key_hint || "saved"}. Not tested yet.`;

  return (
    <div className="glass provider">
      <div className="provider-head">
        <div className="provider-icon">{meta.initial}</div>
        <div><strong>{meta.name}</strong><span>{meta.desc}</span></div>
        {row ? <StatusBadge status={busy ? "testing" : row.status} small /> : <Badge tone="gray" small>Not connected</Badge>}
      </div>
      <label className="sr-only" htmlFor={`key-${meta.id}`}>{meta.name} API key</label>
      <input
        id={`key-${meta.id}`}
        className="input key-input"
        type="password"
        autoComplete="off"
        value={key}
        onChange={(event) => setKey(event.target.value)}
        placeholder={row ? `Replace key (${row.api_key_hint || "saved"})` : `Paste API key · ${meta.placeholder}`}
      />
      {custom && (
        <>
          <input className="input" type="url" value={endpoint} onChange={(event) => setEndpoint(event.target.value)} placeholder="https://example.com/v1" aria-label="API endpoint" />
          <input className="input" value={model} onChange={(event) => setModel(event.target.value)} placeholder="Model ID" aria-label="Model ID" />
        </>
      )}
      <span className="muted">{info}</span>
      {row?.provider === "openrouter" && row.status === "connected" && options.length > 0 && (
        <label className="field">Free model
          <select value={row.model || "openrouter/free"} onChange={(event) => pickModel(event.target.value)}>
            {options.map((item) => <option key={item.id} value={item.id}>{item.id === "openrouter/free" ? "Free Models Router (all free)" : item.name || item.id}</option>)}
          </select>
        </label>
      )}
      {msg && <div className={`note ${msg.ok ? "green" : "red"}`}>{msg.text}</div>}
      <div className="row" style={{ marginTop: "auto", gap: 8 }}>
        {key.trim() || !row ? (
          <button type="button" className="btn-outline" style={{ flex: 1 }} disabled={busy} onClick={connect}>{busy ? "Testing…" : custom ? "Save and test" : "Connect"}</button>
        ) : (
          <button type="button" className="btn-outline" style={{ flex: 1 }} disabled={busy || row.status === "failed"} onClick={test}>{busy ? "Testing…" : "Test connection"}</button>
        )}
        {row && row.status !== "failed" && !custom && <button type="button" className="btn-link" onClick={discover}>Find models</button>}
        {row && <button type="button" className="btn-danger-link" onClick={remove}>Remove</button>}
      </div>
    </div>
  );
}
