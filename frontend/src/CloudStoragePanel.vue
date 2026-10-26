<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api } from "./api";
import Icon from "./Icon.vue";

const emit = defineEmits<{ notify: [message: string] }>();
const settings = ref<any>(null),
  busy = ref(false),
  error = ref(""),
  check = ref<any>(null);
async function load() {
  try {
    settings.value = await api("/storage");
  } catch (e: any) {
    error.value = e.message;
  }
}
async function save() {
  const { configured, usage, ...body } = settings.value;
  settings.value = await api("/storage", "PUT", body);
}
async function run(test = false) {
  busy.value = true;
  error.value = "";
  check.value = null;
  try {
    await save();
    if (test) check.value = await api("/storage/test", "POST");
    emit(
      "notify",
      test ? "COS 上传与读回检查通过" : "云端存储配置已保存，凭据不回显",
    );
  } catch (e: any) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
onMounted(load);
</script>

<template>
  <section class="settings-card" v-if="settings">
    <div class="settings-card-heading">
      <span class="settings-kind-icon"><Icon name="folder" :size="23" /></span>
      <div>
        <h2>腾讯云 COS 素材库</h2>
        <p>私有存储桶通过临时链接预览，无需开放公开读。</p>
      </div>
    </div>
    <p class="notice">
      已上传 {{ settings.usage?.originals || 0 }} 份原始素材，
      {{ settings.usage?.thumbnails || 0 }} 张预览图， 原始文件
      {{ ((settings.usage?.bytes || 0) / 1024 / 1024).toFixed(1) }} MiB。 其中
      {{ settings.usage?.local_copies || 0 }} 份原始素材仍有本地副本。
    </p>
    <div class="form-grid three">
      <label class="field"
        >存储桶名<input
          v-model="settings.bucket"
          placeholder="bucket-1234567890"
          aria-label="COS 存储桶名"
      /></label>
      <label class="field"
        >地域<input
          v-model="settings.region"
          placeholder="ap-guangzhou"
          aria-label="COS 地域"
      /></label>
      <label class="field"
        >长期素材前缀<input
          v-model="settings.prefix"
          placeholder="frameweave"
          aria-label="COS 素材前缀"
      /></label>
      <label class="field"
        >SecretId<input
          v-model="settings.secret_id"
          type="password"
          autocomplete="new-password"
          :placeholder="
            settings.configured ? '已配置，留空保留' : '填写 COS SecretId'
          "
          aria-label="COS SecretId"
      /></label>
      <label class="field"
        >SecretKey<input
          v-model="settings.secret_key"
          type="password"
          autocomplete="new-password"
          :placeholder="
            settings.configured ? '已配置，留空保留' : '填写 COS SecretKey'
          "
          aria-label="COS SecretKey"
      /></label>
      <label class="field"
        >临时凭据 Token（可选）<input
          v-model="settings.token"
          type="password"
          autocomplete="new-password"
          placeholder="长期凭据留空"
          aria-label="COS Token"
      /></label>
    </div>
    <div class="button-row">
      <label class="inline-checkbox"
        ><input type="checkbox" v-model="settings.enabled" />启用 COS
        预览与存储</label
      >
      <label class="inline-checkbox"
        ><input
          type="checkbox"
          v-model="settings.auto_upload"
        />自动备份新生成素材</label
      >
    </div>
    <p class="muted small">
      自动备份适用于所有项目，默认关闭。上传成功后会完整下载核对
      SHA-256；上传不会自动删除本地文件。更换桶或地域需重新填写凭据。
    </p>
    <p v-if="check?.status === 'ok'" class="notice">上传与读回内容检查通过。</p>
    <p v-if="check?.lifecycle?.status === 'unknown'" class="notice warning">
      {{ check.lifecycle.message }}
    </p>
    <p v-else-if="check?.lifecycle" class="notice">
      生命周期规则已读取；删除本地副本前仍需核对长期素材前缀是否受过期规则影响。
    </p>
    <div v-if="error" class="inline-error" role="alert">{{ error }}</div>
    <div class="button-row">
      <button class="btn primary" :disabled="busy" @click="run()">
        保存云端存储
      </button>
      <button class="btn secondary" :disabled="busy" @click="run(true)">
        {{ busy ? "处理中…" : "保存并检查连接" }}
      </button>
      <button class="btn secondary" :disabled="busy" @click="load">
        刷新存储统计
      </button>
    </div>
  </section>
  <div v-else-if="error" class="inline-error" role="alert">{{ error }}</div>
</template>
