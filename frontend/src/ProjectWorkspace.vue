<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { marked } from "marked";
import DOMPurify from "dompurify";
import Icon from "./Icon.vue";
import MediaDetail from "./MediaDetail.vue";
import { api, statusText, stageText, type Artifact, type Project } from "./api";
const props = defineProps<{ project: Project; reload: () => Promise<void> }>(),
  emit = defineEmits<{
    notify: [message: string];
    settings: [];
    back: [];
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
  editShot = ref<any>(null),
  planJson = ref(""),
  showJson = ref(false),
  sourceOpen = ref(false);
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
  episode = computed(() => p.value.plan?.episodes[episodeIndex.value]),
  activeJob = computed(() => p.value.jobs.find((j) => j.status === "running"));
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
function artifact(kind: string, id: string) {
  return p.value.artifacts.find(
    (a) => a.kind === kind && a.entity_id === id && !a.stale,
  );
}
function jobState(kind: string, id = "") {
  return p.value.jobs.find((j) => j.stage === kind && j.entity_id === id);
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
            ? `当前选定 ${project.library_curation.counts.reference || 0} 份参考`
            : `已导入 ${project.import_summary.total} 份历史素材`
        }}</strong>
        <p>
          {{ project.library_curation?.note || project.import_summary.note }}
        </p>
      </div>
      <button class="btn secondary" @click="emit('library', project.id)">
        {{ project.library_curation ? "浏览当前参考" : "浏览导入素材" }}
      </button>
    </div>
    <div v-if="project.paused || project.cancelled" class="notice warning">
      <Icon name="pause" />{{
        project.cancelled
          ? "项目已取消"
          : "项目已暂停；当前已提交的模型请求可能继续完成，后续任务等待恢复。"
      }}<button class="text-button" @click="control('resume')">恢复项目</button>
    </div>
    <div v-if="activeJob" class="active-job-banner">
      <Icon name="loading" class="spinning" :size="17" /><span
        >{{ stageText[activeJob.stage] }}正在生成
        <small>{{ activeJob.entity_id }}</small></span
      ><span>结果会自动出现在工作台</span>
    </div>
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
          >{{ entities.length }} 项 · 生成后逐项审核</span
        >
      </div>
      <div v-if="!readyForMedia" class="stage-empty">
        <Icon name="layers" :size="34" />
        <h3>先确认创作蓝图。</h3>
        <p>请批准制作规范和分集分镜，再开始生成。</p>
        <button class="btn secondary" @click="stage = 'plan'">
          前往分集分镜
        </button>
      </div>
      <div
        v-else
        class="media-grid"
        :class="{ renders: activeKind === 'render' }"
      >
        <article v-for="entity in entities" :key="entity.id" class="media-card">
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
            <h3>{{ entity.name || entity.title }}</h3>
            <p>{{ entity.description || entity.synopsis }}</p>
            <div
              v-if="jobState(activeKind, entity.id)?.error"
              class="card-error"
            >
              {{ jobState(activeKind, entity.id)?.error?.message }}
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
                审核 <Icon name="right" :size="14" /></button
              ><button
                class="text-button"
                :disabled="
                  busy ||
                  ['queued', 'running'].includes(
                    jobState(activeKind, entity.id)?.status || '',
                  )
                "
                @click="generate(activeKind, entity.id, true)"
              >
                <Icon name="refresh" :size="13" />{{
                  artifact(activeKind, entity.id) ? "重新生成" : "生成"
                }}
              </button>
            </div>
            <button
              v-if="activeKind !== 'render'"
              class="text-button audio-generate"
              :disabled="busy"
              @click="chooseUpload(entity.id)"
            >
              <Icon name="upload" :size="13" />上传已有素材</button
            ><button
              v-if="
                activeKind === 'video' && (entity.narration || entity.dialogue)
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
      @close="detail = null"
      @refresh="props.reload"
      @notify="emit('notify', $event)"
    />
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
