<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { marked } from "marked";
import DOMPurify from "dompurify";
import Icon from "./Icon.vue";
import MediaDetail from "./MediaDetail.vue";
import ArtifactSource from "./ArtifactSource.vue";
import ProjectActivity from "./ProjectActivity.vue";
import {
  api,
  jobErrorMessage,
  requiresVerification,
  statusText,
  stageText,
  type Artifact,
  type Project,
} from "./api";
const props = defineProps<{
    project: Project;
    reload: () => Promise<void>;
    worker?: boolean;
  }>(),
  emit = defineEmits<{
    notify: [message: string];
    settings: [];
    back: [];
    jobs: [];
    library: [projectId: string];
  }>();
const fileInput = ref<HTMLInputElement>(),
  uploadTarget = ref("");
const stage = ref("requirements"),
  imageTab = ref("reference"),
  episodeIndex = ref(0),
  markdown = ref(props.project.contract.markdown),
  preview = ref(false),
  busy = ref(false),
  error = ref(""),
  detail = ref<Artifact | null>(null),
  referenceShot = ref<any>(null),
  extraReferences = ref<string[]>([]),
  previousShot = ref(""),
  editShot = ref<any>(null),
  planJson = ref(""),
  showJson = ref(false),
  sourceOpen = ref(false),
  selectedReviewIds = ref<string[]>([]),
  batchNote = ref(""),
  batchError = ref(""),
  batchProgress = ref("");
watch(
  () => props.project.contract.markdown,
  (v, old) => {
    if (markdown.value === old) markdown.value = v;
  },
);
watch(
  () => props.project.id,
  () => {
    stage.value = "requirements";
    markdown.value = props.project.contract.markdown;
    episodeIndex.value = 0;
  },
);
const dirty = computed(
    () => markdown.value !== props.project.contract.markdown,
  ),
  html = computed(() =>
    DOMPurify.sanitize(marked.parse(markdown.value, { async: false })),
  ),
  p = computed(() => props.project);
const shots = computed(
    () => p.value.plan?.episodes.flatMap((e: any) => e.shots) || [],
  ),
  episode = computed(() => p.value.plan?.episodes[episodeIndex.value]);
