<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { api, statusText, stageText, type Artifact } from "./api";
import Icon from "./Icon.vue";
import MediaDetail from "./MediaDetail.vue";
const emit = defineEmits<{ notify: [message: string] }>(),
  artifacts = ref<Artifact[]>([]),
  filter = ref("all"),
  includeOld = ref(false),
  detail = ref<Artifact | null>(null),
  error = ref("");
const filtered = computed(() =>
  artifacts.value.filter(
    (a) =>
      (includeOld.value || !a.stale) &&
      (filter.value === "all" || a.kind === filter.value),
  ),
);
async function load() {
  try {
    artifacts.value = await api("/artifacts");
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
      <button class="btn secondary" @click="load">
        <Icon name="refresh" :size="16" />刷新素材
      </button>
    </div>
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
      <h3>每一份创作，都值得被留下。</h3>
      <p>在项目中生成参考图或视频后，它们会自动收进这里。</p>
    </div>
    <div v-else class="media-grid">
      <article v-for="a in filtered" :key="a.id" class="media-card">
        <button class="media-card-preview" @click="detail = a">
          <img
            v-if="a.mime.startsWith('image/')"
            :src="a.url"
            :alt="a.entity_id"
            loading="lazy"
          /><video
            v-else-if="a.mime.startsWith('video/')"
            :src="a.url + '#t=0.1'"
            preload="metadata"
            muted
          /><Icon v-else name="audio" :size="32" /><span
            :class="['media-review badge', a.stale ? 'stale' : a.review]"
            >{{ a.stale ? "已过期" : statusText[a.review] }}</span
          ><span class="media-preview-overlay"
            ><Icon name="eye" :size="26"
          /></span>
        </button>
        <div class="media-card-info">
          <h3>{{ a.entity_id }}</h3>
          <p>{{ a.project_title }} · {{ stageText[a.kind] }}</p>
          <span class="muted small"
            >规范 v{{ a.contract_revision }} · {{ a.route?.provider }}</span
          >
        </div>
      </article>
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
