<script setup lang="ts">
import { computed, ref } from "vue";
import Icon from "./Icon.vue";
import { api, type Project, type Template } from "./api";
const props = defineProps<{ templates: Template[]; mode: string }>();
const emit = defineEmits<{ close: []; created: [project: Project] }>();
const form = ref({
  title: "",
  mode: props.mode,
  source: "",
  brief: "",
  episodes: props.mode === "drama" ? 3 : 1,
  duration: 60,
  aspect_ratio: "9:16",
  language: "简体中文",
  style: "电影感、自然光、克制表演",
  production_mode: "manual",
  unlimited_budget: false,
});
const selected = computed(() =>
    props.templates.find((t) => t.id === form.value.mode),
  ),
  busy = ref(false),
  error = ref(""),
  fileInput = ref<HTMLInputElement>();
async function file(event: Event) {
  const f = (event.target as HTMLInputElement).files?.[0];
  if (!f) return;
  if (f.size > 8 * 1024 * 1024) {
    error.value = "请选择小于 8MB 的文本文件";
    return;
  }
  form.value.source = await f.text();
  if (!form.value.title) form.value.title = f.name.replace(/\.(txt|md)$/i, "");
}
async function submit() {
  busy.value = true;
  error.value = "";
  try {
    emit("created", await api<Project>("/projects", "POST", { ...form.value, unlimited_budget: form.value.production_mode === 'full' }));
  } catch (e: any) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
function sample() {
  form.value.title = "云海来信";
  form.value.source =
    "第一章 灯塔\n修理师林夏在云海边的旧灯塔中发现一封寄给十年后自己的信。信封上画着一枚蓝色齿轮。她穿着深蓝色工装，背着磨旧的帆布包。\n\n第二章 机械鸟\n林夏沿着信中的线索，在铜色钟楼下找到一只停止运转的机械鸟。她把蓝色齿轮嵌入鸟的胸口，机械鸟亮起柔和的金光，展开翅膀。\n\n第三章 晨光\n机械鸟带着林夏走到灯塔顶端。云海渐渐散开，远处浮岛在晨光中出现。她打开下一封信，上面只写着：欢迎回家。";
  form.value.brief =
    "轻奇幻、温暖治愈，保持林夏服装和机械鸟外观一致。原文未说明的细节请在制作规范中标出待确认。";
}
</script>
<template>
  <div class="modal-backdrop" @click.self="!busy && emit('close')">
    <section
      class="modal create-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="create-title"
    >
      <button
        class="icon-btn modal-close"
        aria-label="关闭新建项目"
        @click="emit('close')"
        :disabled="busy"
      >
        <Icon name="close" />
      </button>
      <div class="eyebrow">新建作品</div>
      <h2 id="create-title">你想做什么视频？</h2>
      <p class="muted">
        导入一本小说，或直接写下想法。比如：一只橘猫做早餐的 30 秒短视频。
      </p>
      <form @submit.prevent="submit">
        <div class="mode-picker">
          <button
            v-for="t in templates"
            :key="t.id"
            type="button"
            :class="{ selected: form.mode === t.id }"
            @click="form.mode = t.id"
          >
            <Icon :name="t.icon" :size="18" />{{ t.name }}
          </button>
        </div>
        <label class="field"
          >项目名称<input
            v-model="form.title"
            required
            maxlength="120"
            placeholder="为这次创作起个名字"
            autofocus
        /></label>
        <div class="field">
          <div class="field-heading">
            <label for="source-content"
              >小说原文或视频需求 <span class="required">*</span></label
            >
            <div>
              <button type="button" class="text-button" @click="sample">
                填入示例小说</button
              ><button
                type="button"
                class="text-button"
                @click="fileInput?.click()"
              >
                <Icon name="upload" :size="14" />导入 TXT / MD
              </button>
            </div>
          </div>
          <textarea
            id="source-content"
            v-model="form.source"
            required
            minlength="8"
            :placeholder="selected?.input_hint"
            rows="7"
          /><input
            ref="fileInput"
            type="file"
            accept=".txt,.md,text/plain,text/markdown"
            hidden
            @change="file"
          /><span class="field-hint"
            >{{ form.source.length.toLocaleString() }} 字 · 原文完整保留。角色、剧情、声音和不想出现的内容都可以写在这里。</span
          >
        </div>
        <div class="form-grid four">
          <label class="field"
            >计划集数<input
              v-model.number="form.episodes"
              type="number"
              min="1"
              max="50"
              required /></label
          ><label class="field"
            >每集时长<select v-model.number="form.duration">
              <option :value="15">15 秒</option>
              <option :value="30">30 秒</option>
              <option :value="60">60 秒</option>
              <option :value="120">2 分钟</option>
              <option :value="300">5 分钟</option>
              <option :value="600">10 分钟</option>
            </select></label
          ><label class="field"
            >画面比例<select v-model="form.aspect_ratio">
              <option value="9:16">竖屏 · 9:16（短视频）</option>
              <option value="16:9">横屏 · 16:9</option>
              <option value="1:1">方形 · 1:1</option>
            </select></label
          ><label class="field"
            >目标语言<input v-model="form.language" maxlength="80" required
          /></label>
        </div>
        <label class="field"
          >视觉风格<input
            v-model="form.style"
            required
            maxlength="1000"
            placeholder="例如：二维漫剧、复古胶片、写实电影…" /></label
        ><label class="field"
          >补充要求 <span class="optional">可选</span
          ><textarea
            v-model="form.brief"
            rows="2"
            placeholder="想保留的细节、不能出现的内容、品牌或声音要求…"
          />
        </label>
        <label class="full-auto-choice">
          <input type="checkbox" :checked="form.production_mode === 'full'" @change="form.production_mode = ($event.target as HTMLInputElement).checked ? 'full' : 'manual'" />
          <span><strong>全自动完成 · 不设费用上限</strong><small>自动生成参考图、全部镜头、配音、字幕和成片，不需要逐步批准。检查不过会持续返工；人物、场景和物品沿用同一套参考。</small><small>勾选即授权调用已配置的付费渠道，不设费用上限。不会新增定制或克隆音色，台词合成可能按用量收费。</small></span>
        </label>
        <div v-if="error" class="inline-error" role="alert">
          <Icon name="warning" :size="17" />{{ error }}
        </div>
        <div class="modal-footer">
          <span
            ><Icon
              name="file"
              :size="15"
            />{{ form.production_mode === 'full' ? '创建后自动开始，可随时暂停' : '先创建项目，再查看方案或开启全自动' }}</span
          ><button class="btn primary" :disabled="busy" type="submit">
            <Icon
              :name="busy ? 'loading' : 'right'"
              :class="{ spinning: busy }"
              :size="17"
            />{{ busy ? "正在整理内容…" : form.production_mode === 'full' ? "开始全自动制作" : "创建项目" }}
          </button>
        </div>
      </form>
    </section>
  </div>
</template>
