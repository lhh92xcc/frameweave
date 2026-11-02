<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import Icon from "./Icon.vue";
import CloudStoragePanel from "./CloudStoragePanel.vue";
import PipelineRulesPanel from "./PipelineRulesPanel.vue";
import { api } from "./api";
import { emotionOptions, speechDefaults, starterVoices } from "./audio";

type GeminiAccount = {
  id: string;
  name: string;
  enabled: boolean;
  state: string;
  error?: { code?: string } | null;
};

const emit = defineEmits<{ notify: [message: string]; projects: [] }>();
const settings = ref<any>(null),
  catalog = ref<any>({ accounts: [], image: [], video: [] }),
  tab = ref("overview"),
  busy = ref(false),
  error = ref(""),
  connection = ref<any>(null),
  credential = ref(""),
  psidts = ref(""),
  accountName = ref(""),
  editingAccount = ref<GeminiAccount | null>(null),
  credits = ref<any>(null),
  upstreams = ref<any>({}),
  health = ref<any>({});
const seedream = ref<any>(null),
  seedreamKey = ref(""),
  seedreamError = ref("");
const savedAllowPaid = ref(false);
const audioVoices = ref(starterVoices),
  audioError = ref("");
const focusedKind = ref(""),
  savedSettings = ref("");
const dirty = computed(
  () =>
    !!settings.value && (JSON.stringify(settings.value) !== savedSettings.value),
);
const setupProgress = computed(
  () => kinds.filter((k) => configuredRoutes(k.id)).length,
);
function configuredRoutes(kind: string) {
  return settings.value?.[kind]?.some(
    (r: any) =>
      r.model &&
      (r.transport === "cloud"
        ? r.base_url && r.configured && !r.clear_key
        : webProviders.value.some(
            (p: any) => p.id === r.provider && p.configured,
          )),
  );
}
function routeSummary(kind: string) {
  const routes = settings.value?.[kind] || [];
  return routes.length
    ? routes
        .map(
          (r: any) =>
            `${webProviders.value.find((p: any) => p.id === r.provider)?.name || r.provider} · ${r.model}`,
        )
        .join("，备用：")
    : "还没有选择模型";
}
async function editAbility(kind: string) {
  focusedKind.value = kind;
  tab.value = "routing";
  await nextTick();
  document
    .getElementById("ability-" + kind)
    ?.scrollIntoView({ block: "start", behavior: "smooth" });
}
function addArk() {
  settings.value.text.unshift({transport: "cloud", provider: "volcengine-ark", model: "", base_url: "https://ark.cn-beijing.volces.com/api/v3", configured: false});
}
function configureAudioProvider(event: Event) {
  const provider = (event.target as HTMLSelectElement).value;
  settings.value.audio =
    provider === "minimax"
      ? {
          transport: "cloud",
          provider,
          model: "speech-2.8-turbo",
          base_url: "https://api.minimaxi.com/v1",
          configured: false,
        }
      : {
          transport: "cloud",
          provider: "compatible",
          model: "tts-1",
          base_url: "",
          configured: false,
        };
  if (provider === "volcengine-tts") {
    settings.value.audio = {transport: "cloud", provider, model: "seed-tts-2.0", base_url: "https://openspeech.bytedance.com", configured: false};
  }
  settings.value.voice = provider === "volcengine-tts" ? "zh_female_vv_uranus_bigtts" : provider === "minimax" ? "cute_boy" : "alloy";
  settings.value.speech ||= { ...speechDefaults };
  audioError.value = "";
}
async function loadAudioVoices() {
  audioError.value = "";
  busy.value = true;
  try {
    audioVoices.value = (await api("/audio/minimax/voices")).voices;
    emit(
      "notify",
      `已读取 ${audioVoices.value.length} 个 MiniMax 音色，未提交配音。`,
    );
  } catch (e: any) {
    audioError.value = e.message;
  } finally {
    busy.value = false;
  }
}
const kinds = [
  {
    id: "text",
    name: "写故事和分镜",
    icon: "file",
    description: "整理你的要求，写出每集内容和每个镜头",
  },
  {
    id: "image",
    name: "画人物、场景和镜头",
    icon: "images",
    description: "先画固定参考，再画每一镜开始时的画面",
  },
  {
    id: "vision",
    name: "检查画面是否一致",
    icon: "images",
    description: "核对人物、场景和物品，也用于视频逐帧检查；必须能看图片",
  },
  {
    id: "video",
    name: "把画面做成视频",
    icon: "clapper",
    description: "按固定画面和动作要求，生成每个动态镜头",
  },
];
const geminiAccounts = computed<GeminiAccount[]>(
  () => catalog.value.accounts || [],
);
const enabledGeminiAccounts = computed(
  () => geminiAccounts.value.filter((account) => account.enabled).length,
);
const webProviders = computed(() => [
  {
    id: "gemini",
    name: "Gemini 网页",
    configured: enabledGeminiAccounts.value > 0,
    kind: "account",
    credential_label: "__Secure-1PSID Cookie Value",
    text: true,
    images: true,
    models: ["gemini-pro", "gemini-flash"],
  },
  ...catalog.value.image.map((p: any) => ({ ...p, kind: "image" })),
  ...catalog.value.video.map((p: any) => ({ ...p, kind: "video" })),
]);
function options(kind: string) {
  return webProviders.value.filter((p: any) =>
    kind === "video"
      ? p.kind === "video"
      : kind === "image"
        ? p.id === "gemini" || p.kind === "image" || p.images
        : p.id === "gemini" || p.text,
  );
}
function models(kind: string, provider: string) {
  if (provider === "tokenhub-vidu") return ["auto", "vidu-video-q2", "vidu-video-q2-pro", "vidu-video-q2-turbo", "vidu-video-q3-pro", "vidu-video-q3-turbo"];
  if (provider === "tencent-vidu") return ["viduq2-pro", "viduq2-turbo", "viduq3-pro", "viduq3-turbo"];
  const item = webProviders.value.find((p: any) => p.id === provider);
  return kind === "image"
    ? item?.image_models || item?.models || []
    : item?.models || [];
}
async function load() {
  try {
    const [s, c, u, h] = await Promise.all([
      api("/settings"),
      api("/gateway/catalog"),
      api("/upstreams"),
      api("/health"),
    ]);
    if (!dirty.value) {
      settings.value = {
        ...s,
        text: Array.isArray(s.text) ? s.text : [],
        image: Array.isArray(s.image) ? s.image : [],
        vision: Array.isArray(s.vision) ? s.vision : [],
        video: Array.isArray(s.video) ? s.video : [],
        audio: s.audio || {},
        speech: s.speech || { ...speechDefaults },
      };
      savedSettings.value = JSON.stringify(settings.value);
    }
    savedAllowPaid.value = s.allow_paid;
    catalog.value = {
      ...c,
      accounts: Array.isArray(c.accounts) ? c.accounts : [],
      image: Array.isArray(c.image) ? c.image : [],
      video: Array.isArray(c.video) ? c.video : [],
    };
    upstreams.value = u;
    health.value = h;
    try {
      seedream.value = await api("/gateway/seedream");
      seedreamError.value = "";
    } catch {
      seedream.value = null;
      seedreamError.value = "无法读取 Seedream 状态，请检查网关。";
    }
  } catch (e: any) {
    error.value = e.message;
  }
}
async function saveSeedream() {
  busy.value = true;
  seedreamError.value = "";
  try {
    seedream.value = await api("/gateway/seedream", "PUT", {
      api_key: seedreamKey.value,
    });
    seedreamKey.value = "";
    emit("notify", "Seedream 凭据已保存到 Web2API，不会回显。");
  } catch (e: any) {
    seedreamError.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function clearSeedream() {
  if (
    !window.confirm(
      "清除 Web2API 中的 Seedream 凭据？这也会关闭网关的 Seedream 兜底。",
    )
  )
    return;
  busy.value = true;
  seedreamError.value = "";
  try {
    seedream.value = await api("/gateway/seedream", "DELETE");
    seedreamKey.value = "";
    emit("notify", "Web2API Seedream 凭据已清除。");
  } catch (e: any) {
    seedreamError.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function save() {
  busy.value = true;
  error.value = "";
  try {
    const data = JSON.parse(JSON.stringify(settings.value));
    delete data.moderation.configured;
    delete data.pipeline;
    delete data.pipeline_revision;
    for (const kind of ["text", "image", "vision", "video", "audio"]) {
      for (const route of kind === "audio" ? [data[kind]] : data[kind]) {
        delete route.configured;
        if (route.api_key === "") delete route.api_key;
        if (route.qc_max_cost_cny === "") route.qc_max_cost_cny = null;
      }
    }
    settings.value = await api("/settings", "PUT", data);
    savedSettings.value = JSON.stringify(settings.value);
    savedAllowPaid.value = settings.value.allow_paid;
    emit("notify", "模型策略已保存。密钥保留在本机，不会回显。");
  } catch (e: any) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
function add(kind: string) {
  settings.value[kind].push({
    transport: kind === "text" || kind === "vision" || kind === "video" ? "cloud" : "gateway",
    provider: kind === "video" ? "tokenhub-vidu" : kind === "image" ? "gemini" : "compatible",
    model: kind === "video" ? "auto" : "default",
    base_url: kind === "video" ? "https://tokenhub.tencentmaas.com" : "",
    api_key: "",
  });
}
async function configureTokenHub() {
  if (!settings.value.video.some((r: any) => r.provider === "tokenhub-vidu")) {
    if (settings.value.video.length >= 6) {
      error.value = "视频渠道已达 6 个，请先移除一个备用渠道再添加 TokenHub。";
      await editAbility("video");
      return;
    }
    addTokenHubVideo();
  }
  const index = settings.value.video.findIndex((r: any) => r.provider === "tokenhub-vidu");
  if (index > 0) settings.value.video.unshift(settings.value.video.splice(index, 1)[0]);
  await editAbility("video");
}
function chooseVideoConnection(r: any) {
  if (r.transport === "cloud") {
    r.provider = "tokenhub-vidu";
    r.model = "auto";
    r.base_url = "https://tokenhub.tencentmaas.com";
    r.api_key = "";
    r.configured = false;
    r.clear_key = false;
  }
}
function addTokenHubVideo() {
  settings.value.video.unshift({ transport: "cloud", provider: "tokenhub-vidu", model: "auto", base_url: "https://tokenhub.tencentmaas.com", api_key: "" });
}
function move(kind: string, i: number) {
  const rs = settings.value[kind];
  [rs[i - 1], rs[i]] = [rs[i], rs[i - 1]];
}
function routeProvider(kind: string, r: any) {
  const p = webProviders.value.find((x: any) => x.id === r.provider);
  r.model =
    (kind === "image" ? p?.image_default_model : p?.default_model) || "default";
}
function accountState(account: GeminiAccount) {
  if (!account.enabled) return "已停用";
  const states: Record<string, string> = {
    configured: "已保存，待检查",
    ready: "已连接",
    busy: "使用中",
    connecting: "检查中",
    login_required: "登录失效",
    cooldown: "暂时受限",
  };
  return states[account.state] || "待检查";
}
function accountIssue(account: GeminiAccount) {
  if (!account.enabled || !account.error?.code) return "";
  const messages: Record<string, string> = {
    authentication_failed: "登录已失效，请更新此账号的 Cookie。",
    quota_exceeded: "上次请求额度不足，网关会尝试其他已启用账号。",
    temporarily_blocked: "平台暂时限制了请求，请稍后检查。",
    upstream_timeout: "上次请求可能已完成，请先核对网页记录。",
  };
  return messages[account.error.code] || "上次请求未成功，请检查账号或网络。";
}
function accountBusy(account: GeminiAccount) {
  return busy.value || ["busy", "connecting"].includes(account.state);
}
function nextAccountName() {
  const names = new Set(geminiAccounts.value.map((account) => account.name));
  let index = 1;
  while (names.has(`Gemini 账号 ${index}`)) index++;
  return `Gemini 账号 ${index}`;
}
function connect(p: any, account: GeminiAccount | null = null) {
  connection.value = p;
  editingAccount.value = account;
  accountName.value = account?.name || nextAccountName();
  credential.value = "";
  psidts.value = "";
  credits.value = null;
  error.value = "";
}
function closeConnection() {
  connection.value = null;
  editingAccount.value = null;
  credential.value = "";
  psidts.value = "";
  credits.value = null;
  error.value = "";
}
async function saveConnection() {
  busy.value = true;
  error.value = "";
  try {
    const p = connection.value;
    const account = editingAccount.value;
    const path =
      p.kind === "account"
        ? "/accounts" + (account ? "/" + encodeURIComponent(account.id) : "")
        : p.kind === "image"
          ? "/web-image-providers/" + p.id
          : "/video-providers/" + p.id;
    const body =
      p.kind === "account"
        ? {
            name: accountName.value.trim(),
            secure_1psid: credential.value,
            secure_1psidts: psidts.value,
            ...(account ? { enabled: account.enabled } : {}),
          }
        : { credential: credential.value };
    await api(
      "/gateway" + path,
      p.kind === "account" && !account ? "POST" : "PUT",
      body,
    );
    closeConnection();
    await load();
    emit(
      "notify",
      p.kind === "account"
        ? "Gemini 账号已保存，请单独检查连接。"
        : "账号凭据已保存。请查询额度或连接状态后开始生成。",
    );
  } catch (e: any) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function checkCredits(p: any) {
  busy.value = true;
  error.value = "";
  try {
    credits.value = await api(
      "/gateway/" +
        (p.kind === "image" ? "web-image-providers" : "video-providers") +
        "/" +
        p.id +
        "/credits",
    );
  } catch (e: any) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function checkAccount(account: GeminiAccount) {
  busy.value = true;
  error.value = "";
  try {
    const path = "/gateway/accounts/" + encodeURIComponent(account.id);
    await api(path + "/connect", "POST");
    if (account.state === "login_required") await api(path + "/retry", "POST");
    await load();
    emit("notify", `「${account.name}」连接检查通过`);
  } catch (e: any) {
    await load();
    error.value = `「${account.name}」连接检查失败：${e.message}`;
  } finally {
    busy.value = false;
  }
}
async function toggleAccount(account: GeminiAccount) {
  busy.value = true;
  error.value = "";
  try {
    const enabled = !account.enabled;
    await api("/gateway/accounts/" + encodeURIComponent(account.id), "PUT", {
      name: account.name,
      enabled,
    });
    await load();
    emit("notify", `「${account.name}」已${enabled ? "启用" : "停用"}`);
  } catch (e: any) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
onMounted(load);
</script>
<template>
  <main class="settings-content">
    <div class="page-intro">
      <div>
        <div class="eyebrow">模型与连接</div>
        <h1>先把制作需要的工具接好</h1>
        <p>
          先连接账号，再选择模型。完整流水线规则已设为全站默认，可在“完整规则”查看、修改和恢复。
        </p>
      </div>
      <button class="btn secondary" @click="emit('projects')">
        <Icon name="folder" :size="17" />回到我的项目
      </button>
    </div>
    <div v-if="dirty" class="notice warning settings-unsaved" role="status">
      有修改还没保存。切换这里的分类可以继续编辑，离开设置页会丢失未保存的内容。<button
        class="text-button"
        :disabled="busy"
        @click="save"
      >
        立即保存
      </button>
    </div>
    <div class="settings-tabs">
      <button
        v-for="t in [
          { id: 'overview', name: '从这里开始', icon: 'grid' },
          { id: 'connections', name: '账号与连接', icon: 'link' },
          { id: 'routing', name: '生成策略', icon: 'layers' },
          { id: 'rules', name: '完整规则', icon: 'file' },
          { id: 'storage', name: '云端存储', icon: 'folder' },
          { id: 'runtime', name: '服务与更新', icon: 'settings' },
        ]"
        :key="t.id"
        :class="{ active: tab === t.id }"
        @click="tab = t.id"
      >
        <Icon :name="t.icon" :size="17" />{{ t.name }}
      </button>
    </div>
    <PipelineRulesPanel v-show="tab === 'rules'" @notify="emit('notify', $event)" />
    <section
      v-if="tab === 'overview' && settings"
      class="settings-start"
      aria-label="首次配置清单"
    >
      <div class="settings-checklist-heading">
        <div>
          <h2>按这三步设置，就能开始制作</h2>
          <p>
            下面表示配置是否已填写。账号能否调用、模型能否生成，仍要以平台检查和实际任务为准。
          </p>
        </div>
        <span class="badge neutral">{{ setupProgress }} / 4 项能力已配置</span>
      </div>
      <div class="setup-step">
        <span class="setup-number">1</span>
        <div>
          <h3>连接你自己的账号</h3>
          <p>
            使用已有平台的网页登录信息，或服务商提供的 API
            Key（接口密钥）。保存后可检查连接。
          </p>
        </div>
        <button class="btn secondary" @click="tab = 'connections'">
          添加或检查账号
        </button>
      </div>
      <div class="setup-step">
        <span class="setup-number">2</span>
        <div>
          <h3>选择四种制作能力</h3>
          <p>
            每项先选一个能用的模型即可；备用模型用于前一个渠道不可用时继续尝试。
          </p>
        </div>
      </div>
      <div class="setup-abilities">
        <button
          v-for="kind in kinds"
          :key="kind.id"
          @click="editAbility(kind.id)"
        >
          <Icon :name="kind.icon" :size="22" /><strong>{{ kind.name }}</strong
          ><small>{{ kind.description }}</small
          ><span
            :class="[
              'badge',
              configuredRoutes(kind.id) ? 'approved' : 'neutral',
            ]"
            >{{ configuredRoutes(kind.id) ? "已填配置" : "还需配置" }}</span
          ><span class="setup-route">{{ routeSummary(kind.id) }}</span
          ><span class="text-button"
            >查看与设置 <Icon name="right" :size="14"
          /></span>
        </button>
      </div>
      <div class="setup-step">
        <span class="setup-number">3</span>
        <div>
          <h3>回到项目，开始制作</h3>
          <p>
            全自动在项目内单独授权不设费用上限，无需打开全局付费开关。沿用已选人声和录音，不新增定制音色。
          </p>
        </div>
        <button class="btn primary" @click="emit('projects')">
          打开我的项目
        </button>
      </div>
      <details class="optional-settings">
        <summary>声音、文件备份和服务状态怎么设置？</summary>
        <p>
          配音服务在“生成策略”下方，声音合成和字幕参数在“完整规则”；“云端存储”是可选的文件备份，不影响默认本机保存；“服务与更新”用于排查本机服务问题。
        </p>
      </details>
    </section>
    <details v-if="tab === 'runtime'" class="advanced-console">
      <summary>高级工具：扩展控制台</summary>
      <p>仅在需要平台专用设置时打开，日常制作可直接使用本工作台。</p>
      <a
        :href="health.gateway_console_url"
        :aria-disabled="!health.gateway_console_url"
        target="_blank"
        rel="noopener"
        class="btn secondary"
        ><Icon name="external" :size="16" />Web2API 扩展控制台</a
      >
    </details>
    <div v-if="error" class="inline-error" role="alert">
      <Icon name="warning" :size="17" />{{ error }}
    </div>
    <div v-if="!settings && !error" class="panel-loading" role="status">
      正在读取配置…
    </div>
    <CloudStoragePanel
      v-if="tab === 'storage'"
      @notify="emit('notify', $event)"
    />
    <template v-if="tab === 'routing' && settings"
      ><section class="policy-card">
        <span class="policy-icon"><Icon name="spark" :size="25" /></span>
        <div>
          <h3>逐步制作是否允许使用付费模型</h3>
          <p>
            这是逐步制作的费用设置。全自动的不限费用授权在每个项目中单独开启。模型按排列顺序尝试，提交结果不明时停下核对。
          </p>
        </div>
        <label class="switch-label"
          ><span>允许付费渠道<small>包含付费主模型与兜底</small></span
          ><input
            v-model="settings.allow_paid"
            type="checkbox"
            role="switch"
            aria-label="允许付费渠道" /><i
        /></label>
      </section>
      <div
        v-if="seedream?.configured && !savedAllowPaid"
        class="notice warning"
      >
        已配置 Seedream 付费备用生图，但逐步制作的付费开关未开启。逐步制作的
        Gemini
        生图会被阻止；全自动使用项目单独的费用授权。可开启并保存逐步付费设置，或在“账号与连接”清除
        Seedream 密钥。
      </div>
      <p class="page-explainer">
        点击需要修改的能力展开设置。先选一个模型就够用；平台名称、模型名称和接口地址请按服务商提供的信息填写。
      </p>
      <details
        v-for="kind in kinds"
        :key="kind.id"
        :id="'ability-' + kind.id"
        class="settings-card ability-config"
        :open="focusedKind === kind.id"
      >
        <summary class="settings-card-heading">
          <span class="settings-kind-icon"
            ><Icon :name="kind.icon" :size="23"
          /></span>
          <div>
            <h2>{{ kind.name }}</h2>
            <p>{{ kind.description }}</p>
          </div>
          <span
            :class="[
              'badge',
              configuredRoutes(kind.id) ? 'approved' : 'neutral',
            ]"
            >{{ configuredRoutes(kind.id) ? "已配置" : "待配置" }}</span
          ><span class="ability-toggle"
            >展开修改 <Icon name="chevron" :size="16"
          /></span>
        </summary>
        <p class="page-explainer">{{ routeSummary(kind.id) }}</p>
        <button
          class="btn secondary small-btn"
          :disabled="settings[kind.id].length >= 6"
          @click="add(kind.id)"
        >
          <Icon name="plus" :size="15" />{{
            settings[kind.id].length ? "添加备用模型" : "选择一个模型"
          }}
        </button>
        <button v-if="kind.id === 'video'" class="btn secondary small-btn" :disabled="settings.video.length >= 6" @click="addTokenHubVideo">添加腾讯 TokenHub · API Key</button>
        <p v-if="kind.id === 'video'" class="route-hint">TokenHub 使用独立 API Key：支持逐镜 Q2/Q3 分流，模型 auto 表示按分镜条件选择；无约束时 Q3 Turbo，分镜可选 Q2 与速度/画质/预算偏好。720p，支持图片与已批准前镜视频参考。需要启用 COS 云端存储，首帧会上传并通过临时链接交给模型。声音默认关闭，分镜可显式开启 audio 或 bgm；按 TokenHub 计费。</p>
<button v-if="kind.id === 'text'" class="btn secondary small-btn" :disabled="settings.text.length >= 6" @click="addArk">添加豆包方舟 · API Key</button>
<p v-if="kind.id === 'video'" class="route-hint">视频仅允许 TokenHub Vidu。旧的其他视频渠道必须移除后再生成。</p>
        <div
          v-for="(r, i) in settings[kind.id]"
          :key="kind.id + '-' + i"
          class="route-row"
        >
          <div class="route-order">
            <span>{{ Number(i) + 1 }}</span
            ><small>{{ Number(i) === 0 ? "先用这个" : "备用" }}</small>
          </div>
          <div class="route-form">
            <div class="form-grid three">
              <label class="field"
                >接入方式<select v-model="r.transport" @change="kind.id === 'video' && chooseVideoConnection(r)">
                  <option v-if="kind.id !== 'video'" value="gateway">使用“账号与连接”中的平台</option>
                  <option value="cloud">
                    {{ kind.id === "video" ? "直接填写 API（含腾讯 TokenHub）" : "直接填写服务商 API" }}
                  </option>
                </select></label
              ><label v-if="r.transport === 'gateway'" class="field"
                >模型平台<select
                  v-model="r.provider"
                  @change="routeProvider(kind.id, r)"
                >
                  <option
                    v-for="p in options(kind.id)"
                    :key="p.id + p.kind"
                    :value="p.id"
                  >
                    {{ p.name || p.id }}
                  </option>
                </select></label
              ><label v-else class="field"
                >服务商名称<input
                  v-model="r.provider"
                  placeholder="如 compatible" /></label
              ><label class="field"
                >模型名称 / ID<input
                  v-model="r.model"
                  :list="kind.id + 'models' + i"
                  placeholder="填写模型 ID" /><datalist
                  :id="kind.id + 'models' + i"
                >
                  <option
                    v-for="m in models(kind.id, r.provider)"
                    :key="typeof m === 'string' ? m : m.id"
                    :value="typeof m === 'string' ? m : m.id"
                  /></datalist
              ></label>
            </div>
            <div v-if="r.transport === 'cloud'" class="form-grid">
              <label class="field"
                >服务商接口地址（通常含 /v1）<input
                  v-model="r.base_url"
                  placeholder="https://your-provider.example/v1"
                  type="url"
                  autocomplete="off" /></label
              ><label class="field"
                >{{ r.provider === "tencent-vidu" ? "腾讯云 SecretId:SecretKey" : "API Key" }}
                <span v-if="r.configured" class="configured-hint"
                  >已保存，留空保留</span
                ><input
                  v-model="r.api_key"
                  type="password"
                  :placeholder="
                    r.configured
                      ? '•••••••• · 输入新 Key 可替换'
                      : '粘贴你的 API Key'
                  "
                  autocomplete="new-password"
                /><label v-if="r.configured" class="inline-checkbox"
                  ><input
                    v-model="r.clear_key"
                    type="checkbox"
                  />清除已保存密钥</label
                ></label
              >
            </div>
            <p v-if="r.transport === 'gateway'" class="route-hint">
              <Icon name="link" :size="13" />凭据在“账号与连接”配置；官方视频
              API 也通过连接器统一管理。<button
                class="text-button"
                @click="tab = 'connections'"
              >
                去配置 <Icon name="arrow" :size="12" />
              </button>
            </p>
            <p v-else-if="kind.id === 'image'" class="route-hint">
              图像渠道需要兼容 images/generations；绑定参考图时需要支持
              images/edits。实际输出比例由模型决定。
            </p>
            <label v-if="['image', 'vision'].includes(kind.id)" class="field">
              单次请求预计费用上限（元，可能付费时必填）
              <input
                v-model.number="r.qc_max_cost_cny"
                type="number"
                min="0.000001"
                step="0.01"
                placeholder="例如 0.20"
              />
            </label>
          </div>
          <div class="route-tools">
            <button
              v-if="Number(i) > 0"
              class="icon-btn"
              title="上移渠道"
              @click="move(kind.id, Number(i))"
            >
              <Icon name="left" :size="16" class="rotate-up" /></button
            ><button
              v-if="settings[kind.id].length > 1"
              class="icon-btn"
              title="移除渠道"
              @click="settings[kind.id].splice(Number(i), 1)"
            >
              <Icon name="trash" :size="16" />
            </button>
          </div>
        </div>
      </details>
      <details class="settings-card optional-settings">
        <summary>高级设置：逐步制作的重试次数与预计预算</summary>
        <div class="settings-card-heading">
          <div>
            <h2>自动质检重试</h2>
            <p>
              逐角色、场景和道具对照项目规范与母版检查。不合格时自动改词重生，通过后自动批准；逐步模式的首次参考图需确认，次数或预算耗尽时暂停。全自动模式按批次持续返工、自动检查参考图，不受此预算限制；缺少配置或原参考时停止。
            </p>
          </div>
        </div>
        <label class="field"
          >每张图最多生成次数（1–5）<input
            type="number"
            min="1"
            max="5"
            v-model.number="settings.image_qc_attempts"
        /></label>
        <label class="field"
          >每张图生成与质检预计预算（元）<input
            type="number"
            min="0"
            step="0.01"
            v-model.number="settings.image_qc_budget_cny"
        /></label>
        <p class="muted small">
          逐步模式默认 0
          元，不提交可能付费的生图或质检请求；全自动按项目授权不设费用上限，无需填写此预算。逐步模式允许付费时须为每个相关渠道填写单次最高预估。预算按提交预留，重试和兜底均计入；仅按填写的单价控制，无法限制供应商实际账单。Gemini
          开启付费兜底时也须填写。
        </p>
      </details>
      <section class="settings-card">
        <div class="settings-card-heading">
          <span class="settings-kind-icon"
            ><Icon name="audio" :size="23"
          /></span>
          <div>
            <h2>声音与字幕</h2>
            <p>声音默认使用独立 TTS 和后置混音；字幕、BGM、弹幕及 Vidu 原声保留在“完整规则”统一设置。</p>
          </div>
        </div>
        <div class="form-grid three">
          <label class="field"
            >独立配音服务方式<select v-model="settings.audio_mode">
              <option value="native">旧配置：原生（开启后置配音时使用 Edge TTS）</option>
              <option value="edge">独立配音 · Edge TTS</option>
              <option value="cloud">独立配音 · 兼容云接口</option>
              <option value="silent">旧配置：静音（开启后置配音时使用 Edge TTS）</option>
            </select></label
          ><label class="field"
            >声线 ID<input
              v-model="settings.voice"
              :placeholder="
                settings.audio_mode === 'cloud'
                  ? settings.audio.provider === 'minimax'
                    ? '如 cute_boy'
                    : '如 alloy'
                  : 'zh-CN-XiaoxiaoNeural'
              "
              :list="
                settings.audio.provider === 'minimax'
                  ? 'minimax-voices'
                  : undefined
              " /></label
          ><div class="field"><span>字幕与合成规则</span><button class="btn secondary" @click="tab = 'rules'">查看完整规则与默认值</button></div>
        </div>
        <section class="panel">
          <h3>腾讯云内容安全 · TMS / IMS / VMS</h3>
          <label><input type="checkbox" v-model="settings.moderation.enabled" />启用生产内容审核</label>
          <p>文本、图片和视频仅 Pass 放行；Review / Block / 请求失败停止生产。先在腾讯云开通服务，审核可能计费。密钥留空保留，不会回显。</p>
          <div class="form-grid">
            <label class="field">SecretId<input type="password" v-model="settings.moderation.secret_id" autocomplete="new-password" /></label>
            <label class="field">SecretKey<input type="password" v-model="settings.moderation.secret_key" autocomplete="new-password" /></label>
            <label class="field">BizType<input v-model="settings.moderation.biz_type" /></label>
            <label class="field">地域<input v-model="settings.moderation.region" /></label>
          </div>
          <label><input type="checkbox" v-model="settings.moderation.clear_keys" />清除审核密钥</label>
        </section>
        <label v-if="settings.audio.provider === 'volcengine-tts'" class="field">火山资源 ID<input v-model="settings.tts_resource_id" /><small>默认 seed-tts-2.0；按已开通资源填写。系统音色：zh_female_vv_uranus_bigtts、zh_male_m191_uranus_bigtts。已有锁定声线不自动替换。</small></label>
        <label v-if="settings.audio_mode === 'cloud'" class="field">
          配音服务
          <select
            :value="
              ['minimax', 'volcengine-tts'].includes(settings.audio.provider) ? settings.audio.provider : 'compatible'
            "
            @change="configureAudioProvider"
          >
            <option value="compatible">兼容云接口</option>
            <option value="minimax">MiniMax · 复用原锁定声音</option><option value="volcengine-tts">火山引擎 TTS · 系统音色</option>
          </select>
        </label>
        <div v-if="settings.audio_mode === 'cloud'" class="form-grid three">
          <label class="field"
            >配音 API 地址<input
              v-model="settings.audio.base_url"
              :readonly="settings.audio.provider === 'minimax'"
              placeholder="https://your-provider.example/v1" /></label
          ><label class="field"
            >配音模型<select
              v-if="settings.audio.provider === 'minimax'"
              v-model="settings.audio.model"
            >
              <option value="speech-2.8-turbo">speech-2.8-turbo</option>
              <option value="speech-2.8-hd">speech-2.8-hd</option></select
            ><input v-else v-model="settings.audio.model" /></label
          ><label class="field"
            >API Key<input
              type="password"
              v-model="settings.audio.api_key"
              :placeholder="
                settings.audio.configured ? '已保存，留空保留' : '填写 Key'
              "
              autocomplete="new-password"
          /></label>
        </div>
        <template
          v-if="
            settings.audio_mode === 'cloud' &&
            settings.audio.provider === 'minimax'
          "
        >
          <datalist id="minimax-voices">
            <option
              v-for="voice in audioVoices"
              :key="voice.id"
              :value="voice.id"
            >
              {{ voice.name }}
            </option>
          </datalist>
          <div class="form-grid three">
            <label class="field"
              >默认语速<input
                type="number"
                min="0.5"
                max="2"
                step="0.05"
                v-model.number="settings.speech.speed"
            /></label>
            <label class="field"
              >默认音高<input
                type="number"
                min="-12"
                max="12"
                step="1"
                v-model.number="settings.speech.pitch"
            /></label>
            <label class="field"
              >默认音量<input
                type="number"
                min="0.1"
                max="10"
                step="0.1"
                v-model.number="settings.speech.vol"
            /></label>
            <label class="field"
              >默认情绪<select v-model="settings.speech.emotion">
                <option :value="null">自动判断</option>
                <option
                  v-for="emotion in emotionOptions"
                  :key="emotion.id"
                  :value="emotion.id"
                >
                  {{ emotion.name }}
                </option>
              </select></label
            >
          </div>
          <button
            class="btn secondary"
            :disabled="busy || !settings.audio.configured"
            @click="loadAudioVoices"
          >
            读取可用音色 / 检查密钥
          </button>
          <p v-if="audioError" class="card-error">{{ audioError }}</p>
          <p class="muted small">
            先保存 API
            Key，再读取账号音色；读取目录不会生成语音。在项目的“角色配音”中为每个角色选声线，并用原台词试音。参考价：Turbo
            2 元 / 万计费字符，汉字按 2 个字符计，实际以 MiniMax
            用量和账单为准。逐步模式需开启允许付费渠道；全自动可使用本项目保存的费用授权。
          </p>
          <p class="muted small">
            <a href="#assets">固定音色资源库</a
            >保留已激活音色和试听，可跨项目复用。已禁止新增定制或克隆音色，新角色只选免音色费的系统音色或已有录音；TTS
            字符费另计。
          </p>
        </template>
        <p class="muted small">
          全自动会制作独立音轨并加字幕，沿用项目已选声音；逐步模式使用此处的声音与字幕选择。音频过长或原声音缺失时会停止。自动检查不包含口型验证。
        </p>
      </section>
      <div class="settings-save">
        <span
          ><Icon name="link" :size="15" />配置只保存在本机 · 密钥不会回显或进入
          Git</span
        ><button class="btn primary" :disabled="busy" @click="save">
          <Icon
            :name="busy ? 'loading' : 'check'"
            :class="{ spinning: busy }"
            :size="17"
          />保存生成策略
        </button>
      </div></template
    >
    <section v-if="tab === 'connections'">
      <div class="section-heading">
        <div>
          <h2>我的模型连接</h2>
          <p class="muted small">
            网页凭据来自你自己的已登录账号；是否有可用额度，以平台查询结果为准。
          </p>
        </div>
        <button class="btn secondary" @click="load">
          <Icon name="refresh" :size="16" />刷新状态
        </button>
      </div>
      <details
        class="settings-card optional-settings"
        aria-label="Seedream 付费兜底"
      >
        <summary>可选：Seedream 付费备用生图</summary>
        <div class="settings-card-heading">
          <span class="settings-kind-icon"
            ><Icon name="images" :size="23"
          /></span>
          <div>
            <h2>Seedream 付费兜底</h2>
            <p>直接同步 Web2API 设置；不是工作台独立保存的一份配置。</p>
          </div>
          <span
            :class="['badge', seedream?.configured ? 'approved' : 'neutral']"
          >
            {{
              !seedream ? "状态未知" : seedream.configured ? "已配置" : "未配置"
            }}
          </span>
        </div>
        <p class="muted small">
          至少两个启用的 Gemini
          账号本次均确认额度耗尽，或所有启用账号（至少两个）均登录失效时，
          工作台可使用
          Seedream。超时、状态不明或其他账号不可用不会触发登录失效兜底；
          请先开启并保存“允许付费渠道”。
        </p>
        <p v-if="seedream" class="muted small">
          模型：{{ seedream.model }} · 网关参考价：¥{{
            seedream.output_price_cny
          }}/张；实际以供应商账单为准。 已配置不代表密钥已验证或账户有额度。
        </p>
        <label class="field"
          >火山方舟 API Key
          <input
            v-model="seedreamKey"
            type="password"
            autocomplete="new-password"
            placeholder="输入新密钥；已保存密钥不会回显"
          />
        </label>
        <div v-if="seedreamError" class="inline-error" role="alert">
          {{ seedreamError }}
        </div>
        <div class="button-row">
          <button
            class="btn primary"
            :disabled="busy || !seedreamKey.trim()"
            @click="saveSeedream"
          >
            保存 Seedream 密钥
          </button>
          <button
            v-if="seedream?.configured"
            class="btn secondary"
            :disabled="busy"
            @click="clearSeedream"
          >
            清除 Seedream 密钥
          </button>
        </div>
      </details>
      <div class="connections-grid">
        <article class="connection-card" data-testid="tokenhub-connection">
          <div class="connection-info">
            <div class="connection-card-top"><span class="connection-symbol">腾</span><span class="badge neutral">API Key</span></div>
            <h3>腾讯 TokenHub</h3>
            <p>Vidu Q2、Q2 Pro / Turbo、Q3 Pro / Turbo 视频生成。使用 TokenHub API Key，并需启用 COS 传递首帧。</p>
            <span class="connection-type">官方 API · 付费 / 平台额度</span>
            <div class="connection-actions"><button class="btn secondary small-btn" :disabled="busy" @click="configureTokenHub">配置 TokenHub<Icon name="right" :size="14" /></button></div>
          </div>
        </article>
        <article
          v-for="p in webProviders"
          :key="p.id + p.kind"
          class="connection-card"
          :class="{ 'gemini-card': p.kind === 'account' }"
        >
          <div class="connection-info">
            <div class="connection-card-top">
              <span class="connection-symbol">{{
                (p.name || p.id).slice(0, 1)
              }}</span
              ><span
                :class="['badge', p.configured ? 'approved' : 'neutral']"
                >{{
                  p.kind === "account" && geminiAccounts.length
                    ? `${geminiAccounts.length} 个账号`
                    : p.configured
                      ? "已配置"
                      : "待配置"
                }}</span
              >
            </div>
            <h3>{{ p.name || p.id }}</h3>
            <p>
              {{
                p.capabilities ||
                p.verification_label ||
                (p.id === "gemini"
                  ? "网页文字规划、参考图与关键帧"
                  : "网页图像连接器")
              }}
            </p>
            <span class="connection-type">{{
              p.kind === "account"
                ? "Gemini 网页 · 支持多个账号"
                : p.kind === "video" && !p.free_web && p.auth_type === "api_key"
                  ? "官方 API · 付费 / 平台额度"
                  : "网页账号 · 额度依平台为准"
            }}</span>
            <div class="connection-actions">
              <button
                class="btn secondary small-btn"
                :disabled="busy"
                @click="connect(p)"
              >
                {{
                  p.kind === "account"
                    ? "添加账号"
                    : p.configured
                      ? "更新凭据"
                      : "配置连接"
                }}<Icon name="right" :size="14" /></button
              ><button
                v-if="p.kind !== 'account' && p.supports_credits"
                class="text-button"
                :disabled="busy"
                @click="
                  connect(p);
                  checkCredits(p);
                "
              >
                查询额度
              </button>
            </div>
          </div>
          <div v-if="p.kind === 'account'" class="account-list">
            <div class="account-list-heading">
              <strong>已保存账号</strong>
              <span>{{ enabledGeminiAccounts }} 个已启用</span>
            </div>
            <div v-if="!geminiAccounts.length" class="account-hint">
              可以分别添加多个 Gemini 网页账号。
            </div>
            <div
              v-for="account in geminiAccounts"
              :key="account.id"
              class="account-row"
            >
              <div class="account-summary">
                <b>{{ account.name }}</b>
                <span
                  :class="[
                    'badge',
                    account.enabled && account.state === 'ready'
                      ? 'approved'
                      : account.enabled && account.state === 'login_required'
                        ? 'failed'
                        : 'neutral',
                  ]"
                  >{{ accountState(account) }}</span
                >
              </div>
              <div v-if="accountIssue(account)" class="account-issue">
                {{ accountIssue(account) }}
              </div>
              <div class="account-actions">
                <button
                  class="text-button"
                  :aria-label="`检查 ${account.name} 连接`"
                  :disabled="accountBusy(account) || !account.enabled"
                  @click="checkAccount(account)"
                >
                  检查连接
                </button>
                <button
                  class="text-button"
                  :aria-label="`编辑 ${account.name}`"
                  :disabled="accountBusy(account)"
                  @click="connect(p, account)"
                >
                  编辑
                </button>
                <button
                  class="text-button"
                  :aria-label="`${account.enabled ? '停用' : '启用'} ${account.name}`"
                  :disabled="accountBusy(account)"
                  @click="toggleAccount(account)"
                >
                  {{ account.enabled ? "停用" : "启用" }}
                </button>
              </div>
            </div>
            <div class="account-hint">
              当前网关最多保存 8
              个账号，停用不会删除；按添加顺序优先尝试已启用账号。部分超时也可能切换账号，可能产生重复请求。
            </div>
          </div>
        </article>
      </div>
      <div class="notice">
        <Icon name="external" /><span
          >Gemini
          支持多个账号，其他连接器目前各保存一套凭据。上游新增的连接器会自动出现在这里。浏览器扩展、自定义网页平台、复杂账号配置，可在
          <a
            :href="health.gateway_console_url"
            :aria-disabled="!health.gateway_console_url"
            target="_blank"
            rel="noopener"
            >Web2API 扩展控制台</a
          >
          继续使用。</span
        >
      </div>
    </section>
    <section v-if="tab === 'runtime'" class="settings-card">
      <h2>服务与上游版本</h2>
      <p class="muted">工作台与两个上游模块独立演进，定制代码保持在适配层。</p>
      <div class="service-status">
        <div
          v-for="[name, value] in [
            ['工作台', health.status === 'ok' ? 'ready' : 'offline'],
            ['网页模型网关', health.gateway],
            ['视频媒体引擎', health.engine],
          ]"
          :key="name"
        >
          <i :class="['connection-dot', { on: value === 'ready' }]" /><b>{{
            name
          }}</b
          ><span>{{ value === "ready" ? "已就绪" : "未连接" }}</span>
        </div>
      </div>
      <div v-for="(u, key) in upstreams" :key="key" class="upstream-row">
        <div>
          <b>{{ key }}</b
          ><a :href="u.url" target="_blank" rel="noopener"
            >{{ u.url }} <Icon name="external" :size="12"
          /></a>
        </div>
        <code>{{ u.commit?.slice(0, 12) }}</code>
      </div>
      <div class="notice">
        <Icon name="refresh" />
        <div>
          在项目目录运行
          <code>python3 scripts/upstream.py check</code> 查看更新；运行
          <code>python3 scripts/upstream.py update all</code>
          更新并验证适配。更新前停止服务；验证失败会恢复原版本。
        </div>
      </div>
      <p class="muted small">
        本机优先使用原生启动。容器部署使用 OrbStack 提供的兼容容器运行时。
      </p>
    </section>
    <div
      v-if="connection"
      class="modal-backdrop"
      @click.self="!busy && closeConnection()"
    >
      <section
        class="modal connection-modal"
        role="dialog"
        aria-modal="true"
        aria-label="配置模型连接"
      >
        <button
          class="icon-btn modal-close"
          aria-label="关闭连接设置"
          :disabled="busy"
          @click="closeConnection"
        >
          <Icon name="close" />
        </button>
        <div class="eyebrow">添加或更新账号</div>
        <h2>
          {{
            connection.kind === "account"
              ? editingAccount
                ? "编辑 Gemini 账号"
                : "添加 Gemini 账号"
              : connection.name || connection.id
          }}
        </h2>
        <p class="muted">
          {{
            connection.kind === "account"
              ? "分别填写每个网页账号的 Cookie；凭据只保存在本机，不会回显。"
              : connection.api_free_note ||
                connection.credential_label ||
                "填写你自己账号的凭据。密钥只保存在本机私有目录。"
          }}
        </p>
        <label v-if="connection.kind === 'account'" class="field"
          >账号名称<input v-model="accountName" maxlength="60" /></label
        ><label class="field"
          >{{ connection.credential_label || "账号凭据 / API Key"
          }}<textarea
            v-model="credential"
            class="credential-input"
            rows="4"
            autocomplete="new-password"
            :placeholder="
              editingAccount
                ? '留空保留已保存的凭据'
                : '粘贴凭据；不会回显已保存内容'
            "
          /></label
        ><label v-if="connection.kind === 'account'" class="field"
          >__Secure-1PSIDTS（可选）<input
            v-model="psidts"
            type="password"
            autocomplete="new-password"
        /></label>
        <p v-if="editingAccount" class="muted small">
          仅修改名称时可留空凭据；更换主 Cookie 时，请重新填写对应的
          __Secure-1PSIDTS（如有）。
        </p>
        <div v-if="credits" class="credits-result">
          <b>平台返回的额度信息</b>
          <pre>{{ JSON.stringify(credits, null, 2) }}</pre>
        </div>
        <div v-if="error" class="inline-error">{{ error }}</div>
        <div class="button-row">
          <button
            class="btn primary"
            :disabled="
              busy ||
              (connection.kind === 'account' && !accountName.trim()) ||
              (!credential.trim() &&
                !(connection.kind === 'account' && editingAccount))
            "
            @click="saveConnection"
          >
            保存连接</button
          ><button
            v-if="connection.kind !== 'account' && connection.supports_credits"
            class="btn secondary"
            :disabled="busy"
            @click="checkCredits(connection)"
          >
            检查已保存连接
          </button>
        </div>
        <p class="muted small">
          登录失效或平台要求验证时，请先在对应网页完成验证，再更新凭据。
        </p>
      </section>
    </div>
  </main>
</template>
