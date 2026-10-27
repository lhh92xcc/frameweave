<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from "vue";
import { api, libraryPrimary, type Project, type Template } from "./api";
import Icon from "./Icon.vue";
import CreateProject from "./CreateProject.vue";
import ProjectWorkspace from "./ProjectWorkspace.vue";
import SettingsPanel from "./SettingsPanel.vue";
import JobQueue from "./JobQueue.vue";
import ArtifactGallery from "./ArtifactGallery.vue";
const nav = [
  { id: "home", name: "创作概览", icon: "grid" },
  { id: "projects", name: "我的项目", icon: "folder" },
  { id: "assets", name: "素材库", icon: "images" },
  { id: "jobs", name: "生成队列", icon: "clapper" },
];
const page = ref("home"),
  projects = ref<Project[]>([]),
  templates = ref<Template[]>([]),
  selected = ref<Project | null>(null),
  health = ref<any>({}),
  creating = ref(false),
  createMode = ref("drama"),
  help = ref(false),
  query = ref(""),
  error = ref(""),
  toast = ref(""),
  mobileNav = ref(false);
const libraryProject = ref("");
let timer: ReturnType<typeof setInterval>,
  toastTimer: ReturnType<typeof setTimeout>;
let pendingRefresh: Promise<void> | null = null;
const titles: Record<string, string> = {
  home: "创作概览",
  projects: "我的项目",
  assets: "素材库",
  jobs: "生成队列",
  settings: "模型与连接",
  project: "项目工作台",
};
const running = computed(
  () =>
    projects.value
      .flatMap((p) => p.jobs)
      .filter((j) => ["running", "queued"].includes(j.status)).length,
);
const artifactsCount = computed(() =>
  projects.value.reduce(
    (n, p) =>
      n + p.artifacts.filter((a) => !a.stale && libraryPrimary(a)).length,
    0,
  ),
);
const filtered = computed(() =>
  projects.value.filter((p) =>
    p.title.toLowerCase().includes(query.value.toLowerCase()),
  ),
);
const approvedRenders = computed(
  () =>
    projects.value
      .flatMap((p) => p.artifacts)
      .filter((a) => a.kind === "render" && a.review === "approved" && !a.stale)
      .length,
);
function notify(message: string) {
  toast.value = message;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toast.value = ""), 4500);
}
function navigate(id: string) {
  page.value = id;
  mobileNav.value = false;
  location.hash = id;
  if (id === "assets") libraryProject.value = "";
  if (id !== "project") selected.value = null;
}
function openLibrary(projectId: string) {
  libraryProject.value = projectId;
  page.value = "assets";
  selected.value = null;
  location.hash = "assets/" + projectId;
}
async function refresh() {
  // A mutation's refresh runs after any earlier poll, and callers can await it.
  while (pendingRefresh) await pendingRefresh;
  const request = refreshNow();
  pendingRefresh = request;
  try {
    await request;
  } finally {
    if (pendingRefresh === request) pendingRefresh = null;
  }
}
async function refreshNow() {
  const selectedId = selected.value?.id;
  try {
    const [ps, h, detail] = await Promise.all([
      api<Project[]>("/projects"),
      api("/health"),
      selectedId
        ? api<Project>("/projects/" + selectedId)
        : Promise.resolve(null),
    ]);
    projects.value = ps;
    health.value = h;
    if (detail && selected.value?.id === selectedId) selected.value = detail;
    error.value = "";
  } catch (e: any) {
    error.value = e.message;
  }
}
async function openProject(p: Project) {
  try {
    selected.value = await api<Project>("/projects/" + p.id);
    page.value = "project";
    location.hash = "project/" + p.id;
    mobileNav.value = false;
  } catch (e: any) {
    notify(e.message);
  }
}
function create(mode = "drama") {
  createMode.value = mode;
  creating.value = true;
}
async function created(p: Project) {
  creating.value = false;
  await refresh();
  await openProject(p);
  notify("项目已创建。先完善制作规范，让每一帧都有依据。");
}
async function parseHash() {
  const [id, pid] = location.hash.slice(1).split("/");
  if (id === "project" && pid) {
    const p = projects.value.find((p) => p.id === pid);
    if (p) await openProject(p);
  } else if (id === "assets") {
    libraryProject.value = pid || "";
    page.value = "assets";
    selected.value = null;
  } else if (["home", "projects", "jobs", "settings"].includes(id)) {
    page.value = id;
  }
}
onMounted(async () => {
  try {
    templates.value = await api<Template[]>("/templates");
    await refresh();
    await parseHash();
  } catch (e: any) {
    error.value = e.message;
  }
  timer = setInterval(refresh, 4000);
  window.addEventListener("hashchange", parseHash);
});
onUnmounted(() => {
  clearInterval(timer);
  clearTimeout(toastTimer);
  window.removeEventListener("hashchange", parseHash);
});
</script>
<template>
  <div class="app-shell">
    <aside
      class="sidebar"
      :class="{ shown: mobileNav }"
      aria-label="工作台导航"
      tabindex="0"
    >
      <a class="brand" href="#home" @click.prevent="navigate('home')"
        ><span class="brand-mark"><i /><i /><i /></span
        ><span>FrameWeave<small>镜织工坊</small></span></a
      >
      <button class="btn primary new-project" @click="create()">
        <Icon name="plus" />开启新创作<kbd>＋</kbd>
      </button>
      <div class="nav-label">工作空间 <span>WORKSPACE</span></div>
      <nav>
        <button
          v-for="item in nav"
          :key="item.id"
          :class="[
            'nav-item',
            {
              active:
                page === item.id ||
                (item.id === 'projects' && page === 'project'),
            },
          ]"
          @click="navigate(item.id)"
        >
          <Icon :name="item.icon" /><span>{{ item.name }}</span
          ><span v-if="item.id === 'jobs' && running" class="count">{{
            running
          }}</span>
        </button>
      </nav>
      <div class="sidebar-divider" />
      <div class="nav-label">
        最近项目
        <button class="icon-btn" title="新建项目" @click="create()">
          <Icon name="plus" :size="15" />
        </button>
      </div>
      <button
        v-for="p in projects.slice(0, 4)"
        :key="p.id"
        class="recent-link"
        @click="openProject(p)"
      >
        <span :class="['project-dot', p.mode]" />{{ p.title }}
      </button>
      <p v-if="!projects.length" class="sidebar-hint">
        第一段故事，从这里开始。
      </p>
      <div class="sidebar-bottom">
        <div class="workflow-note">
          <span class="note-icon"><Icon name="link" :size="17" /></span
          ><strong>从想法到成片，一路相连</strong>
          <p>规范、素材与模型，<br />在同一条创作线上。</p>
          <button @click="help = true">
            了解工作流程 <Icon name="arrow" :size="14" />
          </button>
        </div>
        <button
          :class="['nav-item', { active: page === 'settings' }]"
          @click="navigate('settings')"
        >
          <Icon name="settings" /><span>模型与连接</span
          ><i :class="['connection-dot', { on: health.gateway === 'ready' }]" />
        </button>
        <button class="nav-item" @click="help = true">
          <Icon name="help" /><span>使用指南</span
          ><Icon name="arrow" :size="15" />
        </button>
        <div class="user-card">
          <span class="avatar">Q</span
          ><span><b>我的创作空间</b><small>本地存储 · 私有凭据</small></span
          ><span class="local-tag">LOCAL</span>
        </div>
      </div>
    </aside>
    <div v-if="mobileNav" class="nav-backdrop" @click="mobileNav = false" />
    <div class="main-shell">
      <header class="topbar">
        <div class="breadcrumb">
          <button
            class="icon-btn mobile-menu"
            aria-label="打开导航"
            @click="mobileNav = !mobileNav"
          >
            <Icon name="menu" /></button
          ><span>工作空间</span><Icon name="chevron" :size="14" /><strong>{{
            selected?.title || titles[page]
          }}</strong>
        </div>
        <div class="topbar-actions">
          <label v-if="['home', 'projects'].includes(page)" class="search"
            ><Icon name="search" :size="16" /><input
              v-model="query"
              placeholder="搜索项目…"
              aria-label="搜索项目"
            /><kbd>⌕</kbd></label
          ><button class="runtime-pill" @click="navigate('settings')">
            <i
              :class="[
                'connection-dot',
                { on: health.gateway === 'ready' && health.engine === 'ready' },
              ]"
            />{{
              health.gateway === "ready" && health.engine === "ready"
                ? "服务已就绪"
                : "检查连接"
            }}</button
          ><button class="top-avatar" title="我的创作空间" @click="help = true">
            Q
          </button>
        </div>
      </header>
      <div v-if="error" class="error-banner">
        <Icon name="warning" />{{ error
        }}<button @click="refresh">重试连接</button>
      </div>
      <main v-if="page === 'home' || page === 'projects'" class="home-content">
        <template v-if="page === 'home'">
          <div class="page-intro">
            <div>
              <div class="eyebrow">YOUR CREATIVE SPACE</div>
              <h1>每个想法，都值得被看见<span class="purple">。</span></h1>
              <p>从第一行文字，到最后一帧画面。在这里，把创作串起来。</p>
            </div>
            <span class="date-label">FRAME BY FRAME, IDEA BY IDEA</span>
          </div>
          <section class="hero">
            <div class="hero-copy">
              <span class="hero-label"
                ><span class="tiny-star">✧</span> 让灵感，自然成片</span
              >
              <h2>一个想法。<br />一整个影像世界。</h2>
              <p>
                导入故事、产品或灵感，建立制作规范，<br />让分镜、图像与视频沿着同一份蓝图生长。
              </p>
              <button class="btn dark" @click="create()">
                开始我的创作 <Icon name="right" :size="17" /></button
              ><span class="hero-caption">六种创作起点，无限种表达方式</span>
            </div>
            <div class="hero-art" aria-label="从文本规范到分镜画面的流程示意">
              <div class="orbit orbit-one" />
              <div class="orbit orbit-two" />
              <div class="art-spark one">✦</div>
              <div class="art-spark two">✧</div>
              <div class="floating-label label-one">
                <Icon name="check" :size="13" /> 风格与角色，始终一致
              </div>
              <div class="script-art">
                <div class="script-art-top">
                  <span><Icon name="file" :size="13" /> PRODUCTION BIBLE</span
                  ><i />
                </div>
                <b>让每一帧，都有依据</b>
                <div class="art-lines"><i /><i /><i /></div>
                <div class="script-tags">
                  <span>人物设定</span><span>视觉风格</span>
                </div>
                <div class="art-lines short"><i /><i /></div>
                <span class="script-approved"
                  ><Icon name="approved" :size="11" /> 规范 → 分镜 → 画面</span
                >
              </div>
              <div class="frame-art landscape-art">
                <div class="art-sun" />
                <div class="art-mountain mountain-one" />
                <div class="art-mountain mountain-two" />
                <div class="art-water" />
                <div class="frame-grain" />
                <span class="frame-corner tl" /><span
                  class="frame-corner br"
                /><span class="frame-art-label"
                  >SCENE 01 <i /> A WORLD UNFOLDS</span
                ><span class="frame-art-play"
                  ><Icon name="play" :size="16"
                /></span>
              </div>
              <div class="mini-frame">
                <div class="mini-moon" />
                <div class="mini-hill" />
                <span>SCENE 02 <b>00:05</b></span>
              </div>
              <div class="floating-label label-two">
                <Icon name="layers" :size="14" /> 每一份灵感，都有下一帧
                <Icon name="spark" :size="12" />
              </div>
              <div class="hero-art-caption">流程示意 · 非模型生成结果</div>
            </div>
          </section>
          <div class="stats-row">
            <div class="stat">
              <span class="stat-icon lilac"><Icon name="folder" /></span>
              <div>
                <span>创作项目</span
                ><b>{{ projects.length }}<small>个</small></b>
              </div>
              <span class="stat-trail">每一个故事的起点</span>
            </div>
            <div class="stat">
              <span class="stat-icon peach"><Icon name="clapper" /></span>
              <div>
                <span>生成任务</span><b>{{ running }}<small>进行中</small></b>
              </div>
              <button
                class="stat-go"
                aria-label="查看队列"
                @click="navigate('jobs')"
              >
                <Icon name="arrow" />
              </button>
            </div>
            <div class="stat">
              <span class="stat-icon mint"><Icon name="images" /></span>
              <div>
                <span>创作素材</span
                ><b>{{ artifactsCount }}<small>份</small></b>
              </div>
              <span class="stat-trail">已批准成片 {{ approvedRenders }}</span>
            </div>
          </div>
          <section>
            <div class="section-heading">
              <div>
                <h2>
                  从哪里开始？<span class="section-sub">选择一种创作方式</span>
                </h2>
              </div>
              <button class="text-button" @click="help = true">
                每种方式都能自由编排 <Icon name="arrow" :size="15" />
              </button>
            </div>
            <div class="template-grid">
              <button
                v-for="t in templates"
                :key="t.id"
                :class="['template-card', t.color]"
                @click="create(t.id)"
              >
                <div class="template-top">
                  <span class="template-icon"
                    ><Icon :name="t.icon" :size="24" /></span
                  ><span class="template-tag">{{ t.tag }}</span>
                </div>
                <div class="template-en">{{ t.en }}</div>
                <h3>{{ t.name }}</h3>
                <p>{{ t.description }}</p>
                <span class="template-action"
                  >开始创作 <Icon name="right" :size="15"
                /></span>
                <div class="template-decoration" />
              </button>
            </div>
          </section>
        </template>
        <section class="projects-section">
          <div class="section-heading">
            <div>
              <div v-if="page === 'projects'" class="eyebrow">
                YOUR COLLECTION
              </div>
              <h2>
                {{ page === "home" ? "最近创作" : "我的项目" }}
                <span class="number-chip">{{ projects.length }}</span
                ><span v-if="page === 'home'" class="section-sub"
                  >让未完成的灵感，继续发生</span
                >
              </h2>
            </div>
            <button
              v-if="page === 'home'"
              class="text-button"
              @click="navigate('projects')"
            >
              全部项目 <Icon name="right" :size="15" /></button
            ><button v-else class="btn primary" @click="create()">
              <Icon name="plus" :size="17" />新建项目
            </button>
          </div>
          <div v-if="filtered.length" class="project-grid">
            <button
              v-for="p in filtered.slice(0, page === 'home' ? 6 : 1000)"
              :key="p.id"
              class="project-card"
              @click="openProject(p)"
            >
              <div :class="['project-cover', p.mode]">
                <img
                  v-if="
                    p.artifacts.find(
                      (a) => a.mime.startsWith('image/') && !a.stale,
                    )
                  "
                  :src="
                    p.artifacts.find(
                      (a) => a.mime.startsWith('image/') && !a.stale,
                    )!.url
                  "
                  alt="项目生成素材预览"
                  loading="lazy"
                /><template v-else
                  ><div class="cover-geometry" />
                  <Icon
                    :name="
                      templates.find((t) => t.id === p.mode)?.icon || 'film'
                    "
                    :size="38"
                  /><span>等待第一帧</span></template
                ><span class="cover-badge"
                  >{{ p.aspect_ratio }} · {{ p.episodes_count }} 集</span
                >
              </div>
              <div class="project-card-body">
                <span class="project-type">{{
                  templates.find((t) => t.id === p.mode)?.name
                }}</span>
                <h3>{{ p.title }}</h3>
                <div class="project-progress">
                  <i
                    :style="{
                      width:
                        (p.progress.filter((s) => s.total && s.done === s.total)
                          .length /
                          6) *
                          100 +
                        '%',
                    }"
                  />
                </div>
                <div class="project-card-meta">
                  <span>{{
                    p.paused
                      ? "已暂停"
                      : p.cancelled
                        ? "已取消"
                        : p.jobs.some((j) => j.status === "running")
                          ? "正在生成"
                          : p.contract.approved
                            ? "制作进行中"
                            : "待确认制作规范"
                  }}</span
                  ><Icon name="arrow" :size="16" />
                </div>
              </div>
            </button>
          </div>
          <div v-else class="empty-projects">
            <span class="empty-icon"><Icon name="folder" :size="26" /></span>
            <div>
              <h3>{{ query ? "没有匹配的项目" : "你的故事，还差一个开始" }}</h3>
              <p>
                {{
                  query
                    ? "试试其他关键词。"
                    : "新建一个项目，让零散的灵感连成完整的作品。"
                }}
              </p>
            </div>
            <button v-if="!query" class="btn secondary" @click="create()">
              <Icon name="plus" :size="17" />创建第一个项目
            </button>
          </div>
        </section>
        <footer class="home-footer">
          <span class="footer-brand"
            ><span class="tiny-star">✧</span> FrameWeave</span
          ><span>灵感有形，创作有序。</span
          ><span class="footer-right">由你的模型驱动 · 为你的创作而生</span>
        </footer>
      </main>
      <ProjectWorkspace
        v-else-if="page === 'project' && selected"
        :project="selected"
        :reload="refresh"
        :worker="health.worker"
        @notify="notify"
        @settings="navigate('settings')"
        @back="navigate('projects')"
        @jobs="navigate('jobs')"
        @library="openLibrary"
      />
      <SettingsPanel v-else-if="page === 'settings'" @notify="notify" />
      <JobQueue
        v-else-if="page === 'jobs'"
        @notify="notify"
        @refresh="refresh"
      />
      <ArtifactGallery
        :projects="projects"
        v-else-if="page === 'assets'"
        :key="libraryProject"
        :initial-project="libraryProject"
        @notify="notify"
      />
    </div>
    <CreateProject
      v-if="creating"
      :templates="templates"
      :mode="createMode"
      @close="creating = false"
      @created="created"
    />
    <div v-if="help" class="modal-backdrop" @click.self="help = false">
      <section
        class="modal help-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="help-title"
      >
        <button
          class="icon-btn modal-close"
          aria-label="关闭指南"
          @click="help = false"
        >
          <Icon name="close" /></button
        ><span class="eyebrow">A LITTLE GUIDE</span>
        <h2 id="help-title">让创作，沿着蓝图发生。</h2>
        <p class="muted">
          首次使用，先在“模型与连接”中添加网页账号或云模型 API Key。
        </p>
        <ol class="guide-steps">
          <li>
            <b>导入你的内容</b>
            <p>
              小说、产品介绍、口播稿或创意资料，选择对应模板并设定集数与规格。
            </p>
          </li>
          <li>
            <b>先确认制作规范</b>
            <p>
              AI 整理完整的 Markdown
              规范。编辑并批准后，每次生成都会携带这份规范。
            </p>
          </li>
          <li>
            <b>分集、分镜，再建立参考图</b>
            <p>
              检查故事覆盖与镜头时长，批准素材与关键帧，让身份和风格保持一致。
            </p>
          </li>
          <li>
            <b>生成视频，审核并交付</b>
            <p>
              开启自动生产后，系统推进到下一个审核点。批准视频后合成分集
              MP4，下载完整资料包。
            </p>
          </li>
        </ol>
        <div class="notice">
          <Icon
            name="help"
          />模型生成需要可用账号与额度。网页额度不足时，只有启用付费策略才会切换付费渠道；画面、口型和内容质量仍需你审核。
        </div>
        <button
          class="btn primary"
          @click="
            help = false;
            navigate('settings');
          "
        >
          配置我的模型 <Icon name="right" :size="17" />
        </button>
      </section>
    </div>
    <Transition name="toast"
      ><div v-if="toast" class="toast-message" role="status">
        <Icon name="check" :size="17" />{{ toast }}
      </div></Transition
    >
  </div>
</template>
