<script setup lang="ts">
import { computed, ref, watch } from "vue";
import {
  api,
  jobErrorMessage,
  requiresVerification,
  stageText,
  stamp,
  statusText,
  type Job,
  type Project,
} from "./api";
import Icon from "./Icon.vue";

const props = defineProps<{
  project: Project;
  worker?: boolean;
  reload: () => Promise<void>;
}>();
const emit = defineEmits<{
  notify: [message: string];
  settings: [];
  jobs: [];
  review: [stage: string];
}>();
const expanded = ref(false),
  selectedId = ref(""),
  detail = ref<Job | null>(null),
  error = ref(""),
  busy = ref("");
let detailRequest = 0;

const recentJobs = computed(() => {
  const key = (job: Job) => JSON.stringify([job.stage, job.entity_id]);
  const latest = new Map<string, Job>();
  const active = props.project.jobs.filter((job) =>
    ["running", "queued"].includes(job.status),
  );
  const activeKeys = new Set(active.map(key));
  for (const job of props.project.jobs) {
    const prior = latest.get(key(job));
    if (!prior || job.created_at > prior.created_at) latest.set(key(job), job);
  }
  const jobs = [
    ...active,
    ...[...latest.values()].filter((job) => !activeKeys.has(key(job))),
  ];
  const priority: Record<string, number> = {
    running: 0,
    failed: 1,
    blocked: 1,
    queued: 2,
    cancelled: 3,
    succeeded: 4,
  };
  return jobs.sort(
    (first, second) =>
      (priority[first.status] ?? 5) - (priority[second.status] ?? 5),
  );
});
const visibleJobs = computed(() =>
  expanded.value ? recentJobs.value : recentJobs.value.slice(0, 4),
);
const counts = computed(() => {
  const result: Record<string, number> = {};
  for (const job of recentJobs.value)
    result[job.status] = (result[job.status] || 0) + 1;
  return result;
});
const automation = computed(() => props.project.automation_status);
const state = computed(() => automation.value?.state || "disabled");
const stageName = computed(
  () => stageText[automation.value?.stage || ""] || "当前步骤",
);
const stateTitle = computed(() => {
  const titles: Record<string, string> = {
    disabled: "自动推进已关闭",
    paused: "项目已暂停",
    cancelled: "项目已取消",
    running: "自动推进进行中",
    queued: "任务等待执行",
    scheduling: "正在准备下一步",
    attention: "自动推进等待处理",
    error: "自动推进无法安排下一步",
    review: "等待人工审核",
    rework: "等待返工",
    completed: "生产流程已完成",
  };
  return titles[state.value] || "自动推进状态未知";
});
const stateDescription = computed(() => {
  const descriptions: Record<string, string> = {
    disabled: "已提交的任务仍会按队列执行；开启自动推进后才会安排后续步骤。",
    paused: "已提交的模型请求可能继续完成，后续任务等待恢复。",
    cancelled: "项目已停止；恢复后可继续处理任务。",
    running: "正在执行已提交的任务，完成后仍需人工审核。",
    queued: "任务已入队，等待工作线程执行。",
    scheduling: `等待工作线程安排${stageName.value}。`,
    attention: "本阶段有任务失败、等待处理或已取消；请查看原因并手动重试。",
    error: automation.value?.error?.message || "请检查项目配置后重试安排。",
    review: `${stageName.value}已准备好，请审核后继续。`,
    rework: `${stageName.value}有结果被标记为需返工，请重新生成或上传后审核。`,
    completed: "所有制作阶段均已通过审核。",
  };
  return descriptions[state.value] || "请刷新页面查看最新状态。";
});
const workerOffline = computed(
  () =>
    props.worker === false &&
    (props.project.automation ||
      (counts.value.running || 0) + (counts.value.queued || 0) > 0),
);

function entityName(job: Job) {
  if (!job.entity_id) return "";
  const plan = props.project.plan;
  const entities = [
    ...(plan?.assets || []),
    ...(plan?.episodes || []),
    ...(plan?.episodes?.flatMap((episode: any) => episode.shots) || []),
  ];
  const entity = entities.find((item: any) => item.id === job.entity_id);
  return entity?.name || entity?.title || job.entity_id;
}
function toggleDetail(job: Job) {
  selectedId.value = selectedId.value === job.id ? "" : job.id;
}
watch(
  () => props.project.id,
  () => {
    selectedId.value = "";
    expanded.value = false;
    error.value = "";
  },
);
watch(
  () => [props.project, selectedId.value] as const,
  async ([, id]) => {
    const request = ++detailRequest;
    if (!id) {
      detail.value = null;
      return;
    }
    if (detail.value?.id !== id) detail.value = null;
    try {
      const result = await api<Job>("/jobs/" + id);
      if (request === detailRequest) detail.value = result;
    } catch (exception: any) {
      if (request === detailRequest) error.value = exception.message;
    }
  },
  { immediate: true },
);
async function retry(job: Job) {
  busy.value = job.id;
  error.value = "";
  try {
    const result = await api<Job>("/jobs/" + job.id + "/retry", "POST");
    selectedId.value = result.id;
    await props.reload();
    emit(
      "notify",
      result.id === job.id
        ? "已继续查询已有任务，不会重复提交"
        : result.status === "succeeded"
          ? "该重试任务已完成，请查看结果"
          : "已安排新的重试任务，请查看进度",
    );
  } catch (exception: any) {
    error.value = exception.message;
  } finally {
    busy.value = "";
  }
}
async function retryScheduling() {
  busy.value = "scheduling";
  error.value = "";
  try {
    await api("/projects/" + props.project.id + "/automation", "POST", {
      enabled: true,
    });
    await props.reload();
    emit("notify", "已重新尝试安排自动推进");
  } catch (exception: any) {
    error.value = exception.message;
  } finally {
    busy.value = "";
  }
}
</script>

