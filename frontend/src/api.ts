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
  entity_id: string;
  kind: string;
  url: string;
  review: string;
  stale: boolean;
  mime: string;
  contract_revision: number;
  plan_revision: number;
  created_at: number;
  route: any;
  metadata: any;
  job_id: string;
  project_title?: string;
  review_note?: string;
}
export interface Job {
  id: string;
  project_id: string;
  stage: string;
  entity_id: string;
  status: string;
  created_at: number;
  error?: { code: string; message: string };
  attempt: number;
  events?: any[];
  snapshot?: any;
  runtime?: any;
  effective_prompt?: string;
}
export interface Project {
  id: string;
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
  paused: boolean;
  cancelled: boolean;
  artifacts: Artifact[];
  jobs: Job[];
  progress: { id: string; name: string; done: number; total: number }[];
  created_at: number;
  updated_at: number;
}
