<script setup lang="ts">
import {
  computed,
  defineAsyncComponent,
  onMounted,
  ref,
  shallowRef,
  watch,
} from "vue";
import {
  api,
  libraryPrimary,
  statusText,
  stageText,
  type Artifact,
  type Project,
} from "./api";
import Icon from "./Icon.vue";
import ArtifactSource from "./ArtifactSource.vue";
import VoiceLibraryPanel from "./VoiceLibraryPanel.vue";
import { mediaNames } from "./experience";
const MediaDetail = defineAsyncComponent(() => import("./MediaDetail.vue"));
const emit = defineEmits<{ notify: [message: string]; projects: [] }>(),
  artifacts = shallowRef<Artifact[]>([]),
  filter = ref("all"),
  includeOld = ref(false),
  detail = ref<Artifact | null>(null),
  error = ref(""),
  loading = ref(true);
let pendingLoad: Promise<void> | null = null;
const props = defineProps<{ initialProject?: string; projects?: Project[] }>();
const projectFilter = ref(props.initialProject || ""),
  groupFilter = ref(""),
  query = ref(""),
  currentPage = ref(1);
const scope = ref("current"),
  episodeFilter = ref("");
const section = ref("media"),
  reviewFilter = ref("all");
function showKind(kind: string, approved = false) {
  filter.value = kind;
  reviewFilter.value = approved ? "approved" : "all";
  scope.value = "current";
  includeOld.value = false;
  query.value = "";
  groupFilter.value = "";
  episodeFilter.value = "";
}
const available = computed(() =>
  projectArtifacts.value.filter((a) => !a.stale && libraryPrimary(a)),
);
const mediaShortcuts = computed(() => [
  {
    kind: "render",
    title: "下载最终成片",
    description: "已检查通过、可以交付的视频",
    icon: "download",
    count: available.value.filter(
      (a) => a.kind === "render" && a.review === "approved" && !a.archived,
    ).length,
  },
  {
    kind: "reference",
    title: "查看人物与场景",
    description: "后续镜头沿用的固定参考图",
    icon: "images",
    count: available.value.filter((a) => a.kind === "reference").length,
  },
  {
    kind: "audio",
    title: "查找配音与录音",
    description: "台词、旁白和已有猫叫录音",
    icon: "audio",
    count: available.value.filter((a) => ["audio", "recording"].includes(a.kind)).length,
  },
]);
const selectedProject = computed(() =>
  props.projects?.find((p) => p.id === projectFilter.value),
);
const projectArtifacts = computed(() =>
  artifacts.value.filter(
    (a) => !projectFilter.value || a.project_id === projectFilter.value,
  ),
);
const hasCuration = computed(() =>
  projectArtifacts.value.some((a) => a.metadata?.curation),
);
const scopes = [
  ["current", "当前素材"],
  ["reference", "选定参考"],
  ["candidate", "待复核候选"],
  ["repair", "明确返修"],
  ["test", "动作试片"],
  ["history", "历史排除"],
  ["all", "全部档案"],
];
const scopeCounts = computed(() =>
  Object.fromEntries(
    scopes.map(([key]) => [
      key,
      projectArtifacts.value.filter(
        (a) =>
          key === "all" ||
          (key === "current"
            ? libraryPrimary(a)
            : a.metadata?.curation?.state === key),
      ).length,
    ]),
  ),
);
const episodes = computed(
  () =>
    selectedProject.value?.reference_catalog ||
    [
      ...new Set(
        projectArtifacts.value
          .map((a) => a.metadata?.curation?.episode)
          .filter(Boolean),
      ),
    ]
      .sort()
      .map((id) => ({ id, title: id, generated: 0, missing_slots: [] })),
);
const episodeInfo = computed(() =>
  episodes.value.find((e) => e.id === episodeFilter.value),
);
const episodeCounts = computed(() => {
  const counts: Record<string, number> = {};
  for (const a of projectArtifacts.value) {
    if (a.metadata?.curation?.episode !== episodeFilter.value) continue;
    const state = a.metadata.curation.state;
    counts[state] = (counts[state] || 0) + 1;
  }
  return counts;
});
const pageSize = 32;
const projects = computed(() => [
  ...new Map(
    artifacts.value.map((a) => [a.project_id, a.project_title]),
  ).entries(),
]);
const groups = computed(
  () =>
    [
      ...new Set(
        artifacts.value
          .filter(
            (a) => !projectFilter.value || a.project_id === projectFilter.value,
          )
          .map((a) => a.metadata?.imported?.group)
          .filter(Boolean),
      ),
    ] as string[],
);
const filtered = computed(() =>
  artifacts.value
    .filter(
      (a) =>
        (includeOld.value || !a.stale) &&
        (!hasCuration.value ||
          scope.value === "all" ||
          (scope.value === "current"
            ? libraryPrimary(a)
            : a.metadata?.curation?.state === scope.value)) &&
        (!episodeFilter.value ||
          a.metadata?.curation?.episode === episodeFilter.value) &&
        (filter.value === "all" || a.kind === filter.value ||
          (filter.value === "audio" && a.kind === "recording")) &&
        (reviewFilter.value === "all" ||
          (a.review === reviewFilter.value && !a.archived)) &&
        (!projectFilter.value || a.project_id === projectFilter.value) &&
        (!groupFilter.value ||
          a.metadata?.imported?.group === groupFilter.value) &&
        (!query.value ||
          [
            a.title,
            a.entity_id,
            a.project_title,
            a.metadata?.imported?.source_path,
            a.metadata?.imported?.shot_id,
            a.metadata?.curation?.display_title,
            a.metadata?.curation?.reason,
          ]
            .join(" ")
            .toLowerCase()
            .includes(query.value.toLowerCase())),
    )
    .sort(
      (a, b) =>
        (a.metadata?.curation?.order ?? 10000) -
        (b.metadata?.curation?.order ?? 10000),
    ),
);
const pageCount = computed(() =>
  Math.max(1, Math.ceil(filtered.value.length / pageSize)),
);
const visible = computed(() =>
  filtered.value.slice(
    (currentPage.value - 1) * pageSize,
    currentPage.value * pageSize,
  ),
);
watch(
  () => props.initialProject,
  (value) => (projectFilter.value = value || ""),
);
watch(
  [
    filter,
    reviewFilter,
    includeOld,
    projectFilter,
    groupFilter,
    query,
    scope,
    episodeFilter,
  ],
  () => (currentPage.value = 1),
);
watch(projectFilter, () => {
  groupFilter.value = "";
  episodeFilter.value = "";
});
async function load() {
  if (pendingLoad) return pendingLoad;
  const request = loadNow();
  pendingLoad = request;
  try {
    await request;
  } finally {
    if (pendingLoad === request) pendingLoad = null;
  }
}
async function loadNow() {
  loading.value = true;
  try {
    artifacts.value = await api("/artifacts");
    currentPage.value = Math.min(currentPage.value, pageCount.value);
    error.value = "";
  } catch (e: any) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}
