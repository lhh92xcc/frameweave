<script setup lang="ts">
import { computed, ref } from "vue";
import { type Artifact, type Project } from "./api";
import Icon from "./Icon.vue";

const props = defineProps<{
  readiness: NonNullable<Project["reference_readiness"]>;
  assets: { id: string; name: string }[];
  artifacts: Artifact[];
  roleNames: Record<string, string>;
}>();
const emit = defineEmits<{ preview: [artifact: Artifact] }>();
const failedImages = ref(new Set<string>());
const groups = computed(() => {
  const artifacts = new Map(props.artifacts.filter((item) => !item.archived).map((item) => [item.id, item]));
  const names = new Map(props.assets.map((asset) => [asset.id, asset.name]));
  return Object.entries(props.readiness.assets).map(([id, requirements]) => ({
    id,
    name: names.get(id) || id,
    approved: requirements.views.filter((view) => view.approved).length,
    views: requirements.views.map((view) => {
      const artifact = artifacts.get(view.artifact_id || "");
      const state = !artifact ? "missing" : view.approved ? "approved"
        : artifact.stale ? "invalid" : artifact.review === "rejected" ? "rejected" : "pending";
      return {
        role: view.role,
        name: props.roleNames[view.role] || view.role,
        artifact,
        image: artifact?.thumbnail_url || artifact?.url || "",
        state,
        status: { missing: "缺少", approved: "已批准", invalid: "来源失效", rejected: "需返工", pending: "待审核" }[state],
      };
    }),
  }));
});
const approved = computed(() => groups.value.reduce((total, group) => total + group.approved, 0));
const total = computed(() => groups.value.reduce((total, group) => total + group.views.length, 0));
</script>

