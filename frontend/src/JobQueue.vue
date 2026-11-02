<script setup lang="ts">
import { computed, ref, shallowRef } from "vue";
import { usePolling } from "./usePolling";
import {
  api,
  jobErrorMessage,
  requiresVerification,
  statusText,
  stageText,
  stamp,
  type Job,
  type Project,
} from "./api";
import { jobSubject, taskAdvice, taskNames } from "./experience";
import Icon from "./Icon.vue";
const props = defineProps<{ projects?: Project[] }>();
const emit = defineEmits<{
    notify: [message: string];
    refresh: [];
    project: [id: string];
    settings: [];
  }>(),
  jobs = shallowRef<Job[]>([]),
  filter = ref("all"),
  detail = ref<Job | null>(null),
  error = ref(""),
  loading = ref(true),
  busy = ref(""),
  projectFilter = ref(""),
  query = ref(""),
  includeHistory = ref(false);
let pendingLoad: Promise<void> | null = null;
function retrying(job: Job) {
  const state = props.projects?.find(
    (p) => p.id === job.project_id,
  )?.automation_status;
  return state?.state === "retrying" && !!state.job_ids?.includes(job.id);
}
const currentJobs = computed(() => {
  if (includeHistory.value) return jobs.value;
  const replaced = new Set(jobs.value.map((j) => j.retry_of).filter(Boolean));
  const latest = new Map<string, number>();
  for (const j of jobs.value)
    if (j.fingerprint)
      latest.set(
        j.fingerprint,
        Math.max(j.created_at, latest.get(j.fingerprint) || 0),
      );
  return jobs.value.filter(
    (j) =>
      !replaced.has(j.id) &&
      !(
        j.fingerprint &&
        ["failed", "blocked", "cancelled"].includes(j.status) &&
        j.created_at < latest.get(j.fingerprint)!
      ),
  );
});
const scopedJobs = computed(() =>
  currentJobs.value.filter(
    (j) =>
      (!projectFilter.value || j.project_id === projectFilter.value) &&
      (!query.value ||
        Object.values(jobSubject(j, props.projects || []))
          .join(" ")
          .toLowerCase()
          .includes(query.value.toLowerCase())),
  ),
);
const filters = [
  { id: "all", name: "全部任务" },
  { id: "active", name: "进行中" },
  { id: "attention", name: "需要处理" },
  { id: "succeeded", name: "已完成步骤" },
  { id: "cancelled", name: "已取消" },
];
function matches(j: Job, key: string) {
  return (
    key === "all" ||
    (key === "active" &&
      (["running", "queued"].includes(j.status) || retrying(j))) ||
    (key === "attention" &&
      ["failed", "blocked"].includes(j.status) &&
      !retrying(j)) ||
    key === j.status
  );
}
const counts = computed(() =>
  Object.fromEntries(
    filters.map((f) => [
      f.id,
      scopedJobs.value.filter((j) => matches(j, f.id)).length,
    ]),
  ),
);
const filtered = computed(() =>
  scopedJobs.value.filter((j) => matches(j, filter.value)),
);
async function load() {
  while (pendingLoad) await pendingLoad;
  const request = loadNow();
  pendingLoad = request;
  try {
    await request;
  } finally {
    if (pendingLoad === request) pendingLoad = null;
  }
}
async function loadNow() {
  try {
    jobs.value = await api("/jobs");
    const selectedId = detail.value?.id;
    if (selectedId) {
      const result = await api<Job>("/jobs/" + selectedId);
      if (detail.value?.id === selectedId) detail.value = result;
    }
    error.value = "";
  } catch (e: any) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}
