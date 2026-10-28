<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { api, requiresVerification, type Project } from "./api";
import { emotionOptions, speechDefaults, starterVoices, type SavedVoice } from "./audio";
import Icon from "./Icon.vue";

const props = defineProps<{ project: Project; reload: () => Promise<void> }>();
const emit = defineEmits<{ close: []; settings: []; notify: [message: string] }>();
const voices = ref<Record<string, any>>({}), speakers = ref<Record<string, string>>({});
const catalogue = ref(starterVoices), settings = ref<any>(null), busy = ref(false), error = ref("");
const library = ref<SavedVoice[]>([]), libraryError = ref("");
const characters = computed(() => [
  ...(props.project.plan?.assets || []).filter((a: any) => a.kind === "character"),
  { id: "narrator", name: "旁白" },
]);
const shots = computed(() => (props.project.plan?.episodes || [])
  .flatMap((ep: any) => ep.shots).filter((shot: any) => shot.dialogue || shot.narration));
const ready = computed(() => settings.value?.audio_mode === "cloud" &&
  settings.value?.audio.provider === "minimax" && settings.value.audio.configured &&
  settings.value.allow_paid && props.project.contract.approved && props.project.plan_approved &&
  !props.project.paused && !props.project.cancelled);
const saved = ref("");
const dirty = computed(() => JSON.stringify({ voices: voices.value, speakers: speakers.value }) !== saved.value);
function sampleShot(speaker: string) {
  return shots.value.find((shot: any) => speakers.value[shot.id] === speaker);
}
function sampleArtifact(speaker: string) {
  const shot = sampleShot(speaker);
  return props.project.artifacts.find((a) => a.kind === "audio" && a.entity_id === shot?.id && !a.stale);
}
onMounted(async () => {
  try {
    settings.value = await api("/settings");
    for (const role of characters.value) {
      voices.value[role.id] = { ...speechDefaults,
        voice_id: settings.value.audio.provider === "minimax" ? settings.value.voice : "male-qn-qingse",
        ...props.project.audio_cast?.voices?.[role.id] };
    }
    speakers.value = { ...(props.project.audio_cast?.speakers || {}) };
    saved.value = JSON.stringify({ voices: voices.value, speakers: speakers.value });
  } catch (e: any) { error.value = e.message; }
  try { library.value = (await api("/audio/voice-library")).voices; }
  catch (e: any) { libraryError.value = e.message; }
});
function useLibrary(speaker: string, id: string) {
  const voice = library.value.find((v) => v.id === id);
  if (voice) voices.value[speaker] = { ...speechDefaults, ...voice.profile, library_id: voice.id };
  else delete voices.value[speaker].library_id;
}
function setSource(speaker: string, source: string) {
  if (source === "recording") {
    voices.value[speaker].source = "recording";
    delete voices.value[speaker].library_id;
  } else delete voices.value[speaker].source;
}
async function loadVoices() {
  busy.value = true; error.value = "";
  try { catalogue.value = (await api("/audio/minimax/voices")).voices; }
  catch (e: any) { error.value = e.message; }
  finally { busy.value = false; }
}
async function persist() {
  await api(`/projects/${props.project.id}/audio-cast`, "PUT", {
    voices: voices.value, speakers: speakers.value, expected_version: props.project.version,
  });
  await props.reload();
  saved.value = JSON.stringify({ voices: voices.value, speakers: speakers.value });
}
async function save() {
  busy.value = true; error.value = "";
  try { await persist(); emit("notify", "角色配音已保存，受影响的旧配音与成片需要重新审核。"); }
  catch (e: any) { error.value = e.message; }
  finally { busy.value = false; }
}
async function audition(speaker: string) {
  const shot = sampleShot(speaker);
  if (!shot) return;
  busy.value = true; error.value = "";
  try {
    const previous = props.project.jobs.find((job) => job.stage === "audio" && job.entity_id === shot.id);
    if (requiresVerification(previous)) throw new Error("上次试音结果不明，请先在任务页核对供应商记录和账单。");
    if (dirty.value) await persist();
    await api(`/projects/${props.project.id}/jobs`, "POST", { stage: "audio", entity_id: shot.id, force: true });
    await props.reload();
    emit("notify", "已提交该角色的一句原台词试音，生成后仍需听审。");
  } catch (e: any) { error.value = e.message; }
  finally { busy.value = false; }
}
</script>
<template>
  <div class="modal-backdrop" @click.self="!busy && emit('close')">
    <section class="modal edit-modal wide-modal" role="dialog" aria-modal="true" aria-label="角色配音">
      <button class="icon-btn modal-close" aria-label="关闭角色配音" :disabled="busy" @click="emit('close')"><Icon name="close" /></button>
      <h2>角色配音 · MiniMax</h2>
      <p class="muted">为同一角色固定声线，再给每句台词指定说话者。角色声线和参数变化会使相关配音及成片失效，已定稿剧本和画面参考保留。</p>
      <p class="notice">长期规则：不新增定制或克隆音色。新增角色只选免音色费的系统音色或已有录音，已付费的固定音色可继续复用。TTS 新台词仍按字符计费。</p>
      <p v-if="!ready" class="notice">试音前，请在模型与连接选择 MiniMax、保存 API Key 并启用允许付费渠道，同时批准当前规范与分镜。<button class="text-button" @click="emit('settings')">打开声音设置</button></p>
      <button class="btn secondary" :disabled="busy || !settings?.audio.configured || settings?.audio.provider !== 'minimax'" @click="loadVoices">读取账号可用音色</button>
      <datalist id="cast-minimax-voices"><option v-for="voice in catalogue" :key="voice.id" :value="voice.id">{{ voice.name }}</option></datalist>
      <p v-if="libraryError" class="inline-error">固定音色资源库读取失败：{{ libraryError }}</p>
      <p v-if="library.length" class="notice">优先复用资源库已激活音色，保存不会生成音频。<a href="#assets">查看资源库与已有试听</a></p>
      <div v-for="role in characters" :key="role.id" class="cast-role">
        <h3>{{ role.name }}</h3>
        <div v-if="voices[role.id]" class="form-grid three">
          <label class="field">{{ role.name }}供音方式
            <select :aria-label="role.name + '供音方式'" :value="voices[role.id].source || 'tts'" @change="setSource(role.id, ($event.target as HTMLSelectElement).value)">
              <option value="tts">已有音色合成台词</option><option value="recording">已有录音 / 猫叫，不调用 TTS</option>
            </select>
          </label>
          <template v-if="voices[role.id].source !== 'recording'">
          <label v-if="library.length || voices[role.id].library_id" class="field">{{ role.name }}复用资源库音色
            <select :aria-label="role.name + '复用资源库音色'" :value="voices[role.id].library_id || ''" @change="useLibrary(role.id, ($event.target as HTMLSelectElement).value)">
              <option value="">选择免音色费的系统音色</option>
              <option v-for="voice in library" :key="voice.id" :value="voice.id">{{ voice.name }} · 已锁定 / 已激活</option>
            </select>
          </label>
          <label class="field">{{ role.name }}声线 ID<input v-model="voices[role.id].voice_id" :readonly="!!voices[role.id].library_id" list="cast-minimax-voices" /></label>
          <label class="field">{{ role.name }}语速<input type="number" min="0.5" max="2" step="0.05" v-model.number="voices[role.id].speed" /></label>
          <label class="field">{{ role.name }}音高<input type="number" min="-12" max="12" v-model.number="voices[role.id].pitch" /></label>
          <label class="field">{{ role.name }}音量<input type="number" min="0.1" max="10" step="0.1" v-model.number="voices[role.id].vol" /></label>
          <label class="field">{{ role.name }}情绪<select v-model="voices[role.id].emotion"><option :value="null">自动判断</option><option v-for="emotion in emotionOptions" :key="emotion.id" :value="emotion.id">{{ emotion.name }}</option></select></label>
          </template>
        </div>
        <p v-if="sampleShot(role.id)" class="muted small">{{ voices[role.id]?.source === 'recording' ? '字幕语义（猫叫不朗读中文）' : '试音台词' }}：{{ sampleShot(role.id).dialogue || sampleShot(role.id).narration }}</p>
        <button class="text-button" :disabled="voices[role.id]?.source === 'recording' || busy || !ready || !sampleShot(role.id) || project.jobs.some((j) => j.stage === 'audio' && j.entity_id === sampleShot(role.id)?.id && ['queued', 'running'].includes(j.status))" @click="audition(role.id)">{{ voices[role.id]?.source === 'recording' ? '使用已有录音，不生成试音' : '生成' + role.name + '试音' }}</button>
        <audio v-if="sampleArtifact(role.id) && !dirty" controls :src="sampleArtifact(role.id)!.url" :aria-label="role.name + '试音'" />
      </div>
      <h3>逐镜说话者</h3>
      <p class="muted small">一条配音对应一个说话者；同镜多人对话需先拆开。未指定时使用全局默认声线。</p>
      <label v-for="shot in shots" :key="shot.id" class="field cast-shot">
        {{ shot.id }} · {{ shot.dialogue || shot.narration }}
        <select :value="speakers[shot.id] || ''" @change="($event.target as HTMLSelectElement).value ? speakers[shot.id] = ($event.target as HTMLSelectElement).value : delete speakers[shot.id]">
          <option value="">全局默认声线</option><option v-for="role in characters" :key="role.id" :value="role.id">{{ role.name }}</option>
        </select>
      </label>
      <p v-if="error" class="inline-error">{{ error }}</p>
      <button class="btn primary" :disabled="busy || !settings" @click="save">保存角色配音</button>
      <p class="muted small">生成试音会提交原台词并产生 MiniMax 用量。参考视频音频不会自动上传为克隆素材；新音频保持待审核。超出镜头时长需调整，不会自动加速或截断。</p>
      <p class="muted small">资源库声线 ID 由服务端校验。未确认的音色 ID 和新增定制音色会被拦截，先读取系统音色目录再选择；录音角色只复用已有音轨。</p>
    </section>
  </div>
</template>
<style scoped>
.cast-role { margin: 20px 0; padding: 16px 0; border-bottom: 1px solid var(--border, #ddd); }
.cast-role audio { display: block; width: 100%; margin-top: 10px; }
.cast-shot { margin-bottom: 12px; }
</style>
