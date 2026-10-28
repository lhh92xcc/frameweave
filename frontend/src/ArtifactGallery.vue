<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import {
  api,
  libraryPrimary,
  statusText,
  stageText,
  type Artifact,
  type Project,
} from "./api";
import Icon from "./Icon.vue";
import MediaDetail from "./MediaDetail.vue";
import ArtifactSource from "./ArtifactSource.vue";
import VoiceLibraryPanel from "./VoiceLibraryPanel.vue";
const emit = defineEmits<{ notify: [message: string] }>(),
  artifacts = ref<Artifact[]>([]),
  filter = ref("all"),
  includeOld = ref(false),
  detail = ref<Artifact | null>(null),
  error = ref("");
const props = defineProps<{ initialProject?: string; projects?: Project[] }>();
const projectFilter = ref(props.initialProject || ""),
  groupFilter = ref(""),
  query = ref(""),
  currentPage = ref(1);
const scope = ref("current"),
  episodeFilter = ref("");
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
        (filter.value === "all" || a.kind === filter.value) &&
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
  [filter, includeOld, projectFilter, groupFilter, query, scope, episodeFilter],
  () => (currentPage.value = 1),
);
watch(projectFilter, () => {
  groupFilter.value = "";
  episodeFilter.value = "";
});
async function load() {
  try {
    artifacts.value = await api("/artifacts");
    currentPage.value = Math.min(currentPage.value, pageCount.value);
    error.value = "";
  } catch (e: any) {
    error.value = e.message;
  }
}
onMounted(load);
</script>
<template>
  <main class="gallery-content">
    <div class="page-intro">
      <div>
        <div class="eyebrow">YOUR VISUAL LIBRARY</div>
        <h1>让每一份素材，都有来处。</h1>
        <p>参考图、关键帧、视频与成片，按项目和规范版本留存。</p>
      </div>
      <div class="button-row">
        <a
          v-if="selectedProject?.library_curation"
          class="btn secondary"
          :href="'/api/projects/' + projectFilter + '/export.zip'"
          >下载当前素材（含待复核）</a
        >
        <button class="btn secondary" @click="load">
          <Icon name="refresh" :size="16" />刷新素材
        </button>
      </div>
    </div>
    <VoiceLibraryPanel />
    <div v-if="hasCuration" class="curation-summary notice">
      <div>
        <strong>当前制作素材：选定参考与待复核候选</strong>
        <p>
          当前 {{ scopeCounts.current }} 份：{{ scopeCounts.reference }}
          份选定参考、
          {{ scopeCounts.candidate }}
          份待复核候选。待复核不等于废弃，也不等于制作批准；
          {{ scopeCounts.repair }} 份明确返修另列，旧试片与历史文件分开保存。
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
          placeholder="文件名、镜头编号、原始路径…"
      /></label>
      <span class="muted">{{ filtered.length }} 份素材</span>
    </div>
    <p v-if="episodeInfo" class="notice">
      {{ episodeInfo.id }}：生成过 {{ episodeInfo.generated }} / 10 个镜头候选。
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
          {{ kind === "all" ? "全部素材" : stageText[kind] }}
        </button>
      </div>
      <label class="inline-checkbox"
        ><input type="checkbox" v-model="includeOld" />包含过期版本</label
      >
    </div>
    <div v-if="error" class="inline-error">{{ error }}</div>
    <div v-if="!filtered.length" class="work-panel stage-empty">
      <Icon name="images" :size="36" />
      <h3>
        {{ hasCuration ? "当前筛选下没有素材" : "每一份创作，都值得被留下。" }}
      </h3>
      <p>
        {{
          hasCuration
            ? "可切换集数或用途查看。当前素材包含待复核候选；明确返修单列，历史版本默认排除。"
            : "在项目中生成参考图或视频后，它们会自动收进这里。"
        }}
      </p>
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
            {{ a.metadata?.curation?.display_title || a.title || a.entity_id }}
          </h3>
          <ArtifactSource :artifact="a" />
          <p>{{ a.project_title }} · {{ stageText[a.kind] }}</p>
          <span v-if="a.storage" class="muted small"
            >腾讯云 COS · 原文件已校验</span
          >
          <span class="muted small" v-if="a.archived"
            >{{ a.metadata?.curation?.purpose || a.metadata?.imported?.group }}
            ·
            {{
              a.metadata?.imported?.shot_id || a.metadata?.imported?.role
            }}</span
          >
          <span class="muted small" v-else
            >规范 v{{ a.contract_revision }} · {{ a.route?.provider }}</span
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
  </main>
</template>
