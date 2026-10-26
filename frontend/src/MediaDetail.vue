<script setup lang="ts">
import { computed, ref } from "vue";
import { api, stamp, statusText, type Artifact } from "./api";
import Icon from "./Icon.vue";
const props = defineProps<{ artifact: Artifact }>(),
  emit = defineEmits<{ close: []; refresh: []; notify: [message: string] }>();
const note = ref(""),
  busy = ref(false),
  job = ref<any>(null),
  showPrompt = ref(false);
const mediaKind = computed(() => props.artifact.mime.split("/")[0]);
async function review(approved: boolean) {
  busy.value = true;
  try {
    await api("/artifacts/" + props.artifact.id + "/review", "POST", {
      approved,
      note: note.value,
    });
    emit("notify", approved ? "已批准，可进入下一步" : "已标记为需返工");
    emit("refresh");
    emit("close");
  } catch (e: any) {
    emit("notify", e.message);
  } finally {
    busy.value = false;
  }
}
async function prompt() {
  showPrompt.value = !showPrompt.value;
  if (showPrompt.value && !props.artifact.archived && !job.value)
    try {
      job.value = await api("/jobs/" + props.artifact.job_id);
    } catch (e: any) {
      emit("notify", e.message);
    }
}
</script>
<template>
  <div class="modal-backdrop" @click.self="emit('close')">
    <section
      class="modal media-detail"
      role="dialog"
      aria-modal="true"
      aria-label="素材预览与审核"
    >
      <button
        class="icon-btn modal-close"
        aria-label="关闭预览"
        @click="emit('close')"
      >
        <Icon name="close" />
      </button>
      <div class="media-detail-preview">
        <img
          v-if="mediaKind === 'image'"
          :src="artifact.url"
          alt="生成素材原图"
        /><video
          v-else-if="mediaKind === 'video'"
          :src="artifact.url"
          controls
          playsinline
        /><audio v-else :src="artifact.url" controls />
      </div>
      <div class="media-detail-info">
        <div class="eyebrow">REVIEW & REFINE</div>
        <h2>{{ artifact.title || artifact.entity_id }}</h2>
        <span :class="['badge', artifact.stale ? 'stale' : artifact.review]">{{
          artifact.archived
            ? "历史导入 · 用途待复核"
            : artifact.stale
              ? "已过期"
              : statusText[artifact.review]
        }}</span>
        <dl class="metadata-list">
          <template v-if="artifact.archived">
            <dt>原始路径</dt>
            <dd class="archive-path">
              {{ artifact.metadata?.imported?.source_path }}
            </dd>
            <dt>来源分组</dt>
            <dd>{{ artifact.metadata?.imported?.group }}</dd>
            <dt>原审核状态</dt>
            <dd>
              {{
                artifact.metadata?.imported?.quality_status || "未记录，待复核"
              }}
            </dd>
            <dt>原制作审批</dt>
            <dd>
              {{ artifact.metadata?.imported?.production_approval || "未记录" }}
            </dd>
            <dt>SHA-256</dt>
            <dd class="archive-path">
              {{ artifact.metadata?.imported?.sha256 }}
            </dd>
          </template>
          <dt>实际模型</dt>
          <dd>{{ artifact.route?.provider }} / {{ artifact.route?.model }}</dd>
          <dt>渠道</dt>
          <dd>
            {{
              artifact.route?.paid
                ? "付费渠道 · 费用以账单为准"
                : "网页或本地渠道 · 额度以平台为准"
            }}
          </dd>
          <dt>版本</dt>
          <dd>
            规范 v{{ artifact.contract_revision }} · 分镜 v{{
              artifact.plan_revision
            }}
          </dd>
          <dt>生成时间</dt>
          <dd>{{ stamp(artifact.created_at) }}</dd>
          <template v-if="artifact.metadata?.width"
            ><dt>分辨率</dt>
            <dd>
              {{ artifact.metadata.width }} × {{ artifact.metadata.height }}
            </dd></template
          ><template v-if="artifact.metadata?.duration_seconds"
            ><dt>实际时长</dt>
            <dd>
              {{ artifact.metadata.duration_seconds.toFixed(2) }} 秒
            </dd></template
          >
        </dl>
        <div v-if="artifact.archived" class="notice">
          历史文件保留原版本及审核记录。下载后可在当前项目的指定分镜中上传并审核，再用于生产。
        </div>
        <div
          v-if="artifact.metadata?.imported?.record_warning"
          class="notice warning"
        >
          {{ artifact.metadata.imported.record_warning }}
        </div>
        <div v-if="artifact.metadata?.validation_error" class="notice warning">
          原文件读取检查未通过（{{
            artifact.metadata.validation_error
          }}），已保留文件供下载核对。
        </div>
        <div
          v-if="artifact.metadata?.aspect_matches === false"
          class="notice warning"
        >
          <Icon
            name="warning"
          />输出比例与目标不同；请检查构图，合成时将保留原图比例并补边。
        </div>
        <div v-if="artifact.metadata?.duration_warning" class="notice warning">
          {{ artifact.metadata.duration_warning }}
        </div>
        <p class="muted small">
          请检查人物身份、风格、手部与道具、动作及剧情信息。模型执行成功不代表画面合格。
        </p>
        <label v-if="!artifact.archived" class="field"
          >审核备注<textarea
            v-model="note"
            rows="3"
            placeholder="记录需要修改的细节…"
          />
        </label>
        <div class="button-row">
          <button
            v-if="!artifact.archived"
            class="btn primary"
            :disabled="busy || artifact.stale"
            @click="review(true)"
          >
            <Icon name="check" :size="16" />批准素材</button
          ><button
            v-if="!artifact.archived"
            class="btn secondary"
            :disabled="busy || artifact.stale"
            @click="review(false)"
          >
            需要返工</button
          ><a
            :href="artifact.url"
            :download="artifact.title || artifact.entity_id"
            class="icon-btn"
            title="下载原始素材"
            ><Icon name="download"
          /></a>
        </div>
        <button class="text-button prompt-toggle" @click="prompt">
          {{ showPrompt ? "收起" : "查看"
          }}{{ artifact.archived ? "导入记录" : "完整请求与规范" }}
          <Icon name="down" :size="14" />
        </button>
        <pre v-if="showPrompt" class="prompt-code">{{
          (artifact.archived
            ? JSON.stringify(artifact.metadata?.imported, null, 2)
            : job?.effective_prompt) ||
          JSON.stringify(job?.snapshot, null, 2) ||
          "读取中…"
        }}</pre>
      </div>
    </section>
  </div>
</template>
