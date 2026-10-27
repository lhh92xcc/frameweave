<script setup lang="ts">
import { computed } from "vue";
import type { Artifact } from "./api";

const props = defineProps<{ artifact: Artifact }>();
const route = computed(() => props.artifact.route || {});
const source = computed(() => {
  const provider = route.value.provider;
  if (props.artifact.archived || provider === "legacy-import") return "历史导入 · 原生成渠道未知";
  if (provider === "user-upload") return "用户上传 · 非模型生成";
  if (provider === "ai-video-generation" && route.value.model === "ffmpeg") return "本地合成 · FFmpeg";
  if (typeof provider !== "string" || !provider.trim()) return "生成渠道未知";
  const names: Record<string, string> = {
    gemini: "Gemini 网页",
    seedream: "Seedream（火山方舟）",
    cloud: "云模型",
  };
  return names[provider] || provider;
});
const model = computed(() => {
  const provider = route.value.provider;
  const value = route.value.model;
  if (props.artifact.archived || ["legacy-import", "user-upload", "ai-video-generation"].includes(provider))
    return "";
  return typeof value === "string" && value.trim() && value !== "default" ? value : "";
});
const paid = computed(() =>
  !props.artifact.archived && route.value.provider !== "user-upload" && route.value.paid === true,
);
</script>

<template>
  <div class="artifact-source" aria-label="素材生成来源">
    <span><strong>来源：</strong>{{ source }}<template v-if="paid"> · 付费</template></span>
    <span v-if="model" class="artifact-source-model"><strong>记录模型：</strong>{{ model }}</span>
  </div>
</template>
