export async function api<T = any>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch("/api" + path, {
    method,
    headers:
      body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(
      data.error?.message ||
        (typeof data.detail === "string"
          ? data.detail
          : `请求失败 (${response.status})`),
    );
  return data;
}
export const statusText: Record<string, string> = {
  queued: "排队中",
  running: "生成中",
  succeeded: "已生成",
  failed: "失败",
  blocked: "等待处理",
  cancelled: "已取消",
  pending: "待审核",
  approved: "已批准",
  rejected: "需返工",
};
export const stageText: Record<string, string> = {
  requirements: "制作规范",
  plan: "分集分镜",
  reference: "素材参考图",
  keyframe: "镜头关键帧",
  video: "动态视频",
  audio: "独立配音",
  render: "成片合成",
  archive: "历史素材导入",
};
export function stamp(value: number) {
  return new Date(value * 1000).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
export interface Template {
  id: string;
  name: string;
  en: string;
  icon: string;
  color: string;
  tag: string;
  description: string;
  input_hint: string;
  rules: string;
}
export interface Artifact {
  id: string;
  project_id: string;
  entity_id: string;
  kind: string;
  url: string;
  review: string;
  stale: boolean;
  mime: string;
  contract_revision: number;
  plan_revision: number;
  dependencies?: string[];
  created_at: number;
  route: any;
  metadata: any;
  job_id: string;
  project_title?: string;
  review_note?: string;
  review_method?: string;
  archived?: boolean;
  title?: string;
  thumbnail_url?: string;
  rework_job_id?: string;
  rework_blocked?: string;
  storage?: {
    backend: "cos";
    bucket: string;
    region: string;
    key: string;
    sha256: string;
    size: number;
    verified_at: number;
  };
}
export interface Job {
  id: string;
  project_id: string;
  stage: string;
  entity_id: string;
  status: string;
  created_at: number;
  updated_at?: number;
  error?: { code: string; message: string };
  attempt: number;
  verification_required?: boolean;
  events?: { created: number; message: string }[];
  latest_event?: { created: number; message: string } | null;
  snapshot?: any;
  runtime?: any;
  effective_prompt?: string;
}
export function jobErrorMessage(job?: Job) {
  const error = job?.error;
  if (!error) return "";
  if (error.code === "authentication_failed")
    return "网页账号认证失败，请在连接设置中更新登录信息并重新连接。";
  if (error.code === "accounts_unavailable")
    return "没有可用的 Gemini 账号，请在连接设置中更新失效账号或启用其他账号。";
  return error.message;
}
export function requiresVerification(job?: Job) {
  return Boolean(
    job &&
    (job.verification_required ||
      job.runtime?.cloud_submitting ||
      [
        "REMOTE_RESULT_UNCERTAIN",
        "upstream_timeout",
        "upstream_error",
        "service_stopped",
        "download_failed",
        "video_download_failed",
      ].includes(job.error?.code || "") ||
      (job.error?.code === "SUBMISSION_UNKNOWN" && !job.runtime?.remote_id)),
  );
}
export interface Project {
  id: string;
  version: number;
  shot_reference_bindings?: Record<string, { artifact_ids: string[]; previous_shot_id: string | null }>;
  audio_cast?: { voices: Record<string, any>; speakers: Record<string, string> };
  reference_readiness?: { assets: Record<string, { required_roles: string[]; shot_roles: string[]; views: { role: string; artifact_id?: string; approved: boolean }[] }>; max_references: number; mode?: "automatic" | "explicit" } | null;
  title: string;
  mode: string;
  brief: string;
  source: string;
  style: string;
  language: string;
  duration: number;
  aspect_ratio: string;
  episodes_count: number;
  chapters: any[];
  contract: {
    revision: number;
    markdown: string;
    hash: string;
    approved: boolean;
  };
  plan: any;
  plan_revision: number;
  plan_approved: boolean;
  plan_contract_hash?: string;
  automation: boolean;
  automation_status?: {
    state: string;
    stage?: string;
    job_ids?: string[];
    error?: { code: string; message: string };
  };
  paused: boolean;
  cancelled: boolean;
  artifacts: Artifact[];
  jobs: Job[];
  progress: { id: string; name: string; done: number; total: number }[];
  created_at: number;
  updated_at: number;
  import_summary?: {
    total: number;
    counts: Record<string, number>;
    note: string;
    groups: Record<string, number>;
  };
  library_curation?: {
    counts: Record<string, number>;
    note: string;
    original_total: number;
  };
  reference_catalog?: {
    id: string;
    title: string;
    generated: number;
    missing_slots: string[];
  }[];
}

export function libraryPrimary(a: Artifact) {
  return (
    !a.metadata?.curation ||
    ["reference", "candidate"].includes(a.metadata.curation.state)
  );
}
