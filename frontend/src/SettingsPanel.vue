<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import Icon from "./Icon.vue";
import CloudStoragePanel from "./CloudStoragePanel.vue";
import { api } from "./api";

type GeminiAccount = {
  id: string;
  name: string;
  enabled: boolean;
  state: string;
  error?: { code?: string } | null;
};

const emit = defineEmits<{ notify: [message: string] }>();
const settings = ref<any>(null),
  catalog = ref<any>({ accounts: [], image: [], video: [] }),
  tab = ref("routing"),
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
const seedream = ref<any>(null), seedreamKey = ref(""), seedreamError = ref("");
const savedAllowPaid = ref(false);
const kinds = [
  {
    id: "text",
    name: "文字与规划",
    icon: "file",
    description: "制作规范、角色设定、分集与分镜",
  },
  {
    id: "image",
    name: "图像生成",
    icon: "images",
    description: "角色 / 场景参考图与逐镜关键帧",
  },
  {
    id: "vision",
    name: "图片自动质检",
    icon: "images",
    description: "检查生成图与参考图、制作规范；模型必须支持视觉输入",
  },
  {
    id: "video",
    name: "视频生成",
    icon: "clapper",
    description: "从已批准关键帧生成动态镜头",
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
    settings.value = s;
    savedAllowPaid.value = s.allow_paid;
    catalog.value = c;
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
    seedream.value = await api("/gateway/seedream", "PUT", { api_key: seedreamKey.value });
    seedreamKey.value = "";
    emit("notify", "Seedream 凭据已保存到 Web2API，不会回显。");
  } catch (e: any) {
    seedreamError.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function clearSeedream() {
  if (!window.confirm("清除 Web2API 中的 Seedream 凭据？这也会关闭网关的 Seedream 兜底。")) return;
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
    for (const kind of ["text", "image", "vision", "video", "audio"]) {
      for (const route of kind === "audio" ? [data[kind]] : data[kind]) {
        delete route.configured;
        if (route.api_key === "") delete route.api_key;
      }
    }
    settings.value = await api("/settings", "PUT", data);
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
    transport: kind === "text" || kind === "vision" ? "cloud" : "gateway",
    provider:
      kind === "video" ? "vidu" : kind === "image" ? "gemini" : "compatible",
    model: "default",
    base_url: "",
    api_key: "",
  });
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
        <div class="eyebrow">CONNECTED CREATIVITY</div>
        <h1>连接模型，释放创作。</h1>
        <p>把自己的网页额度和云模型连进来。选好策略，剩下的交给流程。</p>
      </div>
      <a
        :href="health.gateway_console_url"
        :aria-disabled="!health.gateway_console_url"
        target="_blank"
        rel="noopener"
        class="btn secondary"
        ><Icon name="external" :size="16" />Web2API 扩展控制台</a
      >
    </div>
    <div class="settings-tabs">
      <button
        v-for="t in [
          { id: 'routing', name: '生成策略', icon: 'layers' },
          { id: 'connections', name: '账号与连接', icon: 'link' },
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
    <div v-if="error" class="inline-error" role="alert">
      <Icon name="warning" :size="17" />{{ error }}
    </div>
    <CloudStoragePanel
      v-if="tab === 'storage'"
      @notify="emit('notify', $event)"
    />
    <template v-if="tab === 'routing' && settings"
      ><section class="policy-card">
        <span class="policy-icon"><Icon name="spark" :size="25" /></span>
        <div>
          <h3>先用网页额度，按需付费兜底。</h3>
          <p>
            每种能力按渠道顺序尝试。额度不足或鉴权失败时继续下一个渠道；提交结果不明时暂停等待核对。
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
      <div v-if="seedream?.configured && !savedAllowPaid" class="notice warning">
        Web2API 已配置 Seedream 付费兜底，但工作台付费开关未开启；为防止意外付费，Gemini 图片请求会被阻止。
        请启用并保存付费策略，或在账号与连接中清除 Seedream 凭据。
      </div>
      <section v-for="kind in kinds" :key="kind.id" class="settings-card">
        <div class="settings-card-heading">
          <span class="settings-kind-icon"
            ><Icon :name="kind.icon" :size="23"
          /></span>
          <div>
            <h2>{{ kind.name }}</h2>
            <p>{{ kind.description }}</p>
          </div>
          <button
            class="btn secondary small-btn"
            :disabled="settings[kind.id].length >= 6"
            @click="add(kind.id)"
          >
            <Icon name="plus" :size="15" />添加兜底渠道
          </button>
        </div>
        <div
          v-for="(r, i) in settings[kind.id]"
          :key="kind.id + '-' + i"
          class="route-row"
        >
          <div class="route-order">
            <span>{{ Number(i) + 1 }}</span
            ><small>{{ Number(i) === 0 ? "首选" : "兜底" }}</small>
          </div>
          <div class="route-form">
            <div class="form-grid three">
              <label class="field"
                >接入方式<select v-model="r.transport">
                  <option value="gateway">Web2API 连接器</option>
                  <option v-if="kind.id !== 'video'" value="cloud">
                    兼容接口 · 云模型
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
                >渠道标识<input
                  v-model="r.provider"
                  placeholder="如 compatible" /></label
              ><label class="field"
                >模型 ID<input
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
                >API 基础地址（含 /v1）<input
                  v-model="r.base_url"
                  placeholder="https://your-provider.example/v1"
                  type="url"
                  autocomplete="off" /></label
              ><label class="field"
                >API Key
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
      </section>
      <section class="settings-card">
        <div class="settings-card-heading"><div><h2>自动质检重试</h2><p>图片不合格时按问题改词重试；视觉质检失败或不可用时暂停，不进入人工审核。每次生成和质检可能产生费用。</p></div></div>
        <label class="field">每张图最多生成次数（1–5）<input type="number" min="1" max="5" v-model.number="settings.image_qc_attempts" /></label>
      </section>
      <section class="settings-card">
        <div class="settings-card-heading">
          <span class="settings-kind-icon"
            ><Icon name="audio" :size="23"
          /></span>
          <div>
            <h2>声音与字幕</h2>
            <p>选择原生音频或独立配音；分镜台词生成可编辑字幕。</p>
          </div>
        </div>
        <div class="form-grid three">
          <label class="field"
            >音频方式<select v-model="settings.audio_mode">
              <option value="native">视频模型原生音频</option>
              <option value="edge">独立配音 · Edge TTS</option>
              <option value="cloud">独立配音 · 兼容云接口</option>
              <option value="silent">静音视频</option>
            </select></label
          ><label class="field"
            >声线 ID<input
              v-model="settings.voice"
              :placeholder="
                settings.audio_mode === 'cloud'
                  ? '如 alloy'
                  : 'zh-CN-XiaoxiaoNeural'
              " /></label
          ><label class="field"
            >字幕<select v-model="settings.subtitles">
              <option :value="true">按镜头时间线生成字幕</option>
              <option :value="false">关闭字幕</option>
            </select></label
          >
        </div>
        <div v-if="settings.audio_mode === 'cloud'" class="form-grid three">
          <label class="field"
            >配音 API 地址<input
              v-model="settings.audio.base_url"
              placeholder="https://your-provider.example/v1" /></label
          ><label class="field"
            >配音模型<input v-model="settings.audio.model" /></label
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
        <p class="muted small">
          独立配音需要逐镜审核，音频超出镜头时长时合成会停止。数字人口型、跨语言发音与字幕同步需要人工复核。
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
      <section class="settings-card" aria-label="Seedream 付费兜底">
        <div class="settings-card-heading">
          <span class="settings-kind-icon"><Icon name="images" :size="23" /></span>
          <div>
            <h2>Seedream 付费兜底</h2>
            <p>直接同步 Web2API 设置；不是工作台独立保存的一份配置。</p>
          </div>
          <span :class="['badge', seedream?.configured ? 'approved' : 'neutral']">
            {{ !seedream ? '状态未知' : seedream.configured ? '已配置' : '未配置' }}
          </span>
        </div>
        <p class="muted small">
          至少两个启用的 Gemini 账号本次均确认额度耗尽，或所有启用账号（至少两个）均登录失效时，
          工作台可使用 Seedream。超时、状态不明或其他账号不可用不会触发登录失效兜底；
          请先开启并保存“允许付费渠道”。
        </p>
        <p v-if="seedream" class="muted small">
          模型：{{ seedream.model }} · 网关参考价：¥{{ seedream.output_price_cny }}/张；实际以供应商账单为准。
          已配置不代表密钥已验证或账户有额度。
        </p>
        <label class="field">火山方舟 API Key
          <input v-model="seedreamKey" type="password" autocomplete="new-password" placeholder="输入新密钥；已保存密钥不会回显" />
        </label>
        <div v-if="seedreamError" class="inline-error" role="alert">{{ seedreamError }}</div>
        <div class="button-row">
          <button class="btn primary" :disabled="busy || !seedreamKey.trim()" @click="saveSeedream">保存 Seedream 密钥</button>
          <button v-if="seedream?.configured" class="btn secondary" :disabled="busy" @click="clearSeedream">清除 Seedream 密钥</button>
        </div>
      </section>
      <div class="connections-grid">
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
        <div class="eyebrow">CONNECT YOUR MODEL</div>
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