onMounted(load);
</script>
<template>
  <main class="gallery-content">
    <div class="page-intro">
      <div>
        <div class="eyebrow">素材库</div>
        <h1>找成片、图片和声音，都在这里</h1>
        <p>想下载作品，先选“最终成片”；想查看人物形象，选“人物与场景参考”。</p>
      </div>
      <div class="button-row">
        <a
          v-if="selectedProject?.library_curation"
          class="btn secondary"
          :href="'/api/projects/' + projectFilter + '/export.zip'"
          >下载当前素材（含待复核）</a
        >
        <button class="btn secondary" :disabled="loading" @click="load">
          <Icon name="refresh" :size="16" />刷新素材
        </button>
      </div>
    </div>
    <div class="plain-tabs library-sections" aria-label="素材与声音">
      <button
        :class="{ selected: section === 'media' }"
        :aria-pressed="section === 'media'"
        @click="section = 'media'"
      >
        图片、视频与录音
      </button>
      <button
        :class="{ selected: section === 'voices' }"
        :aria-pressed="section === 'voices'"
        @click="section = 'voices'"
      >
        已保存的角色声音
      </button>
    </div>
    <VoiceLibraryPanel v-if="section === 'voices'" />
    <template v-else>
      <div class="library-shortcuts">
        <button
          v-for="item in mediaShortcuts"
          :key="item.kind"
          @click="showKind(item.kind, item.kind === 'render')"
        >
          <Icon :name="item.icon" :size="23" /><span
            ><strong
              >{{ item.title }} <b>{{ item.count }}</b></strong
            ><small>{{ item.description }}</small></span
          ><Icon name="right" :size="16" />
        </button>
      </div>
      <div v-if="hasCuration" class="curation-summary notice">
        <div>
          <strong>当前素材包含已选参考和等待检查的图片</strong>
          <p>
            当前 {{ scopeCounts.current }} 份：{{ scopeCounts.reference }}
            份选定参考、
            {{ scopeCounts.candidate }}
            份待复核候选。等待检查的图片会保留，但还不能直接用于制作。
            需重做、试片和旧档案可以在下方分类查找。
          </p>
        </div>
      </div>
      <div v-if="hasCuration" class="curation-tabs" aria-label="素材用途">
        <button
          v-for="[key, label] in scopes"
          :key="key"
          :class="['btn', scope === key ? 'primary' : 'secondary']"
          @click="scope = key"
        >
          {{ label }} <span>{{ scopeCounts[key] }}</span>
        </button>
      </div>
      <div class="archive-filters">
        <label
          >项目<select v-model="projectFilter" aria-label="按项目筛选素材">
            <option value="">全部项目</option>
            <option v-for="[id, title] in projects" :key="id" :value="id">
              {{ title }}
            </option>
          </select></label
        >
        <label v-if="episodes.length"
          >集数<select v-model="episodeFilter" aria-label="按集数筛选素材">
            <option value="">全部集数 / 人物场景基准</option>
            <option
              v-for="episode in episodes"
              :key="episode.id"
              :value="episode.id"
            >
              {{ episode.id }} · {{ episode.title }}（已生成
              {{ episode.generated }} / 10）
            </option>
          </select></label
        >
        <label v-if="groups.length"
          >来源分组<select v-model="groupFilter" aria-label="按来源分组筛选">
            <option value="">全部来源</option>
            <option v-for="group in groups" :key="group">{{ group }}</option>
          </select></label
        >
        <label
          >搜索<input
            v-model="query"
            aria-label="搜索历史素材"
            placeholder="输入名称、项目或镜头编号"
        /></label>
        <span class="muted">{{ filtered.length }} 份素材</span>
      </div>
      <p v-if="episodeInfo" class="notice">
        {{ episodeInfo.id }}：生成过 {{ episodeInfo.generated }} / 10
        个镜头候选。
        <span v-if="episodeInfo.missing_slots.length"
          >未生成：{{ episodeInfo.missing_slots.join("、") }}。</span
        >
        选定参考 {{ episodeCounts.reference || 0 }} 张，待复核
        {{ episodeCounts.candidate || 0 }} 张，明确返修
        {{ episodeCounts.repair || 0 }} 张。当前列表按所选用途筛选。
        <button
          v-if="episodeCounts.repair && scope !== 'repair'"
          class="btn secondary"
          @click="scope = 'repair'"
        >
          查看本集返修
        </button>
      </p>
      <div class="media-filters">
        <div class="segmented">
          <button
            v-for="kind in [
              'all',
              'reference',
              'keyframe',
              'video',
              'audio',
              'render',
            ]"
            :key="kind"
            :class="{ selected: filter === kind }"
            @click="filter = kind"
          >
            {{ mediaNames[kind] }}
          </button>
        </div>
        <label class="inline-checkbox"
          ><input type="checkbox" v-model="includeOld" />包含过期版本</label
        >
        <label class="sort-control"
          >检查状态<select
            v-model="reviewFilter"
            aria-label="按检查状态筛选素材"
          >
            <option value="all">全部状态</option>
            <option value="approved">检查通过</option>
            <option value="pending">等待检查</option>
            <option value="rejected">需要重做</option>
          </select></label
        >
      </div>
      <p class="page-explainer media-help">
        “镜头画面”是一镜开始时的静态图，“镜头视频”是单个片段；“最终成片”是已经拼接好的整集视频。旧版素材可预览下载，不能直接用于当前制作。
      </p>
      <div v-if="error" class="inline-error">{{ error }}</div>
      <div
        v-if="loading && !artifacts.length"
        class="panel-loading"
        role="status"
      >
        正在加载素材…
      </div>
      <div v-else-if="!filtered.length" class="work-panel stage-empty">
        <Icon name="images" :size="36" />
        <h3>
          {{
            filter === "render" ? "还没有符合条件的成片" : "当前筛选下没有素材"
          }}
        </h3>
        <p>
          {{
            filter === "render"
              ? "制作完成并检查通过后，成片会出现在这里。可以打开项目查看进度。"
              : "可以切换项目、素材类型或检查状态。在项目中生成和导入的素材会自动收进这里。"
          }}
        </p>
        <div class="button-row">
          <button class="btn secondary" @click="showKind('all')">
            查看全部当前素材</button
          ><button class="text-button" @click="emit('projects')">
            打开我的项目
          </button>
        </div>
      </div>
      <div v-else class="media-grid">
        <article v-for="a in visible" :key="a.id" class="media-card">
          <button
            class="media-card-preview"
            :aria-label="
              '预览 ' +
              (a.metadata?.curation?.display_title || a.title || a.entity_id)
            "
            @click="detail = a"
          >
            <img
              v-if="a.mime.startsWith('image/')"
              :src="a.thumbnail_url || a.url"
              :alt="a.title || a.entity_id"
              loading="lazy"
              decoding="async"
            /><video
              v-else-if="a.mime.startsWith('video/')"
              :src="a.url + '#t=0.1'"
              preload="metadata"
              muted
            /><Icon v-else name="audio" :size="32" /><span
              :class="['media-review badge', a.stale ? 'stale' : a.review]"
              >{{
                a.metadata?.curation?.label ||
                (a.archived
                  ? "历史导入"
                  : a.stale
                    ? "已过期"
                    : statusText[a.review])
              }}</span
            ><span class="media-preview-overlay"
              ><Icon name="eye" :size="26"
            /></span>
          </button>
          <div class="media-card-info">
            <h3 :title="a.title || a.entity_id">
              {{
                a.metadata?.curation?.display_title || a.title || a.entity_id
              }}
            </h3>
            <ArtifactSource :artifact="a" />
            <p>
              {{ a.project_title }} ·
              {{ mediaNames[a.kind] || stageText[a.kind] }}
            </p>
            <span v-if="a.storage" class="muted small"
              >腾讯云 COS · 原文件已校验</span
            >
            <span class="muted small" v-if="a.archived"
              >{{
                a.metadata?.curation?.purpose || a.metadata?.imported?.group
              }}
              ·
              {{
                a.metadata?.imported?.shot_id || a.metadata?.imported?.role
              }}</span
            >
            <span class="muted small" v-else
              >制作要求第 {{ a.contract_revision }} 版</span
            >
            <a
              class="text-button media-download"
              :href="a.url + '?download=true'"
              download
              ><Icon name="download" :size="15" />{{
                a.kind === "render" ? "下载 MP4" : "下载原文件"
              }}</a
            >
          </div>
        </article>
      </div>
      <div v-if="filtered.length" class="archive-pagination">
        <button
          class="btn secondary"
          :disabled="currentPage === 1"
          @click="currentPage--"
        >
          上一页
        </button>
        <span
          >第 {{ currentPage }} / {{ pageCount }} 页 · 每页
          {{ pageSize }} 份</span
        >
        <button
          class="btn secondary"
          :disabled="currentPage >= pageCount"
          @click="currentPage++"
        >
          下一页
        </button>
      </div>
      <MediaDetail
        v-if="detail"
        :artifact="detail"
        @close="detail = null"
        @refresh="load"
        @notify="emit('notify', $event)"
      />
    </template>
  </main>
</template>
