<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { api } from "./api";
const emit = defineEmits<{ notify: [message: string] }>();
const state = ref<any>(null), saved = ref(""), error = ref(""), busy = ref(false);
const dirty = computed(() => !!state.value && JSON.stringify(state.value.rules) !== saved.value);
const stageNames: Record<string,string> = {validate:"入参校验与逐镜分流",video:"Vidu 画面",tts:"TTS 人声",mix:"BGM / 音效混音",subtitles:"ASS 台词字幕",danmaku:"ASS 滚动弹幕",compose:"FFmpeg 成片"};
const enumNames: Record<string,string> = { post_task_get:"文档协议 · POST task/get", get_tasks:"旧协议 · GET tasks/id", stop:"停止并报错", silent_marked:"生成静音异常候选，等待人工审核", trim:"截断或丢弃并记录", error:"停止并报错", ultrafast:"最快", veryfast:"很快", fast:"快速", medium:"标准", slow:"较慢" };
const groups = computed(() => Object.entries(state.value?.schema.properties || {}).filter(([key]) => key !== "instructions").map(([key, prop]: any) => ({ key, title: prop.title, fields: Object.entries(state.value.schema.$defs[prop.$ref.split('/').pop()].properties).map(([name, value]:any) => ({name, ...value, nullable: !!value.anyOf, type: value.type || value.anyOf?.find((t:any) => t.type !== 'null')?.type})) })));
function setNumber(group:string, field:any, event:Event) {
  const raw = (event.target as HTMLInputElement).value;
  state.value.rules[group][field.name] = raw === '' && field.nullable ? null : Number(raw);
}
function baseline(group:string, field:string) {
  const value = state.value.defaults[group][field];
  return value === null ? "未知，不估算" : value === true ? "开启" : value === false ? "关闭" : String(value);
}
async function load() {
  busy.value = true;
  try { state.value = await api('/settings/pipeline'); saved.value = JSON.stringify(state.value.rules); error.value = ''; }
  catch(e:any) { error.value = e.message; }
  finally { busy.value = false; }
}
async function save() {
  busy.value = true; error.value = '';
  try {
    state.value = await api('/settings/pipeline', 'PUT', {rules:state.value.rules,expected_revision:state.value.revision});
    saved.value = JSON.stringify(state.value.rules);
    emit('notify','制作规则已保存。新任务使用新规则；自动项目已暂停，旧任务和相关输出需核对或重新审核。');
  } catch(e:any) { error.value = e.message; } finally { busy.value = false; }
}
function reset() { state.value.rules = JSON.parse(JSON.stringify(state.value.defaults)); }
onMounted(load);
</script>
<template>
  <section class="settings-start pipeline-rules" aria-label="完整制作规则">
    <div class="settings-checklist-heading"><div><h2>完整制作规则 · 全站统一默认值</h2><p>下面的设置直接用于规划、逐镜分流、声音和最终合成。可以修改、保存或恢复用户文档的默认值。</p></div><span v-if="state" class="badge neutral">修订 {{ state.revision }}</span></div>
    <div v-if="error" class="inline-error" role="alert">{{ error }} <button class="text-button" @click="load">重新读取（丢弃草稿）</button></div>
    <template v-if="state">
      <ol class="pipeline-sequence"><li v-for="stage in state.stages" :key="stage">{{stageNames[stage]}}</li></ol>
      <p class="muted">{{ state.precedence }}</p>
      <p class="notice">保存变更会暂停自动项目，使相关旧任务和输出失效，需要重新创建或审核后手动继续；不会自动重新付费生成。音色绑定和密钥在原配置中保留。文案说明不会自动改写结构化参数，请同时修改对应设置。</p>
      <p v-if="dirty" class="notice warning" role="status">有未保存的规则修改，切换设置分类会保留草稿；离开本页会丢失。</p>
      <details v-for="group in groups" :key="group.key" class="optional-settings" open>
        <summary>{{ group.title }}</summary>
        <div class="form-grid three">
          <label v-for="field in group.fields" :key="field.name" class="field">
            <span>{{ field.title }}</span>
            <select :aria-label="field.title" v-if="field.enum" v-model="state.rules[group.key][field.name]"><option v-for="value in field.enum" :key="value" :value="value">{{enumNames[value] || value}}</option></select>
            <select :aria-label="field.title" v-else-if="field.type === 'boolean'" v-model="state.rules[group.key][field.name]"><option :value="true">开启</option><option :value="false">关闭</option></select>
            <input :aria-label="field.title" v-else-if="['integer','number'].includes(field.type)" :value="state.rules[group.key][field.name]" type="number" :step="field.type === 'integer' ? 1 : 'any'" :min="field.minimum" :max="field.maximum" @input="setNumber(group.key,field,$event)" />
            <input :aria-label="field.title" v-else v-model="state.rules[group.key][field.name]" :type="field.name.includes('color') ? 'color' : 'text'" />
            <small class="muted">默认：{{baseline(group.key,field.name)}}</small>
          </label>
        </div>
      </details>
      <details class="optional-settings"><summary>完整规则原文与自定义说明</summary><label class="field">规则文本<textarea v-model="state.rules.instructions" rows="22" /></label><p class="muted">默认全文来自你提供的规则。自定义说明加入规划任务上下文；接口能力、批准的参考和锁定音色不可被文字绕过。</p></details>
      <details class="optional-settings"><summary>不能通过设置绕过的硬限制</summary><ul><li v-for="limit in state.hard_limits" :key="limit">{{limit}}</li></ul></details>
      <div class="button-row"><button class="btn secondary" :disabled="busy" @click="reset">恢复全部默认值</button><button class="btn primary" :disabled="busy || !dirty" @click="save">保存完整规则</button></div>
      <p class="muted small">视频参考上限可收紧，不能超出供应商能力。分辨率、字体、真实积分和接口查询协议仍需服务端及供应商实际支持。价格留空表示未知，离线测试不代表真实生成。</p>
    </template>
    <p v-else-if="!error">正在读取完整制作规则…</p>
  </section>
</template>
<style scoped>
.pipeline-sequence { display:flex;flex-wrap:wrap;gap:12px 28px;padding-left:24px; }
.pipeline-rules details { margin-block:16px; }
.pipeline-rules summary { margin-bottom:12px; }
.pipeline-rules textarea { width:100%;font-family:inherit; }
.pipeline-rules .field { min-width:0; }
.pipeline-rules .button-row { display:flex;gap:12px;flex-wrap:wrap; }
</style>
