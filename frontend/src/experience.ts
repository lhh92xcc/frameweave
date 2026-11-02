import { requiresVerification, type Project, type Job } from "./api";

export const mediaNames: Record<string, string> = {
  all: "全部素材",
  reference: "人物与场景参考",
  keyframe: "镜头画面",
  video: "镜头视频",
  audio: "配音与录音",
  render: "最终成片",
};
export const taskNames: Record<string, string> = {
  requirements: "整理制作要求",
  plan: "编写分集和镜头",
  reference: "制作固定参考图",
  keyframe: "制作镜头画面",
  video: "生成镜头视频",
  audio: "制作配音",
  quality: "检查素材是否合格",
  render: "合成最终成片",
  archive: "导入已有素材",
};
export function deliveries(p: Project) {
  return p.artifacts.filter(
    (a) =>
      a.kind === "render" && a.review === "approved" && !a.stale && !a.archived,
  );
}
export function projectPercent(p: Project) {
  const total = p.progress.reduce((n, s) => n + s.total, 0);
  const done = p.progress.reduce((n, s) => n + Math.min(s.done, s.total), 0);
  return Math.min(100, Math.round((100 * done) / Math.max(1, total)));
}
export function projectSummary(p: Project) {
  const state = p.automation_status?.state;
  if (p.paused)
    return {
      id: "paused",
      label: "已暂停",
      hint: "打开项目，恢复后继续制作",
      action: "打开并恢复",
    };
  if (p.cancelled)
    return {
      id: "paused",
      label: "已停止",
      hint: "已完成的素材仍会保留",
      action: "查看项目",
    };
  if (
    state === "completed" ||
    (p.episodes_count > 0 && deliveries(p).length >= p.episodes_count)
  )
    return {
      id: "ready",
      label: "成片已完成",
      hint: `${deliveries(p).length} 集成片可播放、下载`,
      action: "查看成片",
    };
  if (["error", "attention"].includes(state || ""))
    return {
      id: "attention",
      label: "需要处理",
      hint: p.automation_status?.error?.message || "打开项目查看原因和处理办法",
      action: "查看并解决",
    };
  if (state === "retrying")
    return {
      id: "working",
      label: "自动重试中",
      hint: "系统会沿用固定参考继续，不用手动重试",
      action: "查看进度",
    };
  if (
    ["running", "queued", "scheduling"].includes(state || "") ||
    p.jobs.some((j) => ["running", "queued"].includes(j.status))
  )
    return {
      id: "working",
      label: state === "queued" ? "等待开始" : "正在制作",
      hint:
        p.production_mode === "full"
          ? "自动制作和检查，无需逐步批准"
          : "完成当前步骤后，可以打开项目继续",
      action: "查看进度",
    };
  if (state === "review" || state === "rework")
    return {
      id: "draft",
      label: p.production_mode === "full" ? "等待系统检查" : "等待你确认",
      hint:
        p.production_mode === "full"
          ? "系统检查合格后继续"
          : "打开项目检查结果，或开启全自动",
      action: "继续制作",
    };
  return {
    id: "draft",
    label: "尚未完成",
    hint:
      p.production_mode === "full"
        ? "全自动已关闭，可在项目里继续"
        : "打开项目查看方案，或开启全自动",
    action: "继续制作",
  };
}
export function jobSubject(job: Job, projects: Project[]) {
  const p = projects.find((p) => p.id === job.project_id);
  const entities = [
    ...(p?.plan?.assets || []),
    ...(p?.plan?.episodes || []),
    ...(p?.plan?.episodes?.flatMap((e: any) => e.shots) || []),
  ];
  const entity = entities.find((e) => e.id === job.entity_id);
  return {
    project: p?.title || "所属项目",
    entity: entity?.name || entity?.title || taskNames[job.stage] || "制作任务",
  };
}
export function taskAdvice(job: Job, retrying = false) {
  if (requiresVerification(job))
    return "先核对供应商的原任务和账单，确认结果；这里已停用重试。";
  if (retrying) return "系统稍后自动继续，固定参考不变，你不用操作。";
  if (job.status === "running") return "正在执行，完成后会自动更新。";
  if (job.status === "queued") return "正在排队，轮到后会自动开始。";
  const code = job.error?.code || "";
  if (
    /CONFIGURED|NO_ROUTE|authentication|accounts_unavailable|PAID_DISABLED|QC_COST|QC_BUDGET/.test(
      code,
    )
  )
    return "到“模型与连接”检查账号、模型或逐步制作预算，保存后再重试。";
  if (/VOICE|CAST|RECORDED|SPEAKER/.test(code))
    return "打开项目检查原声音、说话者或已有录音；不会创建替代音色。";
  if (/STALE|REFERENCE|INPUT/.test(code))
    return "打开项目检查最新要求和原参考，按当前版本继续。";
  if (["failed", "blocked"].includes(job.status))
    return "先查看原因，解决后可重试；已完成的素材会保留。";
  if (job.status === "succeeded")
    return "这个步骤已完成，最终成片请到项目或素材库查看。";
  return "已停止这个任务，已完成的内容会保留。";
}