const stageDescription: Record<string, string> = {
  requirements: "一份规范，贯穿每一次生成。先把故事、视觉和交付要求写清楚。",
  plan: "先看全局，再看每一镜。核对来源章节、素材绑定和镜头时长。",
  reference: "为人物、场景和道具建立稳定的身份，再把它们带进每一帧。",
  keyframe: "让每个镜头从一张经过确认的画面开始。",
  video: "从批准的关键帧出发，让动作、运镜与情绪自然发生。",
  render: "核对完整时间线，把已批准的镜头织成一集作品。",
};
const activeKind = computed(() =>
  stage.value === "reference" ? imageTab.value : stage.value,
);
const entities = computed(() =>
  activeKind.value === "reference"
    ? p.value.plan?.assets || []
    : activeKind.value === "render"
      ? p.value.plan?.episodes || []
      : episode.value?.shots || [],
);
const mediaSummary = computed(() => {
  const results = entities.value.map((entity: any) =>
    artifact(activeKind.value, entity.id),
  );
  return {
    available: results.filter(Boolean).length,
    pending: results.filter(
      (result: Artifact | undefined) => result?.review === "pending",
    ).length,
    approved: results.filter(
      (result: Artifact | undefined) => result?.review === "approved",
    ).length,
  };
});
function artifact(kind: string, id: string) {
  return p.value.artifacts.find(
    (a) => a.kind === kind && a.entity_id === id && !a.stale,
  );
}
function openReferences(shot: any) {
  referenceShot.value = shot;
  const binding = p.value.shot_reference_bindings?.[shot.id];
  extraReferences.value = [...(binding?.artifact_ids || [])];
  previousShot.value = binding?.previous_shot_id || "";
}
const eligibleReferences = computed(() => p.value.artifacts.filter((item) =>
  referenceShot.value && item.kind === "reference" && !item.archived &&
  item.review === "approved" && item.contract_revision === p.value.contract.revision &&
  item.plan_revision === p.value.plan_revision && referenceShot.value.asset_ids.includes(item.entity_id),
));
const previousShots = computed(() => {
  const index = shots.value.findIndex((shot: any) => shot.id === referenceShot.value?.id);
  return shots.value.slice(0, Math.max(index, 0)).filter((shot: any) => artifact("keyframe", shot.id)?.review === "approved");
});
async function saveReferences() {
  await action(() => api(`/projects/${p.value.id}/shots/${referenceShot.value.id}/references`, "PUT", {
    artifact_ids: extraReferences.value,
    previous_shot_id: previousShot.value || null,
    expected_version: p.value.version,
  }), "镜头参考绑定已更新，受影响的关键帧及下游审核失效。");
  if (!error.value) referenceShot.value = null;
}
function referenceSource(id: string) {
  return p.value.artifacts
    .filter(
      (a) =>
        a.kind === "reference" &&
        a.entity_id === id &&
        !a.archived &&
        a.route?.provider === "user-upload" &&
        a.contract_revision === p.value.contract.revision &&
        a.plan_revision === p.value.plan_revision,
    )
    .sort((first, second) => second.created_at - first.created_at)[0];
}
function hasOlderReferenceSource(id: string) {
  return p.value.artifacts.some(
    (a) =>
      a.kind === "reference" &&
      a.entity_id === id &&
      !a.archived &&
      a.route?.provider === "user-upload",
  );
}
function jobState(kind: string, id = "") {
  const jobs = p.value.jobs.filter(
    (j) => j.stage === kind && j.entity_id === id,
  );
  return (
    jobs.find((job) => job.status === "running") ||
    jobs.find((job) => job.status === "queued") ||
    jobs.reduce<(typeof jobs)[number] | undefined>(
      (latest, job) =>
        !latest || job.created_at > latest.created_at ? job : latest,
      undefined,
    )
  );
}
async function action(fn: () => Promise<any>, message?: string) {
  busy.value = true;
  error.value = "";
  try {
    await fn();
    await props.reload();
    await nextTick();
    if (message) emit("notify", message);
  } catch (e: any) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function generate(kind: string, id = "", force = false) {
  if (
    requiresVerification(jobState(kind, id)) &&
    !window.confirm(
      "上次提交结果不明，重新生成可能重复扣费。请先在供应商侧核对任务和账单；确认已核对并需要重新生成吗？",
    )
  )
    return;
  await action(
    () =>
      api("/projects/" + p.value.id + "/jobs", "POST", {
        stage: kind,
        entity_id: id,
        force,
      }),
    "任务已加入队列",
  );
}
async function generateAll() {
  const kind = activeKind.value;
  await action(async () => {
    const uncertain = entities.value.find(
      (entity: any) =>
        !artifact(kind, entity.id) &&
        requiresVerification(jobState(kind, entity.id)),
    );
    if (uncertain)
      throw new Error(
        `${uncertain.name || uncertain.title || uncertain.id} 的上次提交结果不明；请先核对供应商记录，再单独确认重新生成。`,
      );
    for (const e of entities.value) {
      if (!artifact(kind, e.id))
        await api("/projects/" + p.value.id + "/jobs", "POST", {
          stage: kind,
          entity_id: e.id,
        });
    }
  }, "已为当前集提交待生成任务");
}
async function saveContract() {
  await action(
    () =>
      api("/projects/" + p.value.id + "/contract", "PUT", {
        markdown: markdown.value,
        expected_revision: p.value.contract.revision,
      }),
    "新规范版本已保存。请重新确认下游内容。",
  );
}
async function approveContract() {
  await action(
    () =>
      api("/projects/" + p.value.id + "/contract/approve", "POST", {
        hash: p.value.contract.hash,
      }),
    "制作规范已批准，可以拆分分集和分镜。",
  );
}
async function control(actionName: string) {
  await action(() => api("/projects/" + p.value.id + "/" + actionName, "POST"));
}
async function automate() {
  await action(
    () =>
      api("/projects/" + p.value.id + "/automation", "POST", {
        enabled: !p.value.automation,
      }),
    p.value.automation
      ? "已关闭自动推进"
      : "已开启自动生产，将在需要审核的阶段等待你确认。",
  );
}
function openEdit(shot: any) {
  editShot.value = JSON.parse(JSON.stringify(shot));
}
async function saveShot() {
  const plan = JSON.parse(JSON.stringify(p.value.plan));
  for (const ep of plan.episodes) {
    const i = ep.shots.findIndex((s: any) => s.id === editShot.value.id);
    if (i >= 0) ep.shots[i] = editShot.value;
  }
  await action(
    () =>
      api("/projects/" + p.value.id + "/plan", "PUT", {
        plan,
        expected_revision: p.value.plan_revision,
      }),
    "分镜已保存为新版本，关联旧素材的批准状态已失效。",
  );
  if (!error.value) editShot.value = null;
}
function editJson() {
  planJson.value = JSON.stringify(p.value.plan, null, 2);
  showJson.value = true;
}
async function saveJson() {
  await action(
    () =>
      api("/projects/" + p.value.id + "/plan", "PUT", {
        plan: JSON.parse(planJson.value),
        expected_revision: p.value.plan_revision,
      }),
    "制作计划已更新",
  );
  if (!error.value) showJson.value = false;
}
function chooseUpload(id: string) {
  uploadTarget.value = id;
  fileInput.value?.click();
}
async function upload(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0];
  if (!file) return;
  const form = new FormData();
  form.set("stage", activeKind.value);
  form.set("entity_id", uploadTarget.value);
  form.set("file", file);
  await action(async () => {
    const response = await fetch("/api/projects/" + p.value.id + "/upload", {
      method: "POST",
      body: form,
    });
    if (!response.ok) {
      const data = await response.json();
      throw new Error(data.error?.message || data.detail || "导入失败");
    }
  }, "原始素材已导入，审核批准后可进入下一步");
  (event.target as HTMLInputElement).value = "";
}
const readyForMedia = computed(
  () => p.value.contract.approved && p.value.plan_approved,
);
const canShowReference = computed(
  () =>
    activeKind.value === "reference" &&
    !!p.value.plan &&
    p.value.plan_contract_hash === p.value.contract.hash,
);
const canBrowseMedia = computed(
  () => readyForMedia.value || canShowReference.value,
);
const canBatchReview = computed(
  () => ["reference", "keyframe"].includes(activeKind.value) && readyForMedia.value,
);
const reviewCandidates = computed<{ id: string; name: string }[]>(() =>
  entities.value.flatMap((entity: any) => {
    const item = artifact(activeKind.value, entity.id);
    return item && !item.archived && item.review === "pending"
      ? [{ id: item.id, name: entity.name || entity.title || entity.id }]
      : [];
  }),
);
const selectedReviews = computed(() =>
  reviewCandidates.value.filter((item: { id: string }) => selectedReviewIds.value.includes(item.id)),
);
watch([() => p.value.id, activeKind, episodeIndex], () => {
  selectedReviewIds.value = [];
  batchNote.value = "";
  batchError.value = "";
  batchProgress.value = "";
});
async function reviewSelected(approved: boolean) {
  const items = [...selectedReviews.value];
  if (!items.length || busy.value) return;
  const decision = approved ? "批准" : "标记为需返工";
  const warning = approved && p.value.automation && !p.value.paused && !p.value.cancelled
    ? "\n项目已开启自动推进，批准后可能继续生成并产生费用。"
    : "";
  if (!window.confirm(`确认将选中的 ${items.length} 项${decision}？${warning}`)) return;
  busy.value = true;
  batchError.value = "";
  const failures: string[] = [];
  const completed: string[] = [];
  try {
    for (const [index, item] of items.entries()) {
      batchProgress.value = `审核中 ${index + 1} / ${items.length}`;
      try {
        await api(`/artifacts/${item.id}/review`, "POST", {
          approved,
          note: batchNote.value,
          expected_review: "pending",
        });
        completed.push(item.id);
      } catch (e: any) {
        failures.push(`${item.name}：${e.message}`);
      }
    }
    selectedReviewIds.value = selectedReviewIds.value.filter((id) => !completed.includes(id));
    try {
      await props.reload();
    } catch {
      failures.push("刷新失败，请手动刷新页面查看最新状态");
    }
    const result = `批量审核完成：成功 ${completed.length} 项，失败 ${items.length - completed.length} 项。`;
    batchProgress.value = result;
    if (failures.length) batchError.value = failures.join("\n");
    else emit("notify", result);
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <main class="workspace-content">
    <div class="workspace-heading">
      <div>
        <button class="text-button back-link" @click="emit('back')">
          <Icon name="left" :size="15" />所有项目
        </button>
        <div class="workspace-title">
          <h1>{{ project.title }}</h1>
          <span class="badge neutral"
            >{{ project.aspect_ratio }} · {{ project.episodes_count }} 集 ·
            {{ project.duration }}s / 集</span
          >
        </div>
        <p class="muted">{{ stageDescription[stage] }}</p>
      </div>
      <div class="button-row">
        <button class="btn secondary" @click="sourceOpen = true">
          <Icon name="book" :size="16" />原始内容</button
        ><button
          class="btn secondary"
          @click="control(project.paused ? 'resume' : 'pause')"
          :disabled="busy"
        >
          <Icon :name="project.paused ? 'play' : 'pause'" :size="16" />{{
            project.paused ? "恢复" : "暂停"
          }}</button
        ><button class="btn primary" :disabled="busy" @click="automate">
          <Icon :name="project.automation ? 'pause' : 'wand'" :size="17" />{{
            project.automation ? "停止自动推进" : "自动生产"
          }}
        </button>
      </div>
    </div>
    <div v-if="project.import_summary" class="notice archive-notice">
      <Icon name="folder" :size="22" />
      <div>
        <strong>{{
          project.library_curation
            ? `当前素材 ${(project.library_curation.counts.reference || 0) + (project.library_curation.counts.candidate || 0)} 份（选定参考 ${project.library_curation.counts.reference || 0}、待复核 ${project.library_curation.counts.candidate || 0}）`
            : `已导入 ${project.import_summary.total} 份历史素材`
        }}</strong>
        <p>
          {{ project.library_curation?.note || project.import_summary.note }}
        </p>
      </div>
      <button class="btn secondary" @click="emit('library', project.id)">
        {{ project.library_curation ? "浏览当前素材" : "浏览导入素材" }}
      </button>
    </div>
    <div v-if="project.paused || project.cancelled" class="notice warning">
      <Icon name="pause" />{{
        project.cancelled
          ? "项目已取消"
          : "项目已暂停；当前已提交的模型请求可能继续完成，后续任务等待恢复。"
      }}<button class="text-button" @click="control('resume')">恢复项目</button>
    </div>
    <ProjectActivity
      :project="project"
      :worker="worker"
      :reload="props.reload"
      @notify="emit('notify', $event)"
      @settings="emit('settings')"
      @jobs="emit('jobs')"
      @review="stage = $event === 'audio' ? 'video' : $event"
    />
    <div class="pipeline-steps">
      <button
        v-for="(s, i) in project.progress"
        :key="s.id"
        :class="[
          'pipeline-step',
          {
            active: stage === s.id,
            complete: s.total > 0 && s.done === s.total,
          },
        ]"
        @click="stage = s.id"
      >
        <span class="step-index"
          ><Icon
            v-if="s.total > 0 && s.done === s.total"
            name="check"
            :size="16"
          /><template v-else>{{
            String(i + 1).padStart(2, "0")
          }}</template></span
        ><span
          ><b>{{ s.name }}</b
          ><small>{{
            s.total ? s.done + " / " + s.total + " 已批准" : "等待上一步"
          }}</small></span
        ><Icon v-if="i < 5" class="step-arrow" name="chevron" :size="16" />
      </button>
    </div>
    <div v-if="error" class="inline-error workspace-error" role="alert">
      <Icon name="warning" />{{ error
      }}<button class="text-button" @click="emit('settings')">
        检查模型设置</button
      ><button class="icon-btn" aria-label="关闭错误" @click="error = ''">
        <Icon name="close" :size="16" />
      </button>
    </div>
    <section v-if="stage === 'requirements'" class="work-panel contract-panel">
      <div class="panel-toolbar">
        <div class="button-row">
          <span class="document-icon"><Icon name="file" /></span>
          <div>
            <b>production-requirements.md</b
            ><small class="block muted"
              >版本 {{ project.contract.revision }} · SHA-256
              {{ project.contract.hash.slice(0, 10) }}…</small
            >
          </div>
          <span
            :class="[
              'badge',
              project.contract.approved ? 'approved' : 'pending',
            ]"
            >{{ project.contract.approved ? "已批准" : "待确认" }}</span
          >
        </div>
        <div class="button-row">
          <div class="segmented">
            <button :class="{ selected: !preview }" @click="preview = false">
              编辑</button
            ><button :class="{ selected: preview }" @click="preview = true">
              预览
            </button>
          </div>
          <a
            :href="'/api/projects/' + project.id + '/contract.md'"
            download
            class="icon-btn"
            title="下载制作规范"
            ><Icon name="download" :size="18"
          /></a>
        </div>
      </div>
      <div class="contract-notice">
        <Icon
          name="link"
          :size="16"
        />批准后，参考图、关键帧和视频都会携带这份完整规范。修改规范将使旧下游批准状态失效。
      </div>
      <div v-if="preview" class="markdown-preview" v-html="html" />
      <textarea
        v-else
        v-model="markdown"
        class="contract-editor"
        aria-label="制作规范 Markdown"
        spellcheck="false"
      />
      <div class="panel-footer">
        <div class="button-row">
          <button
            class="btn secondary"
            :disabled="
              busy ||
              jobState('requirements')?.status === 'running' ||
              jobState('requirements')?.status === 'queued'
            "
            @click="generate('requirements', '', true)"
          >
            <Icon name="spark" :size="16" />AI 完善规范</button
          ><span class="muted small">{{
            dirty ? "有未保存的修改" : "先检查创作 Bible、身份设定与交付要求"
          }}</span>
        </div>
        <div class="button-row">
          <button
            class="btn secondary"
            :disabled="busy || !dirty"
            @click="saveContract"
          >
            保存新版本</button
          ><button
            v-if="!project.contract.approved"
            class="btn primary"
            :disabled="busy || dirty"
            @click="approveContract"
          >
            <Icon name="check" :size="16" />批准规范</button
          ><button v-else class="btn primary" @click="stage = 'plan'">
            进入分集分镜 <Icon name="right" :size="16" />
          </button>
        </div>
      </div>
    </section>
    <section v-else-if="stage === 'plan'" class="work-panel">
      <div class="panel-toolbar">
        <div>
          <h2>
            分集与分镜蓝图
            <span
              class="badge"
              :class="project.plan_approved ? 'approved' : 'pending'"
              >{{ project.plan_approved ? "已批准" : "待审核" }}</span
            >
          </h2>
          <span class="muted small">{{
            project.plan
              ? `${project.plan.episodes.length} 集 · ${shots.length} 个镜头 · ${project.plan.assets.length} 份素材`
              : "根据完整原文与规范拆分，生成后可逐镜编辑。"
          }}</span>
        </div>
        <div class="button-row">
          <button v-if="project.plan" class="btn secondary" @click="editJson">
            <Icon name="pencil" :size="16" />编辑完整计划</button
          ><button
            class="btn primary"
            :disabled="
              busy ||
              !project.contract.approved ||
              ['running', 'queued'].includes(jobState('plan')?.status || '')
            "
            @click="generate('plan', '', true)"
          >
            <Icon name="spark" :size="17" />{{
              project.plan ? "重新拆分" : "生成分集分镜"
            }}
          </button>
        </div>
      </div>
      <div v-if="!project.plan" class="stage-empty">
        <span class="empty-icon"><Icon name="layers" :size="34" /></span>
        <h3>让故事，变成可以拍摄的镜头。</h3>
        <p>
          {{
            project.contract.approved
              ? "制作规范已就绪，可以自动规划各集内容与素材库。"
              : "请先批准制作规范，再开始拆分分集与分镜。"
          }}
        </p>
        <button
          v-if="!project.contract.approved"
          class="btn secondary"
          @click="stage = 'requirements'"
        >
          前往制作规范
        </button>
      </div>
      <template v-else
        ><div class="episode-tabs">
          <button
            v-for="(ep, i) in project.plan.episodes"
            :key="ep.id"
            :class="{ active: episodeIndex === i }"
            @click="episodeIndex = Number(i)"
          >
            <span>EP {{ String(Number(i) + 1).padStart(2, "0") }}</span
            >{{ ep.title }}
          </button>
        </div>
        <div v-if="episode" class="episode-summary">
          <b>{{ episode.title }}</b>
          <p>{{ episode.synopsis }}</p>
          <span
            >来源章节：{{ episode.source_chapters.join("、") }} ·
            {{ episode.shots.length }} 镜 · 总计
            {{
              episode.shots.reduce((sum: number, s: any) => sum + s.duration, 0)
            }}
            秒</span
          >
        </div>
        <div class="shot-list">
          <article
            v-for="(shot, i) in episode?.shots"
            :key="shot.id"
            class="shot-row"
          >
            <span class="shot-number">{{
              String(Number(i) + 1).padStart(2, "0")
            }}</span>
            <div
              class="shot-thumbnail"
              @click="
                artifact('keyframe', shot.id) &&
                (detail = artifact('keyframe', shot.id)!)
              "
            >
              <img
                v-if="artifact('keyframe', shot.id)"
                :src="artifact('keyframe', shot.id)!.url"
                alt="分镜关键帧"
              /><Icon v-else name="clapper" :size="27" /><span
                >{{ shot.duration }}s</span
              >
            </div>
            <div class="shot-body">
              <h3>
                {{ shot.title }} <code>{{ shot.id }}</code>
              </h3>
              <p>{{ shot.description }}</p>
              <div v-if="shot.dialogue || shot.narration" class="shot-dialogue">
                {{ shot.dialogue ? "对白" : "旁白" }}：{{
                  shot.dialogue || shot.narration
                }}
              </div>
              <div class="shot-assets">
                <span v-for="id in shot.asset_ids" :key="id"
                  ><Icon name="link" :size="11" />{{
                    project.plan.assets.find((a: any) => a.id === id)?.name ||
                    id
                  }}</span
                >
              </div>
            </div>
            <button
              class="icon-btn"
              :aria-label="'编辑分镜 ' + shot.title"
              @click="openEdit(shot)"
            >
              <Icon name="pencil" :size="17" />
            </button>
          </article>
        </div>
        <div class="panel-footer">
          <span class="muted small"
            >批准前请核对原文覆盖、镜头时长、人物与场景绑定。</span
          ><button
            v-if="!project.plan_approved"
            class="btn primary"
            :disabled="
              busy ||
              !project.contract.approved ||
              project.plan_contract_hash !== project.contract.hash
            "
            @click="
              action(
                () =>
                  api('/projects/' + project.id + '/plan/approve', 'POST', {
                    revision: project.plan_revision,
                  }),
                '分集分镜已批准',
              )
            "
          >
            <Icon name="check" :size="16" />批准这份蓝图</button
          ><button v-else class="btn primary" @click="stage = 'reference'">
            建立素材参考 <Icon name="right" :size="16" />
          </button></div
      ></template>
    </section>
    <section v-else class="work-panel">
      <div class="panel-toolbar">
        <div>
          <h2>
            {{
              activeKind === "keyframe" ? "分镜关键帧" : stageText[activeKind]
            }}
          </h2>
          <p class="muted small">{{ stageDescription[activeKind] }}</p>
        </div>
        <div class="button-row">
          <a
            v-if="stage === 'render'"
            :href="'/api/projects/' + project.id + '/export.zip'"
            class="btn secondary"
            ><Icon name="download" :size="16" />完整资料包</a
          ><button
            class="btn primary"
            :disabled="busy || !readyForMedia || !entities.length"
            @click="generateAll"
          >
            <Icon name="spark" :size="16" />生成当前{{
              activeKind === "reference" ? "素材库" : "集"
            }}
          </button>
        </div>
      </div>
      <div class="media-filters">
        <div v-if="stage === 'reference'" class="segmented">
          <button
            :class="{ selected: imageTab === 'reference' }"
            @click="imageTab = 'reference'"
          >
            身份与素材</button
          ><button
            :class="{ selected: imageTab === 'keyframe' }"
            @click="imageTab = 'keyframe'"
          >
            逐镜关键帧
          </button>
        </div>
        <select
          v-if="
            activeKind !== 'reference' &&
            activeKind !== 'render' &&
            project.plan
          "
          v-model.number="episodeIndex"
          aria-label="选择集"
        >
          <option
            v-for="(ep, i) in project.plan.episodes"
            :key="ep.id"
            :value="i"
          >
            {{ ep.title }}
          </option></select
        ><span class="muted small"
          >已有结果 {{ mediaSummary.available }} / {{ entities.length }} ·
          待审核 {{ mediaSummary.pending }} · 已批准
          {{ mediaSummary.approved }}</span
        >
      </div>
      <div v-if="canBatchReview" class="batch-review" role="region" aria-label="批量审核">
        <div class="batch-review-controls">
          <button
            class="btn secondary"
            :disabled="busy || !reviewCandidates.length"
            @click="selectedReviewIds = reviewCandidates.map((item) => item.id)"
          >全选待审核</button>
          <button class="btn secondary" :disabled="busy || !selectedReviews.length" @click="selectedReviewIds = []">
            取消选择
          </button>
          <span class="muted small">已选择 {{ selectedReviews.length }} / {{ reviewCandidates.length }} 项待审核</span>
          <button class="btn primary" :disabled="busy || !selectedReviews.length" @click="reviewSelected(true)">
            批量批准
          </button>
          <button class="btn secondary" :disabled="busy || !selectedReviews.length" @click="reviewSelected(false)">
            批量标记返工
          </button>
        </div>
        <label class="batch-review-note small">审核备注（可选）
          <input v-model="batchNote" :disabled="busy" maxlength="2000" placeholder="将应用于本次选中的素材" />
        </label>
        <p class="muted small">仅审核当前页面选中的待审核素材；每项仍会校验当前版本与审核状态。</p>
        <p v-if="batchProgress" class="small" role="status">{{ batchProgress }}</p>
        <p v-if="batchError" class="inline-error batch-review-error" role="alert">{{ batchError }}</p>
      </div>
      <div v-if="!canBrowseMedia" class="stage-empty">
        <Icon name="layers" :size="34" />
        <h3>先确认创作蓝图。</h3>
        <p>请批准制作规范和分集分镜，再开始生成。</p>
        <button class="btn secondary" @click="stage = 'plan'">
          前往分集分镜
        </button>
      </div>
      <div v-else>
        <div v-if="canShowReference && !readyForMedia" class="notice">
          <Icon
            name="upload"
          />可以先导入角色、场景和道具参考图；批准规范与分镜后，才能审核参考图并生成后续画面。
        </div>
        <div class="media-grid" :class="{ renders: activeKind === 'render' }">
          <article
            v-for="entity in entities"
            :key="entity.id"
            class="media-card"
          >
            <button
              class="media-card-preview"
              :class="{ portrait: project.aspect_ratio === '9:16' }"
              :disabled="!artifact(activeKind, entity.id)"
              @click="detail = artifact(activeKind, entity.id) || null"
            >
              <template v-if="artifact(activeKind, entity.id)"
                ><img
                  v-if="['reference', 'keyframe'].includes(activeKind)"
                  :src="artifact(activeKind, entity.id)!.url"
                  :alt="entity.name || entity.title"
                  loading="lazy" /><video
                  v-else-if="activeKind !== 'audio'"
                  :src="artifact(activeKind, entity.id)!.url + '#t=0.1'"
                  preload="metadata"
                  muted
                  playsinline /><Icon v-else name="audio" :size="32" /><span
                  class="media-preview-overlay"
                  ><Icon
                    :name="
                      activeKind === 'video' || activeKind === 'render'
                        ? 'play'
                        : 'eye'
                    "
                    :size="26" /></span></template
              ><template v-else
                ><Icon
                  :name="
                    ['running', 'queued'].includes(
                      jobState(activeKind, entity.id)?.status || '',
                    )
                      ? 'loading'
                      : activeKind === 'video' || activeKind === 'render'
                        ? 'clapper'
                        : 'images'
                  "
                  :size="30"
                  :class="{
                    spinning:
                      jobState(activeKind, entity.id)?.status === 'running',
                  }"
                /><span>{{
                  statusText[jobState(activeKind, entity.id)?.status || ""] ||
                  "等待生成"
                }}</span></template
              ><span
                v-if="artifact(activeKind, entity.id)"
                :class="[
                  'media-review badge',
                  artifact(activeKind, entity.id)!.review,
                ]"
                >{{ statusText[artifact(activeKind, entity.id)!.review] }}</span
              >
            </button>
            <div class="media-card-info">
              <label
                v-if="canBatchReview && artifact(activeKind, entity.id)?.review === 'pending' && !artifact(activeKind, entity.id)?.archived"
                class="batch-review-select small"
              >
                <input v-model="selectedReviewIds" type="checkbox" :value="artifact(activeKind, entity.id)!.id" :disabled="busy" />
                选择审核 {{ entity.name || entity.title || entity.id }}
              </label>
              <h3>{{ entity.name || entity.title }}</h3>
              <ArtifactSource
                v-if="artifact(activeKind, entity.id)"
                :artifact="artifact(activeKind, entity.id)!"
              />
              <p>{{ entity.description || entity.synopsis }}</p>
              <div
                v-if="jobState(activeKind, entity.id)?.error"
                class="card-error"
              >
                {{ jobErrorMessage(jobState(activeKind, entity.id)) }}
              </div>
              <div
                v-if="activeKind === 'reference' && hasOlderReferenceSource(entity.id)"
                class="muted small"
              >
                {{
                  !referenceSource(entity.id)
                    ? "上传原图属于旧版本，请重新导入并批准"
                    : referenceSource(entity.id)?.review === "approved"
                      ? "重新生成将使用已批准的上传原图"
                      : "请先审核上传原图，才能以原图生成"
                }}
              </div>
              <div class="media-card-actions">
                <span class="muted small">{{
                  entity.duration
                    ? entity.duration + " 秒"
                    : entity.kind || entity.id
                }}</span
                ><button
                  v-if="artifact(activeKind, entity.id)"
                  class="text-button"
                  @click="detail = artifact(activeKind, entity.id)!"
                >
                  {{ readyForMedia ? "审核" : "预览" }}
                  <Icon name="right" :size="14" /></button
                ><button
                  class="text-button"
                  :disabled="
                    busy ||
                    !readyForMedia ||
                    ['queued', 'running'].includes(
                      jobState(activeKind, entity.id)?.status || '',
                    )
                  "
                  @click="generate(activeKind, entity.id, true)"
                >
                  <Icon name="refresh" :size="13" />{{
                    activeKind === "reference" && referenceSource(entity.id)
                      ? "基于原图生成"
                      : artifact(activeKind, entity.id)
                        ? "重新生成"
                        : "生成"
                  }}
                </button>
              </div>
              <button v-if="activeKind === 'keyframe'" class="text-button" :disabled="!readyForMedia" @click="openReferences(entity)">
                <Icon name="link" :size="13" />配置多角度参考与前镜
              </button>
              <button
                v-if="activeKind !== 'render'"
                class="text-button audio-generate"
                :disabled="busy || project.paused || project.cancelled"
                @click="chooseUpload(entity.id)"
              >
                <Icon name="upload" :size="13" />上传已有素材</button
              ><button
                v-if="
                  activeKind === 'video' &&
                  (entity.narration || entity.dialogue)
                "
                class="audio-generate text-button"
                :disabled="busy"
                @click="generate('audio', entity.id, true)"
              >
                <Icon name="audio" :size="13" />{{
                  artifact("audio", entity.id)
                    ? "查看 / 重新配音"
                    : "生成独立配音"
                }}</button
              ><button
                v-if="artifact('audio', entity.id) && activeKind === 'video'"
                class="text-button"
                @click="detail = artifact('audio', entity.id)!"
              >
                审核配音
              </button>
            </div>
          </article>
        </div>
      </div>
    </section>
    <input
      ref="fileInput"
      type="file"
      hidden
      :accept="
        activeKind === 'video'
          ? 'video/*'
          : activeKind === 'audio'
            ? 'audio/*'
            : 'image/png,image/jpeg,image/webp'
      "
      @change="upload"
    /><MediaDetail
      v-if="detail"
      :artifact="detail"
      :review-allowed="readyForMedia"
      @close="detail = null"
      @refresh="props.reload"
      @notify="emit('notify', $event)"
    />
    <div v-if="referenceShot" class="modal-backdrop" @click.self="referenceShot = null">
      <section class="modal edit-modal wide-modal" role="dialog" aria-modal="true" aria-label="配置镜头参考">
        <button class="icon-btn modal-close" aria-label="关闭" @click="referenceShot = null"><Icon name="close" /></button>
        <h2>配置 {{ referenceShot.title }} 的参考图</h2>
        <p class="muted small">已绑定素材的当前批准图自动加入。可额外选择已批准的同素材正面、侧面、服装、场景或道具细节；请逐张上传并批准。修改绑定会使相关关键帧及下游结果失效。</p>
        <div class="asset-checkboxes">
          <label v-for="item in eligibleReferences" :key="item.id">
            <input v-model="extraReferences" type="checkbox" :value="item.id" />
            <img :src="item.url" alt="参考图" style="width:72px;height:72px;object-fit:cover" />
            {{ project.plan.assets.find((asset: any) => asset.id === item.entity_id)?.name || item.entity_id }} · {{ item.id }}
          </label>
        </div>
        <p v-if="!eligibleReferences.length" class="muted">尚无已批准的绑定素材参考图。</p>
        <label class="field">已批准的前镜连续性参考<select v-model="previousShot"><option value="">不使用前镜</option><option v-for="shot in previousShots" :key="shot.id" :value="shot.id">{{ shot.title }} · {{ shot.id }}</option></select></label>
        <p class="muted small">网页渠道通常最多 10 张图；视觉质检还需额外上传候选图，超过渠道上限会暂停而不会遗漏参考。</p>
        <div v-if="error" class="inline-error">{{ error }}</div>
        <button class="btn primary" :disabled="busy" @click="saveReferences">保存参考绑定</button>
      </section>
    </div>
    <div v-if="editShot" class="modal-backdrop" @click.self="editShot = null">
      <section
        class="modal edit-modal"
        role="dialog"
        aria-modal="true"
        aria-label="编辑分镜"
      >
        <button
          class="icon-btn modal-close"
          aria-label="关闭分镜编辑"
          @click="editShot = null"
        >
          <Icon name="close" />
        </button>
        <h2>把这一镜，写得更清楚。</h2>
        <p class="muted small">
          修改会创建新的分镜版本，并使关联旧素材审批失效。
        </p>
        <label class="field">镜头名称<input v-model="editShot.title" /></label
        ><label class="field"
          >时长（秒）<input
            v-model.number="editShot.duration"
            type="number"
            min="1"
            max="30" /></label
        ><label class="field"
          >画面内容<textarea v-model="editShot.description" rows="3" /></label
        ><label class="field"
          >关键帧提示词<textarea
            v-model="editShot.image_prompt"
            rows="3"
          /></label
        ><label class="field"
          >动态视频提示词<textarea v-model="editShot.video_prompt" rows="3" />
        </label>
        <div class="form-grid">
          <label class="field"
            >对白<textarea v-model="editShot.dialogue" rows="2" /></label
          ><label class="field"
            >旁白<textarea v-model="editShot.narration" rows="2" />
          </label>
        </div>
        <div class="field">
          绑定素材
          <div class="asset-checkboxes">
            <label v-for="a in project.plan.assets" :key="a.id"
              ><input
                type="checkbox"
                :value="a.id"
                v-model="editShot.asset_ids"
              />{{ a.name }}</label
            >
          </div>
        </div>
        <div v-if="error" class="inline-error">{{ error }}</div>
        <button class="btn primary" :disabled="busy" @click="saveShot">
          保存分镜新版本
        </button>
      </section>
    </div>
    <div v-if="showJson" class="modal-backdrop" @click.self="showJson = false">
      <section
        class="modal edit-modal wide-modal"
        role="dialog"
        aria-modal="true"
        aria-label="编辑完整制作计划"
      >
        <button
          class="icon-btn modal-close"
          aria-label="关闭计划编辑"
          @click="showJson = false"
        >
          <Icon name="close" />
        </button>
        <h2>完整制作计划</h2>
        <p class="muted">
          可编辑集数内容、素材设定、参考图提示词及绑定关系。保存时将校验章节覆盖、唯一
          ID 与时长。
        </p>
        <textarea
          class="json-editor"
          v-model="planJson"
          aria-label="制作计划 JSON"
          rows="24"
        />
        <div v-if="error" class="inline-error">{{ error }}</div>
        <button class="btn primary" :disabled="busy" @click="saveJson">
          校验并保存新版本
        </button>
      </section>
    </div>
    <div
      v-if="sourceOpen"
      class="modal-backdrop"
      @click.self="sourceOpen = false"
    >
      <section
        class="modal edit-modal"
        role="dialog"
        aria-modal="true"
        aria-label="原始内容"
      >
        <button
          class="icon-btn modal-close"
          aria-label="关闭原文"
          @click="sourceOpen = false"
        >
          <Icon name="close" />
        </button>
        <h2>原始资料</h2>
        <p class="muted">
          {{ project.chapters?.length }} 个章节 ·
          {{ project.source.length.toLocaleString() }} 字
        </p>
        <pre class="source-preview">{{ project.source }}</pre>
      </section>
    </div>
  </main>
</template>