async function action(job: Job, name: string) {
  if (busy.value) return;
  busy.value = job.id;
  try {
    const result = await api<Job>("/jobs/" + job.id + "/" + name, "POST");
    await load();
    emit("refresh");
    emit(
      "notify",
      name !== "retry"
        ? "取消请求已记录"
        : result.id === job.id
          ? "已继续查询已有任务，不会重复提交"
          : result.status === "succeeded"
            ? "该重试任务已完成，请查看结果"
            : "已安排新的重试任务，请查看进度",
    );
  } catch (e: any) {
    error.value = e.message;
  } finally {
    busy.value = "";
  }
}
async function show(job: Job) {
  try {
    detail.value = await api("/jobs/" + job.id);
  } catch (e: any) {
    error.value = e.message;
  }
}
usePolling(load, 3000);
</script>
<template>
  <main class="queue-content">
    <div class="page-intro">
      <div>
        <div class="eyebrow">生成队列</div>
        <h1>看看系统正在做什么</h1>
        <p>
          一个作品会拆成多个任务。这里查看进度和处理问题；下载整集视频请打开项目。
        </p>
      </div>
      <button class="btn secondary" @click="load">
        <Icon name="refresh" :size="16" />刷新队列
      </button>
    </div>
    <div class="queue-overview" aria-label="任务状态说明">
      <span><i class="connection-dot on" />制作和排队会自动更新</span
      ><span
        ><Icon name="refresh" :size="16" />系统自动重试时，不需要你操作</span
      ><span><Icon name="warning" :size="16" />结果不明先核对原任务</span>
    </div>
    <div class="collection-toolbar queue-search">
      <label class="sort-control"
        >所属项目<select v-model="projectFilter" aria-label="按项目筛选任务">
          <option value="">全部项目</option>
          <option v-for="p in projects || []" :key="p.id" :value="p.id">
            {{ p.title }}
          </option>
        </select></label
      >
      <label class="queue-search-input"
        ><Icon name="search" :size="17" /><input
          v-model="query"
          aria-label="搜索制作任务"
          placeholder="搜索项目、角色或镜头"
      /></label>
      <label class="inline-checkbox"
        ><input
          v-model="includeHistory"
          type="checkbox"
        />显示以前的重试记录</label
      >
    </div>
    <div class="queue-filters">
      <button
        v-for="f in filters"
        :key="f.id"
        :class="{ selected: filter === f.id }"
        :aria-pressed="filter === f.id"
        :aria-label="`${f.name} ${counts[f.id]}`"
        @click="filter = f.id"
      >
        {{ f.name }}<span>{{ counts[f.id] }}</span>
      </button>
    </div>
    <div v-if="error" class="inline-error" role="alert">
      <Icon name="warning" />{{ error
      }}<button class="icon-btn" aria-label="关闭错误" @click="error = ''">
        <Icon name="close" :size="15" />
      </button>
    </div>
    <div v-if="loading" class="panel-loading" role="status">
      正在读取制作进度…
    </div>
    <div v-else-if="!filtered.length" class="work-panel stage-empty">
      <Icon name="clapper" :size="35" />
      <h3>
        {{
          filter === "attention"
            ? "当前没有需要你处理的任务"
            : "当前筛选下没有任务"
        }}
      </h3>
      <p>开始制作后，这里会显示各个步骤的进度。全自动的临时故障会自行重试。</p>
    </div>
    <div v-else class="job-table-wrap">
      <table class="job-table">
        <thead>
          <tr>
            <th>正在做什么 / 所属作品</th>
            <th>状态</th>
            <th>现在需要做什么</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="j in filtered" :key="j.id">
            <td>
              <button @click="show(j)" class="job-title">
                <Icon
                  :name="
                    j.status === 'running'
                      ? 'loading'
                      : j.stage === 'video'
                        ? 'clapper'
                        : j.stage === 'reference' || j.stage === 'keyframe'
                          ? 'images'
                          : 'file'
                  "
                  :class="{ spinning: j.status === 'running' }"
                /><span
                  ><b>{{ taskNames[j.stage] || stageText[j.stage] }}</b
                  ><small
                    >{{ jobSubject(j, projects || []).project }} ·
                    {{ jobSubject(j, projects || []).entity }}</small
                  ></span
                >
              </button>
              <p v-if="j.error" class="job-error">{{ jobErrorMessage(j) }}</p>
            </td>
            <td>
              <span :class="['badge', j.status]">{{
                retrying(j)
                  ? "等待自动重试"
                  : j.status === "succeeded"
                    ? "步骤已完成"
                    : statusText[j.status]
              }}</span>
            </td>
            <td class="task-advice">{{ taskAdvice(j, retrying(j)) }}</td>
            <td>
              <div class="button-row">
                <button class="text-button" @click="show(j)">详情</button
                ><button
                  class="text-button"
                  @click="emit('project', j.project_id)"
                >
                  打开项目</button
                ><button
                  v-if="['failed', 'blocked', 'cancelled'].includes(j.status)"
                  class="text-button"
                  :disabled="!!busy || requiresVerification(j) || retrying(j)"
                  :title="
                    requiresVerification(j)
                      ? '请先核对供应商任务和账单'
                      : undefined
                  "
                  @click="action(j, 'retry')"
                >
                  <Icon name="refresh" :size="14" />重试</button
                ><button
                  v-if="['queued', 'running'].includes(j.status)"
                  class="text-button"
                  :disabled="!!busy"
                  @click="action(j, 'cancel')"
                >
                  取消
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="detail" class="modal-backdrop" @click.self="detail = null">
      <section
        class="modal job-detail"
        role="dialog"
        aria-modal="true"
        aria-label="任务详情"
      >
        <button
          class="icon-btn modal-close"
          aria-label="关闭任务详情"
          @click="detail = null"
        >
          <Icon name="close" />
        </button>
        <div class="eyebrow">任务详情</div>
        <h2>
          {{ stageText[detail.stage] }}
          <span :class="['badge', detail.status]">{{
            statusText[detail.status]
          }}</span>
        </h2>
        <p class="page-explainer">
          {{ jobSubject(detail, projects || []).project }} ·
          {{ jobSubject(detail, projects || []).entity }}
        </p>
        <p class="notice">{{ taskAdvice(detail, retrying(detail)) }}</p>
        <div v-if="detail.error" class="notice warning">
          <Icon name="warning" /><span>{{ jobErrorMessage(detail) }}</span>
        </div>
        <div class="event-log">
          <div v-for="(event, i) in detail.events" :key="i">
            <time>{{ stamp(event.created) }}</time
            ><span>{{ event.message }}</span>
          </div>
          <p v-if="!detail.events?.length" class="muted">任务尚未执行。</p>
        </div>
        <details v-if="detail.runtime?.image_qc_attempts?.length">
          <summary>
            自动视觉质检与候选图（{{
              detail.runtime.image_qc_attempts.length
            }}
            次）
          </summary>
          <div
            v-for="attempt in detail.runtime.image_qc_attempts"
            :key="attempt.attempt"
          >
            <p>
              第 {{ attempt.attempt }} 次 ·
              {{ attempt.passed ? "视觉质检通过" : "未通过" }}
            </p>
            <p class="muted small">
              生图：{{ attempt.result?.route?.provider || "未知" }} /
              {{ attempt.result?.route?.model || "未知" }}；质检：{{
                attempt.qc_route?.provider || "未知"
              }}
              / {{ attempt.qc_route?.model || "未知" }}
            </p>
            <a
              :href="`/api/jobs/${detail.id}/image-attempts/${attempt.attempt}`"
              target="_blank"
              rel="noopener"
              >查看候选原图</a
            >
            <pre class="prompt-code">{{
              JSON.stringify(attempt.qc, null, 2)
            }}</pre>
            <details>
              <summary>本次生图提示词</summary>
              <pre class="prompt-code">{{ attempt.prompt }}</pre>
            </details>
          </div>
        </details>
        <p v-if="detail.runtime?.pending_qc_image" class="notice warning">
          候选图已生成但质检未完成，未进入人工审核。
          <a
            :href="`/api/jobs/${detail.id}/image-attempts/${detail.runtime.pending_qc_image.attempt}`"
            target="_blank"
            rel="noopener"
            >查看候选图</a
          >
        </p>
        <details v-if="detail.runtime?.qc_cost_reservations">
          <summary>已预留的预计请求费用（元，非实际账单）</summary>
          <pre class="prompt-code">{{
            JSON.stringify(detail.runtime.qc_cost_reservations, null, 2)
          }}</pre>
        </details>
        <details>
          <summary>技术记录：任务编号、输入与完整提示词</summary>
          <p>
            任务 {{ detail.id }} · 创建于 {{ stamp(detail.created_at) }} · 第
            {{ detail.attempt }} 次尝试
          </p>
          <p v-if="detail.error">错误代码：{{ detail.error.code }}</p>
          <pre class="prompt-code">{{
            detail.effective_prompt || JSON.stringify(detail.snapshot, null, 2)
          }}</pre>
        </details>
        <div v-if="requiresVerification(detail)" class="notice warning">
          该请求结果不明。请先在供应商侧核对任务与账单；确认需要新请求后，在项目对应步骤中点击“重新生成”。
        </div>
        <div class="button-row">
          <button
            v-if="['failed', 'blocked', 'cancelled'].includes(detail.status)"
            class="btn primary"
            :disabled="
              !!busy || requiresVerification(detail) || retrying(detail)
            "
            @click="action(detail, 'retry')"
          >
            继续 / 重试
          </button>
          <button
            class="btn secondary"
            @click="emit('project', detail.project_id)"
          >
            打开所属项目
          </button>
          <button
            v-if="detail.error && !requiresVerification(detail)"
            class="text-button"
            @click="emit('settings')"
          >
            检查模型与连接
          </button>
        </div>
      </section>
    </div>
  </main>
</template>