<template>
  <section
    v-if="
      project.automation ||
      project.paused ||
      project.cancelled ||
      project.jobs.length
    "
    class="project-activity"
    aria-label="项目生产进度"
  >
    <div class="activity-heading">
      <div>
        <h2>生产进度</h2>
        <p>状态自动更新 · 生成成功不等于审核通过</p>
      </div>
      <button class="text-button" @click="emit('jobs')">
        查看全部生成队列 <Icon name="right" :size="14" />
      </button>
    </div>
    <div :class="['activity-state', state]" aria-live="polite">
      <Icon
        :name="
          ['error', 'attention'].includes(state)
            ? 'warning'
            : state === 'running'
              ? 'loading'
              : 'clock'
        "
        :class="{ spinning: state === 'running' }"
        :size="18"
      />
      <div>
        <strong>{{ stateTitle }}</strong>
        <p>{{ stateDescription }}</p>
      </div>
      <button
        v-if="['review', 'rework'].includes(state) && automation?.stage"
        class="text-button"
        @click="emit('review', automation.stage)"
      >
        {{ state === "review" ? "前往审核" : "前往处理" }}
      </button>
      <button
        v-if="state === 'error'"
        class="text-button"
        :disabled="!!busy"
        @click="retryScheduling"
      >
        重试安排
      </button>
    </div>
    <div v-if="workerOffline" class="activity-warning" role="alert">
      工作线程未运行，队列不会继续；请检查服务状态并重启工作台。
    </div>
    <div v-if="error" class="inline-error" role="alert">
      <Icon name="warning" :size="16" />{{ error }}
      <button class="icon-btn" aria-label="关闭进度错误" @click="error = ''">
        <Icon name="close" :size="15" />
      </button>
    </div>
    <template v-if="recentJobs.length">
      <div class="activity-counts">
        <span>当前及最近任务</span>
        <span
          >生成中 <b>{{ counts.running || 0 }}</b></span
        >
        <span
          >排队 <b>{{ counts.queued || 0 }}</b></span
        >
        <span
          >成功 <b>{{ counts.succeeded || 0 }}</b></span
        >
        <span
          >失败 <b>{{ counts.failed || 0 }}</b></span
        >
        <span
          >待处理 <b>{{ counts.blocked || 0 }}</b></span
        >
        <span
          >已取消 <b>{{ counts.cancelled || 0 }}</b></span
        >
      </div>
      <div class="activity-list">
        <div v-for="job in visibleJobs" :key="job.id" class="activity-entry">
          <div class="activity-row">
            <Icon
              :name="
                job.status === 'running'
                  ? 'loading'
                  : ['failed', 'blocked'].includes(job.status)
                    ? 'warning'
                    : job.status === 'succeeded'
                      ? 'approved'
                      : 'clock'
              "
              :class="{ spinning: job.status === 'running' }"
              :size="17"
            />
            <div class="activity-job">
              <strong
                >{{ stageText[job.stage] || job.stage
                }}<template v-if="entityName(job)">
                  · {{ entityName(job) }}</template
                ></strong
              >
              <p v-if="job.error" class="activity-job-error">
                {{ jobErrorMessage(job) }}
              </p>
              <p v-else-if="job.status === 'running' && job.latest_event">
                {{ job.latest_event.message }} ·
                {{ stamp(job.latest_event.created) }}
              </p>
              <p v-else-if="job.status === 'running'">
                任务已启动，等待执行记录
              </p>
            </div>
            <span :class="['badge', job.status]">{{
              statusText[job.status] || job.status
            }}</span>
            <button
              class="text-button"
              :aria-label="`查看${stageText[job.stage] || job.stage} ${entityName(job)}任务详情`"
              :aria-expanded="selectedId === job.id"
              @click="toggleDetail(job)"
            >
              {{ selectedId === job.id ? "收起" : "详情" }}
            </button>
            <button
              v-if="['failed', 'blocked', 'cancelled'].includes(job.status)"
              class="text-button"
              :disabled="!!busy || requiresVerification(job)"
              @click="retry(job)"
            >
              重试
            </button>
          </div>
          <p v-if="requiresVerification(job)" class="activity-uncertain">
            提交结果不明，请先在供应商侧核对任务和账单，避免重复提交。
          </p>
          <div v-if="selectedId === job.id" class="activity-log">
            <p v-if="!detail || detail.id !== job.id">正在读取任务记录…</p>
            <template v-else>
              <p>
                任务 {{ detail.id }} · 第 {{ detail.attempt }} 次尝试<template
                  v-if="detail.error"
                >
                  · {{ detail.error.code }}</template
                >
              </p>
              <div v-for="(event, index) in detail.events" :key="index">
                <time>{{ stamp(event.created) }}</time
                ><span>{{ event.message }}</span>
              </div>
              <p v-if="!detail.events?.length">任务尚无执行记录。</p>
            </template>
          </div>
        </div>
      </div>
      <button
        v-if="recentJobs.length > 4"
        class="text-button activity-more"
        @click="expanded = !expanded"
      >
        {{ expanded ? "收起任务" : `展开全部 ${recentJobs.length} 项最近任务` }}
      </button>
    </template>
    <button
      v-if="['attention', 'error'].includes(state)"
      class="text-button activity-settings"
      @click="emit('settings')"
    >
      检查模型与连接
    </button>
  </section>
</template>
