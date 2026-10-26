<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { api, statusText, stageText, stamp, type Job } from "./api";
import Icon from "./Icon.vue";
const emit = defineEmits<{ notify: [message: string]; refresh: [] }>(),
  jobs = ref<Job[]>([]),
  filter = ref("all"),
  detail = ref<Job | null>(null),
  error = ref("");
let timer: ReturnType<typeof setInterval>;
const filtered = computed(() =>
  jobs.value.filter(
    (j) =>
      filter.value === "all" ||
      (filter.value === "active" && ["running", "queued"].includes(j.status)) ||
      (filter.value === "attention" &&
        ["failed", "blocked"].includes(j.status)) ||
      filter.value === j.status,
  ),
);
async function load() {
  try {
    jobs.value = await api("/jobs");
    if (detail.value) detail.value = await api("/jobs/" + detail.value.id);
  } catch (e: any) {
    error.value = e.message;
  }
}
async function action(job: Job, name: string) {
  try {
    await api("/jobs/" + job.id + "/" + name, "POST");
    await load();
    emit("refresh");
    emit(
      "notify",
      name === "retry" ? "任务已恢复或重新入队" : "取消请求已记录",
    );
  } catch (e: any) {
    error.value = e.message;
  }
}
async function show(job: Job) {
  try {
    detail.value = await api("/jobs/" + job.id);
  } catch (e: any) {
    error.value = e.message;
  }
}
onMounted(() => {
  load();
  timer = setInterval(load, 3000);
});
onUnmounted(() => clearInterval(timer));
</script>
<template>
  <main class="queue-content">
    <div class="page-intro">
      <div>
        <div class="eyebrow">EVERY STEP, IN SIGHT</div>
        <h1>创作在进行，一切有迹可循。</h1>
        <p>查看任务状态，定位失败原因，继续未完成的步骤。</p>
      </div>
      <button class="btn secondary" @click="load">
        <Icon name="refresh" :size="16" />刷新队列
      </button>
    </div>
    <div class="queue-filters">
      <button
        v-for="f in [
          { id: 'all', name: '全部任务' },
          { id: 'active', name: '进行中' },
          { id: 'attention', name: '需要处理' },
          { id: 'succeeded', name: '已生成' },
          { id: 'cancelled', name: '已取消' },
        ]"
        :key="f.id"
        :class="{ selected: filter === f.id }"
        @click="filter = f.id"
      >
        {{ f.name }}<span v-if="f.id === 'all'">{{ jobs.length }}</span>
      </button>
    </div>
    <div v-if="error" class="inline-error" role="alert">
      <Icon name="warning" />{{ error
      }}<button class="icon-btn" aria-label="关闭错误" @click="error = ''">
        <Icon name="close" :size="15" />
      </button>
    </div>
    <div v-if="!filtered.length" class="work-panel stage-empty">
      <Icon name="clapper" :size="35" />
      <h3>这里，记录每一帧的诞生。</h3>
      <p>创建项目并开始生成后，任务会出现在这里。</p>
    </div>
    <div v-else class="job-table-wrap">
      <table class="job-table">
        <thead>
          <tr>
            <th>生成步骤 / 对象</th>
            <th>状态</th>
            <th>创建时间</th>
            <th>尝试</th>
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
                  ><b>{{ stageText[j.stage] }}</b
                  ><small>{{ j.entity_id || j.project_id }}</small></span
                >
              </button>
              <p v-if="j.error" class="job-error">{{ j.error.message }}</p>
            </td>
            <td>
              <span :class="['badge', j.status]">{{
                statusText[j.status]
              }}</span>
            </td>
            <td>{{ stamp(j.created_at) }}</td>
            <td>{{ j.attempt }}</td>
            <td>
              <div class="button-row">
                <button class="text-button" @click="show(j)">详情</button
                ><button
                  v-if="['failed', 'blocked', 'cancelled'].includes(j.status)"
                  class="text-button"
                  @click="action(j, 'retry')"
                >
                  <Icon name="refresh" :size="14" />重试</button
                ><button
                  v-if="['queued', 'running'].includes(j.status)"
                  class="text-button"
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
        <div class="eyebrow">GENERATION LOG</div>
        <h2>
          {{ stageText[detail.stage] }}
          <span :class="['badge', detail.status]">{{
            statusText[detail.status]
          }}</span>
        </h2>
        <code>{{ detail.id }}</code>
        <div v-if="detail.error" class="notice warning">
          <Icon name="warning" /><span
            >{{ detail.error.message
            }}<small class="block">{{ detail.error.code }}</small></span
          >
        </div>
        <div class="event-log">
          <div v-for="(event, i) in detail.events" :key="i">
            <time>{{ stamp(event.created) }}</time
            ><span>{{ event.message }}</span>
          </div>
          <p v-if="!detail.events?.length" class="muted">任务尚未执行。</p>
        </div>
        <details>
          <summary>输入快照与完整提示词</summary>
          <pre class="prompt-code">{{
            detail.effective_prompt || JSON.stringify(detail.snapshot, null, 2)
          }}</pre>
        </details>
        <div v-if="detail.runtime?.cloud_submitting" class="notice warning">
          该云请求结果不明。请先在供应商侧核对任务与账单；确认需要新请求后，在项目对应步骤中点击“重新生成”。
        </div>
        <div class="button-row">
          <button
            v-if="['failed', 'blocked', 'cancelled'].includes(detail.status)"
            class="btn primary"
            :disabled="detail.runtime?.cloud_submitting"
            @click="action(detail, 'retry')"
          >
            继续 / 重试
          </button>
        </div>
      </section>
    </div>
  </main>
</template>
