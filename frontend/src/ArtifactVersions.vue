<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { statusText, type Artifact } from "./api";
import Icon from "./Icon.vue";

const props = defineProps<{
  artifacts: Artifact[];
  selected: Artifact;
  latestId?: string;
  roleNames: Record<string, string>;
}>();
const emit = defineEmits<{ select: [artifact: Artifact] }>();
const strip = ref<HTMLElement>();
const index = computed(() => props.artifacts.findIndex((item) => item.id === props.selected.id));
function role(item: Artifact) {
  const value = item.metadata?.reference_role;
  return props.roleNames[value] || (value ? value : item.kind === "reference" ? "标准参考" : "关键帧");
}
function label(item: Artifact, position: number) {
  return `查看第 ${position + 1} 张 · ${role(item)} · ${item.stale ? "已过期 · " : ""}${statusText[item.review] || item.review}`;
}
function move(offset: number) {
  const item = props.artifacts[index.value + offset];
  if (item) emit("select", item);
}
watch([() => props.selected.id, index], async () => {
  await nextTick();
  const container = strip.value;
  const button = container?.querySelector<HTMLElement>('[aria-pressed="true"]');
  if (!container || !button) return;
  const bounds = container.getBoundingClientRect();
  const selected = button.getBoundingClientRect();
  if (selected.left < bounds.left) container.scrollLeft += selected.left - bounds.left;
  else if (selected.right > bounds.right) container.scrollLeft += selected.right - bounds.right;
}, { immediate: true });
</script>

<template>
  <div class="artifact-versions" role="group" aria-label="图片版本">
    <div class="versions-toolbar">
      <span class="small">共 {{ artifacts.length }} 张 · 正在查看 {{ index + 1 }} / {{ artifacts.length }}</span>
      <div class="version-controls">
        <button class="icon-btn" aria-label="上一张图片" :disabled="index <= 0" @click="move(-1)"><Icon name="left" :size="15" /></button>
        <button class="icon-btn" aria-label="下一张图片" :disabled="index >= artifacts.length - 1" @click="move(1)"><Icon name="right" :size="15" /></button>
      </div>
    </div>
    <div ref="strip" class="versions-strip">
      <button v-for="(item, position) in artifacts" :key="item.id" class="version-thumbnail"
        :class="{ selected: selected.id === item.id }" :aria-pressed="selected.id === item.id"
        :aria-label="label(item, position)" :title="label(item, position)" @click="emit('select', item)">
        <span class="thumbnail-image">
          <img :src="item.thumbnail_url || item.url" alt="" loading="lazy" decoding="async" />
          <span v-if="item.id === latestId" class="latest-label">最新</span>
        </span>
        <span class="thumbnail-role">{{ role(item) }}</span>
        <span :class="['thumbnail-status', item.stale ? 'stale' : item.review]">{{ item.stale ? '已过期' : statusText[item.review] }}</span>
      </button>
    </div>
    <p v-if="selected.stale" class="version-notice small">此图已过期，仅供查看和下载；请以当前有效版本审核、生成。</p>
    <p v-else class="version-notice small">点击缩略图切换，点击大图查看原图。浏览图片不会改变生成绑定。</p>
  </div>
</template>

<style scoped>
.artifact-versions { padding: 12px 16px; border-bottom: 1px solid #eadef3; min-width: 0; }
.versions-toolbar, .version-controls { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
.versions-toolbar { margin-bottom: 8px; color: #77628b; }
.version-controls { flex-shrink: 0; }
.version-controls .icon-btn { width: 28px; height: 28px; }
.versions-strip { display: flex; gap: 8px; overflow-x: auto; padding: 3px 2px 8px; position: relative; scrollbar-width: thin; }
.version-thumbnail { flex: 0 0 88px; display: flex; flex-direction: column; gap: 4px; padding: 4px; border: 2px solid transparent; border-radius: 8px; background: #faf7fc; color: #6e5787; text-align: left; }
.version-thumbnail.selected { border-color: #9871b9; background: #f1e8f9; }
.version-thumbnail:focus-visible { outline: 2px solid #6e488d; outline-offset: 1px; }
.thumbnail-image { position: relative; display: block; width: 100%; height: 58px; overflow: hidden; border-radius: 4px; background: #f0e9f6; }
.thumbnail-image img { width: 100%; height: 100%; object-fit: contain; }
.latest-label { position: absolute; top: 2px; right: 2px; padding: 1px 4px; border-radius: 3px; background: #ffffffeb; font-size: 10px; }
.thumbnail-role { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; font-size: 11px; }
.thumbnail-status { font-size: 10px; color: #9677ab; }
.thumbnail-status.approved { color: #4e815c; }
.thumbnail-status.rejected { color: #a95161; }
.thumbnail-status.stale { color: #8c8591; }
.version-notice { margin: 8px 0 0; color: #8a7997; line-height: 1.6; }
@media (max-width: 680px) {
  .version-controls .icon-btn { width: 44px; height: 44px; }
}
</style>