<template>
  <section class="reference-overview" aria-label="强制参考检查">
    <div class="reference-overview-heading">
      <div class="reference-overview-title">
        <h3>{{ readiness.mode === 'automatic' ? '项目资产母版' : '多视角参考' }}</h3>
        <span>{{ approved }} / {{ total }} 已批准</span>
      </div>
      <span class="reference-overview-hint">点击小图预览与审核</span>
    </div>
    <details class="reference-overview-rules">
      <summary>查看参考规则 <Icon name="down" :size="12" /></summary>
      <p v-if="readiness.mode === 'automatic'">每个角色、场景、道具先建立并批准母版，后续生图始终复用同一母版；可上传已有图片，也可生成首张定妆图。每镜自动带入资产母版及同集上一镜，逐资产对照规范质检，不通过会自动改词重生。达到次数或预算上限则暂停。换母版需明确选择，旧画面审批随之失效。</p>
      <p v-else>先上传并批准母版，自动生产会按规范补齐派生视角；生成图逐资产质检通过后自动批准，上传图需人工审核。必备视角齐全后才能生成剧情画面。每镜携带指定参考与同集上一镜，最多 {{ readiness.max_references }} 张参考卡；批量生成素材已停用。</p>
    </details>
    <div v-for="group in groups" :key="group.id" class="reference-overview-row">
      <div class="reference-overview-identity">
        <h4>{{ group.name }}</h4>
        <span :class="{ complete: group.approved === group.views.length }">{{ group.approved }} / {{ group.views.length }} 已批准</span>
      </div>
      <div class="reference-overview-grid">
        <button v-for="view in group.views" :key="view.role" class="reference-view"
          :class="view.state" :disabled="!view.artifact"
          :aria-label="`${group.name} · ${view.name} · ${view.status}`"
          :title="`${view.name} · ${view.status}`"
          @click="view.artifact && emit('preview', view.artifact)">
          <span class="reference-view-image">
            <img v-if="view.artifact && !failedImages.has(view.image)" :src="view.image" alt="" loading="lazy" decoding="async"
              @error="failedImages.add(view.image)" />
            <span v-else class="reference-view-placeholder">
              <Icon :name="view.artifact ? 'images' : 'plus'" :size="20" />
              <span>{{ view.artifact ? '预览未载入' : '待补充' }}</span>
            </span>
            <span v-if="view.artifact" class="reference-view-state">
              <Icon :name="view.state === 'approved' ? 'check' : view.state === 'pending' ? 'clock' : 'warning'" :size="10" />
              {{ view.status }}
            </span>
          </span>
          <span class="reference-view-name">{{ view.name }}</span>
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.reference-overview { margin: 0 25px 24px; padding: 18px 20px 4px; border: 1px solid #e9e4ee; border-radius: 12px; background: #fff; }
.reference-overview-heading, .reference-overview-title { display: flex; align-items: center; gap: 12px; }
.reference-overview-heading { justify-content: space-between; flex-wrap: wrap; gap: 6px 12px; }
.reference-overview-title h3 { margin: 0; color: #51435d; font-size: 14px; font-weight: 600; }
.reference-overview-title > span { padding: 3px 8px; border-radius: 20px; background: #f3f0f7; color: #79668c; font-size: 11px; }
.reference-overview-hint { color: #95899e; font-size: 11px; }
.reference-overview-rules { margin: 8px 0 2px; color: #95899e; font-size: 11px; }
.reference-overview-rules summary { display: inline-flex; align-items: center; gap: 4px; cursor: pointer; list-style: none; }
.reference-overview-rules summary::-webkit-details-marker { display: none; }
.reference-overview-rules[open] summary svg { transform: rotate(180deg); }
.reference-overview-rules p { max-width: 760px; margin: 8px 0 12px; line-height: 1.8; color: #7c7086; }
.reference-overview-row { display: grid; grid-template-columns: minmax(100px, 160px) minmax(0, 1fr); gap: 16px; padding: 16px 0; }
.reference-overview-row + .reference-overview-row { border-top: 1px solid #f0edf4; }
.reference-overview-identity { padding-top: 3px; min-width: 0; }
.reference-overview-identity h4 { margin: 0 0 6px; font-size: 12px; font-weight: 600; color: #65546f; overflow-wrap: anywhere; line-height: 1.6; }
.reference-overview-identity > span { color: #a096a6; font-size: 10px; }
.reference-overview-identity > .complete { color: #648674; }
.reference-overview-grid { display: grid; grid-template-columns: repeat(auto-fill, 96px); gap: 10px; min-width: 0; align-content: start; }
.reference-view { display: flex; flex-direction: column; gap: 6px; min-width: 0; padding: 0; text-align: center; background: transparent; color: #7c6d88; border: 0; border-radius: 7px; }
.reference-view:disabled { opacity: 1; cursor: default; }
.reference-view:focus-visible { outline: 2px solid #9575b4; outline-offset: 4px; }
.reference-view-image { display: block; position: relative; width: 100%; height: 76px; overflow: hidden; border: 1px solid #eae5ee; border-radius: 7px; background: #f8f7fa; transition: border-color .15s, box-shadow .15s; }
.reference-view:not(:disabled):hover .reference-view-image { border-color: #ab8bc4; box-shadow: 0 2px 8px #70558918; }
.reference-view-image img { display: block; width: 100%; height: 100%; object-fit: contain; }
.reference-view-name { font-size: 11px; line-height: 1.4; }
.reference-view-state { position: absolute; right: 3px; bottom: 3px; display: flex; align-items: center; gap: 2px; padding: 2px 4px; border-radius: 4px; background: #f5effbeF; color: #8c6aa8; font-size: 9px; line-height: 1.5; }
.approved .reference-view-state { background: #edf6f0f2; color: #4e7e61; }
.rejected .reference-view-state, .invalid .reference-view-state { background: #fff3e9f2; color: #a27249; }
.missing .reference-view-image { border-style: dashed; background: #fcfbfd; }
.missing .reference-view-name { color: #aaa0b3; }
.reference-view-placeholder { height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px; color: #beb3c7; font-size: 9px; }
@media (max-width: 680px) {
  .reference-overview { margin: 0 12px 16px; padding: 14px 12px 0; }
  .reference-overview-row { grid-template-columns: minmax(0, 1fr); gap: 10px; padding: 14px 0; }
  .reference-overview-identity { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 0; }
  .reference-overview-identity h4 { margin: 0; }
  .reference-overview-identity > span { flex-shrink: 0; }
  .reference-overview-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px 8px; }
}
</style>
