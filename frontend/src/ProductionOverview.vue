<script setup lang="ts">
import { computed, ref } from "vue";
import { api, type Project } from "./api";
import Icon from "./Icon.vue";
const props = defineProps<{ project: Project; reload: () => Promise<void> }>();
const emit = defineEmits<{ details: []; notify: [message: string] }>();
const busy = ref(false), error = ref("");
const full = computed(() => props.project.production_mode === "full" && props.project.automation);
const finished = computed(() => props.project.automation_status?.state === "completed");
const status = computed(() => props.project.automation_status?.state);
const stopped = computed(() => props.project.paused || props.project.cancelled || ['attention', 'error'].includes(status.value || ''));
const headline = computed(() => {
  if (props.project.paused) return '制作已暂停，恢复后继续';
  if (props.project.cancelled) return '制作已停止';
  if (['attention', 'error'].includes(status.value || '')) return '有个问题需要处理，请查看下方原因';
  if (finished.value) return '你的作品已准备好';
  if (status.value === 'retrying') return '系统正在等待重试，会自动继续';
  return full.value ? '你可以离开页面，系统会继续做完' : '点一次，自动做好整部作品';
});
const totals = computed(() => props.project.progress.reduce((r, s) => ({ done: r.done + s.done, total: r.total + s.total }), { done: 0, total: 0 }));
const percent = computed(() => Math.round(100 * totals.value.done / Math.max(1, totals.value.total)));
const renders = computed(() => props.project.artifacts.filter(a => a.kind === 'render' && a.review === 'approved' && !a.stale));
const references = computed(() => Object.values(props.project.reference_readiness?.assets || {}).flatMap(a => a.views));
async function start() {
  busy.value = true; error.value = "";
  try {
    await api(`/projects/${props.project.id}/automation`, "POST", { enabled: true, mode: "full", unlimited_budget: true });
    await props.reload();
    emit('notify', '已开启全自动制作：不设费用上限，检查不通过会自动返工。');
  } catch (e: any) { error.value = e.message; }
  finally { busy.value = false; }
}
</script>
<template>
  <section class="production-overview" aria-label="全自动制作">
    <div class="production-main">
      <div class="production-symbol"><Icon :name="finished ? 'check' : 'wand'" :size="28" /></div>
      <div class="production-copy">
        <span class="production-kicker">{{ stopped ? '制作暂未继续' : finished ? '制作完成' : full ? '全自动制作已开启' : '从需求到成片，交给系统完成' }}</span>
        <h2>{{ headline }}</h2>
        <p>{{ full ? '自动写方案、做参考图、生成每个镜头、配音、加字幕，最后合成全部分集。检查不通过就返工，不需要逐步批准。' : '已有内容继续使用；缺少的参考图、镜头、配音和字幕会自动补齐。' }}</p>
      </div>
      <div class="production-actions">
        <a v-if="finished" class="btn primary" :href="`/api/projects/${project.id}/export.zip`" download><Icon name="download" :size="18" />下载全部成片与素材</a>
        <button v-else-if="!full" class="btn primary" :disabled="busy" @click="start"><Icon name="wand" :size="18" />开启全自动 · 不设费用上限</button>
        <button class="text-button" @click="emit('details')">查看制作细节 <Icon name="right" :size="15" /></button>
      </div>
    </div>
    <div class="production-promises">
      <span><Icon name="approved" :size="16" />人物、场景、物品固定参考</span>
      <span><Icon name="images" :size="16" />画面检查不过就返工</span>
      <span><Icon name="audio" :size="16" />沿用已选声音，不新增定制音色</span>
    </div>
    <p class="production-budget">{{ full ? '本项目已授权不设费用上限，可调用已配置的付费渠道并持续返工。随时可以暂停。' : '开启即同意本项目不设费用上限，可调用已配置的付费渠道并持续返工。新台词仍可能按用量收费。' }} 账号失效、原参考或录音缺失、请求结果不明时会停下并说明原因。</p>
    <div v-if="project.plan" class="production-progress">
      <div><strong>{{ finished ? '全部完成' : `已完成 ${totals.done} / ${totals.total} 项` }}</strong><span>按当前已规划内容统计 · {{ percent }}%</span></div>
      <progress :value="totals.done" :max="Math.max(1, totals.total)" aria-label="整体制作进度" />
      <div class="production-metrics">
        <span>参考图 <b>{{ references.filter(v => v.approved).length }} / {{ references.length }}</b></span>
        <span>分集 <b>{{ project.plan.episodes.length }}</b></span>
        <span>镜头 <b>{{ project.plan.episodes.reduce((n: number, e: any) => n + e.shots.length, 0) }}</b></span>
        <span>可下载成片 <b>{{ renders.length }} / {{ project.episodes_count }}</b></span>
      </div>
    </div>
    <div v-if="renders.length" class="delivery-grid">
      <article v-for="item in renders" :key="item.id" class="delivery-card">
        <video controls preload="none" playsinline :src="item.url" :aria-label="`成片 ${item.entity_id}`" />
        <div><strong>{{ project.plan?.episodes.find((e: any) => e.id === item.entity_id)?.title || item.entity_id }}</strong><a class="text-button" :href="item.url + '?download=true'" download><Icon name="download" :size="15" />下载 MP4</a></div>
      </article>
    </div>
    <p class="production-limit">系统会严格执行一致性检查；模型判断仍可能出错，视频会逐帧核对，但自动检查仍不能保证零误判。</p>
    <p v-if="error" role="alert" class="inline-error">{{ error }}</p>
  </section>
</template>
