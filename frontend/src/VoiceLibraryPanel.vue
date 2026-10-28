<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api } from "./api";
import type { SavedVoice } from "./audio";

const voices = ref<SavedVoice[]>([]), error = ref("");
onMounted(async () => {
  try { voices.value = (await api("/audio/voice-library")).voices; }
  catch (e: any) { error.value = e.message; }
});
</script>
<template>
  <section class="work-panel voice-library" aria-label="固定音色资源库">
    <div class="library-heading">
      <div><h2>固定音色资源库 · {{ voices.length }}</h2>
        <p class="muted">已激活音色跨项目复用，声线 ID 锁定保存。在项目“角色配音”中选择使用。</p></div>
      <a v-if="voices.length" class="btn secondary" href="/api/audio/voice-library/export.zip">备份音色资源库</a>
    </div>
    <p class="notice">不再新增定制或克隆音色；新增角色只用免音色费的系统音色或已有录音。已激活音色可继续复用，TTS 字符费另计。</p>
    <p v-if="error" class="inline-error">{{ error }}</p>
    <p v-else-if="!voices.length" class="muted small">暂无保存的定制音色。</p>
    <div class="voice-grid">
      <article v-for="voice in voices" :key="voice.id" class="saved-voice">
        <h3>{{ voice.name }}</h3>
        <p class="muted small">已锁定 · 已激活 · MiniMax</p>
        <code>{{ voice.voice_id }}</code>
        <audio controls preload="none" :src="voice.preview_url" :aria-label="voice.name + '资源试听'" />
        <p class="muted small">{{ voice.review === 'approved' ? '试听已批准' : '试听待听审' }} · 播放已有音频不产生生成费用</p>
      </article>
    </div>
    <p v-if="voices.length" class="muted small">已记录首次音色费用；生成新台词仍按字符计费。需使用原 MiniMax 账号，音色不可用时停止，不自动创建替代音色。备份保存 ID、参数、试听和来源记录。</p>
  </section>
</template>
<style scoped>
.voice-library { margin-bottom: 24px; padding: 24px; }
.library-heading { display: flex; align-items: start; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.voice-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 250px), 1fr)); gap: 16px; }
.saved-voice { min-width: 0; padding: 16px; border: 1px solid var(--border, #ddd); border-radius: 12px; }
.saved-voice code { display: block; overflow-wrap: anywhere; font-size: 12px; }
.saved-voice audio { display: block; width: 100%; margin-top: 14px; }
</style>
