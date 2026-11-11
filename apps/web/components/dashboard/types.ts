export type Session = { user: { email: string; role: string; display_name: string }; organization: { name: string } };

export type Source = {
  id: string;
  kind: string;
  name: string;
  status: string;
  status_detail: string;
  config: { url?: string; filename?: string; accounts?: { username: string; role: string }[] };
  has_secret: boolean;
  last_checked_at?: string | null;
};

export type ProjectSettings = {
  analysis_mode: string;
  documentation_language: string;
  target_audience: string;
  allowed_domains: string[];
  doc_types: string[];
  output_formats: string[];
  safety_level: string;
  max_pages: number;
  max_depth: number;
  max_seconds: number;
  enable_video: boolean;
  enable_voiceover: boolean;
  use_ai_documentation: boolean;
  ai_provider_id: string;
  tts_provider: string;
  tts_model: string;
  tts_voice: string;
  [key: string]: unknown;
};

export type Project = { id: string; name: string; description: string; settings: ProjectSettings; sources?: Source[] };

export type JobStep = { name: string; status: string; detail: string };
export type Job = {
  id: string;
  project_id: string;
  run_id: string | null;
  job_type: string;
  status: string;
  progress: number;
  current_step: string;
  error_category: string;
  error_detail: string;
  retry_count: number;
  started_at: string | null;
  finished_at: string | null;
  steps: JobStep[];
  actions: { pause: boolean; resume: boolean; cancel: boolean; retry: boolean };
};

export type DiscoveredModel = { id: string; name: string; billing: string; chat?: boolean };
export type Provider = {
  id: string;
  provider: string;
  display_name: string;
  model: string;
  status: string;
  status_detail: string;
  error_category: string;
  api_key_hint: string;
  endpoint: string;
  free_models?: DiscoveredModel[];
  free_model_count?: number;
  discovered_models?: DiscoveredModel[];
};

export type Page = { id: string; run_id: string; url: string; title: string; access_status: string; verification_status: string; discovered_at: string | null };
export type Screenshot = { id: string; page_id: string | null; kind: string; sha256: string; url: string };
export type DocVersion = { id: string; run_id: string | null; format: string; verification_status: string; sha256: string; created_at: string | null };
export type DocumentRow = { id: string; doc_type: string; title: string; versions: DocVersion[] };
export type Video = { id: string; status: string; duration_seconds: number | null; limitations: string[] };
export type ChangeSummary = {
  changed_files?: string[];
  added_files?: string[];
  removed_files?: string[];
  pages_added?: string[];
  pages_removed?: string[];
};
export type ChangeRecord = { id: string; from_run_id: string; to_run_id: string; summary: ChangeSummary };

export type Criterion = { id: string; key: string; description: string; verification_method: string; status: string; mandatory: boolean };
export type Requirement = {
  id: string;
  key: string;
  title: string;
  description: string;
  category: string;
  priority: string;
  scope: string;
  status: string;
  blockers: unknown[];
  implementation_refs: unknown;
  acceptance_criteria: Criterion[];
  test_executions: { id: string; criterion_id: string; result: string; actual_result: string; environment: string; executed_at: string }[];
  evidence: { id: string; kind: string; summary: string }[];
};

export type Destination = { id: string; label: string; site_url: string; username: string; status: string; status_detail: string };
export type Monitor = { enabled: boolean; interval_minutes: number; next_run_at: string | null; last_run_at?: string | null; last_status: string; last_error: string };

export type Notify = (text: string, kind?: "ok" | "error") => void;
export type Screen = "analyze" | "results" | "api" | "reqs" | "settings";
