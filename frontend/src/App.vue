<script setup lang="ts">
import {
  ref,
  shallowRef,
  computed,
  defineAsyncComponent,
  onMounted,
  onUnmounted,
} from "vue";
import { api, type Project, type Template } from "./api";
import { deliveries, projectPercent, projectSummary } from "./experience";
import Icon from "./Icon.vue";
import PanelLoading from "./PanelLoading.vue";
import { usePolling } from "./usePolling";
const CreateProject = defineAsyncComponent(() => import("./CreateProject.vue"));
const ProjectWorkspace = defineAsyncComponent({
  loader: () => import("./ProjectWorkspace.vue"),
  loadingComponent: PanelLoading,
});
const SettingsPanel = defineAsyncComponent({
  loader: () => import("./SettingsPanel.vue"),
  loadingComponent: PanelLoading,
});
const JobQueue = defineAsyncComponent({
  loader: () => import("./JobQueue.vue"),
  loadingComponent: PanelLoading,
});
const ArtifactGallery = defineAsyncComponent({
  loader: () => import("./ArtifactGallery.vue"),
  loadingComponent: PanelLoading,
});
const nav = [
  { id: "home", name: "创作概览", icon: "grid" },
  { id: "projects", name: "我的项目", icon: "folder" },
  { id: "assets", name: "素材库", icon: "images" },
  { id: "jobs", name: "生成队列", icon: "clapper" },
];
const page = ref("home"),
  projects = shallowRef<Project[]>([]),
  templates = ref<Template[]>([]),
  selected = shallowRef<Project | null>(null),
  health = ref<any>({}),
  creating = ref(false),
  createMode = ref("drama"),
  help = ref(false),
  query = ref(""),
  error = ref(""),
  toast = ref(""),
  mobileNav = ref(false);
const libraryProject = ref("");
const projectFilter = ref("all"),
  projectSort = ref("recent");
const projectFilters = [
  { id: "all", label: "全部项目" },
  { id: "working", label: "正在制作" },
  { id: "attention", label: "需要处理" },
  { id: "ready", label: "已有成片" },
  { id: "draft", label: "待继续" },
  { id: "paused", label: "已暂停或停止" },
];
let toastTimer: ReturnType<typeof setTimeout>;
let pendingRefresh: Promise<void> | null = null;
let listUpdatedAt = 0,
  healthUpdatedAt = 0,
  projectRequest = 0;
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
const filtered = computed(() =>
  projects.value
    .filter(
      (p) =>
        p.title.toLowerCase().includes(query.value.toLowerCase()) &&
        (page.value === "home" ||
          projectFilter.value === "all" ||
          projectSummary(p).id === projectFilter.value),
    )
    .sort((a, b) =>
      projectSort.value === "name"
        ? a.title.localeCompare(b.title, "zh-CN")
        : (b.updated_at || b.created_at) - (a.updated_at || a.created_at),
    ),
);
const projectCounts = computed(() =>
  Object.fromEntries(
    projectFilters.map((f) => [
      f.id,
      projects.value.filter(
        (p) => f.id === "all" || projectSummary(p).id === f.id,
      ).length,
    ]),
  ),
);
const attentionProjects = computed(() =>
  projects.value.filter((p) => projectSummary(p).id === "attention"),
);
const approvedRenders = computed(() =>
  projects.value.reduce((n, p) => n + deliveries(p).length, 0),
);
function notify(message: string) {
  toast.value = message;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toast.value = ""), 4500);
}
function scrollNavigation(event: KeyboardEvent) {
  const sidebar = event.currentTarget as HTMLElement;
  sidebar.scrollTop = event.key === "Home" ? 0 : sidebar.scrollHeight;
}
function navigate(id: string) {
  projectRequest++;
  page.value = id;
  mobileNav.value = false;
  location.hash = id;
  if (id === "assets") libraryProject.value = "";
  if (id !== "project") selected.value = null;
}
function viewProjects(filter: string) {
  projectFilter.value = filter;
  navigate("projects");
}
function openLibrary(projectId: string) {
  projectRequest++;
  libraryProject.value = projectId;
  page.value = "assets";
  selected.value = null;
  location.hash = "assets/" + projectId;
}
async function refresh() {
  // A mutation's refresh runs after any earlier poll, and callers can await it.
  while (pendingRefresh) await pendingRefresh;
  await startRefresh(true);
}
async function startRefresh(force: boolean) {
  const request = refreshNow(force);
  pendingRefresh = request;
  try {
    await request;
  } finally {
    if (pendingRefresh === request) pendingRefresh = null;
  }
}
async function refreshNow(force: boolean) {
  const selectedId = selected.value?.id;
  const navigation = projectRequest;
  const refreshList = force || Date.now() - listUpdatedAt >= 30000;
  const refreshHealth =
    !healthUpdatedAt || Date.now() - healthUpdatedAt >= 30000;
  try {
    const [ps, h, detail] = await Promise.all([
      refreshList ? api<Project[]>("/projects") : Promise.resolve(null),
      refreshHealth ? api("/health") : Promise.resolve(null),
      selectedId
        ? api<Project>("/projects/" + selectedId)
        : Promise.resolve(null),
    ]);
    if (ps) {
      projects.value = ps;
      listUpdatedAt = Date.now();
    }
    if (h) {
      health.value = h;
      healthUpdatedAt = Date.now();
    }
    if (
      detail &&
      selected.value?.id === selectedId &&
      navigation === projectRequest
    ) {
      selected.value = detail;
      projects.value = projects.value.map((project) =>
        project.id === detail.id ? detail : project,
      );
    }
    error.value = "";
  } catch (e: any) {
    error.value = e.message;
  }
}
async function openProject(p: Project) {
  if (selected.value?.id === p.id && page.value === "project") return;
  const request = ++projectRequest;
  try {
    const result = await api<Project>("/projects/" + p.id);
    if (request !== projectRequest) return;
    selected.value = result;
    page.value = "project";
    location.hash = "project/" + p.id;
    mobileNav.value = false;
  } catch (e: any) {
    if (request === projectRequest) notify(e.message);
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
  notify(
    p.production_mode === "full"
      ? "已开始全自动制作，可以随时查看进度。"
      : "项目已创建。可查看方案，或开启全自动完成。",
  );
}
async function parseHash() {
  const [id, pid] = location.hash.slice(1).split("/");
  if (id === "project" && pid) {
    const p = projects.value.find((p) => p.id === pid);
    if (p) await openProject(p);
  } else if (id === "assets") {
    projectRequest++;
    libraryProject.value = pid || "";
    page.value = "assets";
    selected.value = null;
  } else if (["home", "projects", "jobs", "settings"].includes(id)) {
    projectRequest++;
    page.value = id;
    selected.value = null;
  }
}
usePolling(
  async () => {
    if (!pendingRefresh) await startRefresh(false);
  },
  4000,
  false,
);
onMounted(async () => {
  try {
    await Promise.all([
      api<Template[]>("/templates").then((value) => {
        templates.value = value;
      }),
      refresh(),
    ]);
    await parseHash();
  } catch (e: any) {
    error.value = e.message;
  }
  window.addEventListener("hashchange", parseHash);
});
onUnmounted(() => {
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
      @keydown.home.self.prevent="scrollNavigation"
      @keydown.end.self.prevent="scrollNavigation"
    >
      <a class="brand" href="#home" @click.prevent="navigate('home')"
        ><span class="brand-mark"><i /><i /><i /></span
        ><span>FrameWeave<small>镜织工坊</small></span></a
      >
      <button class="btn primary new-project" @click="create()">
        <Icon name="plus" />开启新创作<kbd>＋</kbd>
      </button>
      <div class="nav-label">工作空间</div>
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
          <p>写清需求，开启自动制作。<br />完成后在项目里下载。</p>
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
              <div class="eyebrow">创作概览</div>
              <h1>今天想做什么视频？</h1>
              <p>新建作品、查看制作进度，或者下载已经做好的视频。</p>
            </div>
            <button class="text-button" @click="help = true">
              <Icon name="help" :size="17" />第一次使用？看这三步
            </button>
          </div>
          <section class="hero">
            <div class="hero-copy">
              <span class="hero-label"
                ><span class="tiny-star">✧</span> 让灵感，自然成片</span
              >
              <h2>写下你的想法，<br />剩下交给全自动。</h2>
              <p>
                小说改短剧、产品介绍、猫咪短视频。<br />一次写清要求，系统沿用固定参考，自动检查并完成制作。
              </p>
              <button class="btn dark" @click="create()">
                开始我的创作 <Icon name="right" :size="17" /></button
              ><span class="hero-caption"
                >填写需求 → 全自动制作 → 下载成片</span
              >
            </div>
            <div class="hero-art" aria-label="从文本规范到分镜画面的流程示意">
              <div class="orbit orbit-one" />
              <div class="orbit orbit-two" />
              <div class="art-spark one">✦</div>
              <div class="art-spark two">✧</div>
              <div class="floating-label label-one">
                <Icon name="check" :size="13" /> 沿用固定人物和场景参考
              </div>
              <div class="script-art">
                <div class="script-art-top">
                  <span><Icon name="file" :size="13" /> 固定设定与制作要求</span
                  ><i />
                </div>
                <b>先固定形象，再制作每一镜</b>
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
          <div class="overview-actions" aria-label="作品状态总览">
            <button @click="viewProjects('working')">
              <span class="stat-icon lilac"><Icon name="clapper" /></span
              ><span
                ><strong>{{ projectCounts.working }} 个作品正在制作</strong
                ><small>查看进度，自动更新</small></span
              ><Icon name="right" :size="18" />
            </button>
            <button @click="viewProjects('attention')">
              <span class="stat-icon peach"><Icon name="warning" /></span
              ><span
                ><strong>{{ attentionProjects.length }} 个作品需要处理</strong
                ><small>{{
                  attentionProjects.length
                    ? "打开后查看原因和解决办法"
                    : "当前没有需要处理的问题"
                }}</small></span
              ><Icon name="right" :size="18" />
            </button>
            <button @click="viewProjects('ready')">
              <span class="stat-icon mint"><Icon name="download" /></span
              ><span
                ><strong>{{ approvedRenders }} 集成片可以下载</strong
                ><small>打开已完成的作品</small></span
              ><Icon name="right" :size="18" />
            </button>
          </div>
          <section class="start-steps" aria-label="第一次使用">
            <div>
              <span>1</span><strong>连接你的模型账号</strong>
              <p>只需首次配置，或账号失效时更新。</p>
              <button class="text-button" @click="navigate('settings')">
                去设置 <Icon name="right" :size="14" />
              </button>
            </div>
            <div>
              <span>2</span><strong>写清想做的视频</strong>
              <p>导入小说，或填写人物、画面、声音和时长。</p>
              <button class="text-button" @click="create()">
                填写需求 <Icon name="right" :size="14" />
              </button>
            </div>
            <div>
              <span>3</span><strong>自动制作，完成后下载</strong>
              <p>开启全自动即同意本项目不设费用上限。</p>
              <button class="text-button" @click="navigate('projects')">
                查看我的作品 <Icon name="right" :size="14" />
              </button>
            </div>
          </section>
          <details class="starter-templates">
            <summary>
              想找点灵感？按视频类型开始 <Icon name="chevron" :size="16" />
            </summary>
            <section>
              <div class="section-heading">
                <div>
                  <h2>
                    从哪里开始？<span class="section-sub"
                      >选择一种创作方式</span
                    >
                  </h2>
                </div>
                <button class="text-button" @click="help = true">
                  不知道选哪个？看操作说明 <Icon name="arrow" :size="15" />
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
          </details>
        </template>
        <section class="projects-section">
          <div class="section-heading">
            <div>
              <div v-if="page === 'projects'" class="eyebrow">
                所有作品都在这里
              </div>
              <h2>
                {{ page === "home" ? "最近创作" : "我的项目" }}
                <span class="number-chip">{{ projects.length }}</span
                ><span v-if="page === 'home'" class="section-sub"
                  >查看进度，继续制作或下载成片</span
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
          <p v-if="page === 'projects'" class="page-explainer">
            每个项目是一部作品。打开查看进度、解决问题，完成后播放或下载成片。
          </p>
          <div v-if="page === 'projects'" class="collection-toolbar">
            <div class="plain-tabs" aria-label="按制作状态筛选项目">
              <button
                v-for="f in projectFilters"
                :key="f.id"
                :class="{ selected: projectFilter === f.id }"
                :aria-pressed="projectFilter === f.id"
                @click="projectFilter = f.id"
              >
                {{ f.label }} <span>{{ projectCounts[f.id] }}</span>
              </button>
            </div>
            <label class="sort-control"
              >排序<select v-model="projectSort" aria-label="项目排序">
                <option value="recent">最近更新</option>
                <option value="name">项目名称</option>
              </select></label
            >
          </div>
          <div v-if="filtered.length" class="project-grid">
            <article
              v-for="p in filtered.slice(0, page === 'home' ? 6 : 1000)"
              :key="p.id"
              class="project-card"
            >
              <button
                :class="['project-cover', p.mode]"
                :aria-label="`打开项目 ${p.title}`"
                @click="openProject(p)"
              >
                <img
                  v-if="
                    p.artifacts.find(
                      (a) => a.mime.startsWith('image/') && !a.stale,
                    )
                  "
                  :src="
                    p.artifacts.find(
                      (a) => a.mime.startsWith('image/') && !a.stale,
                    )!.thumbnail_url ||
                    p.artifacts.find(
                      (a) => a.mime.startsWith('image/') && !a.stale,
                    )!.url
                  "
                  alt="项目素材预览"
                  loading="lazy"
                  decoding="async"
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
              </button>
              <div class="project-card-body">
                <span class="project-type">{{
                  templates.find((t) => t.id === p.mode)?.name
                }}</span>
                <h3>
                  <button class="project-name" @click="openProject(p)">
                    {{ p.title }}
                  </button>
                </h3>
                <div
                  class="project-progress"
                  role="progressbar"
                  :aria-label="`${p.title} 已规划内容进度`"
                  :aria-valuenow="projectPercent(p)"
                  aria-valuemin="0"
                  aria-valuemax="100"
                >
                  <i :style="{ width: projectPercent(p) + '%' }" />
                </div>
                <div class="project-card-meta">
                  <span :class="['project-state', projectSummary(p).id]">{{
                    projectSummary(p).label
                  }}</span
                  ><span
                    >{{ deliveries(p).length }} /
                    {{ p.episodes_count }} 集成片</span
                  >
                </div>
                <p class="project-next">{{ projectSummary(p).hint }}</p>
                <div class="project-card-actions">
                  <button
                    class="btn secondary small-btn"
                    @click="openProject(p)"
                  >
                    {{ projectSummary(p).action }}
                    <Icon name="right" :size="14" /></button
                  ><a
                    v-if="deliveries(p).length"
                    class="text-button"
                    :href="`/api/projects/${p.id}/export.zip`"
                    download
                    >下载作品包</a
                  >
                </div>
              </div>
            </article>
          </div>
          <div v-else class="empty-projects">
            <span class="empty-icon"><Icon name="folder" :size="26" /></span>
            <div>
              <h3>
                {{
                  query || (page === "projects" && projectFilter !== "all")
                    ? "当前筛选下没有项目"
                    : "还没有作品，从一个想法开始"
                }}
              </h3>
              <p>
                {{
                  query || projectFilter !== "all"
                    ? "可以换个关键词，或查看全部项目。"
                    : "上传小说或写下视频需求，创建后就能开始制作。"
                }}
              </p>
            </div>
            <button
              v-if="!projects.length && !query"
              class="btn secondary"
              @click="create()"
            >
              <Icon name="plus" :size="17" />创建第一个项目
            </button>
            <button
              v-if="query || projectFilter !== 'all'"
              class="text-button"
              @click="
                query = '';
                projectFilter = 'all';
              "
            >
              清除筛选
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
      <SettingsPanel
        v-else-if="page === 'settings'"
        @notify="notify"
        @projects="navigate('projects')"
      />
      <JobQueue
        v-else-if="page === 'jobs'"
        :projects="projects"
        @notify="notify"
        @refresh="refresh"
        @project="
          (id) => {
            const p = projects.find((p) => p.id === id);
            if (p) openProject(p);
          }
        "
        @settings="navigate('settings')"
      />
      <ArtifactGallery
        :projects="projects"
        v-else-if="page === 'assets'"
        :key="libraryProject"
        :initial-project="libraryProject"
        @notify="notify"
        @projects="navigate('projects')"
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
        ><span class="eyebrow">操作说明</span>
        <h2 id="help-title">三步，把需求做成视频</h2>
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
            <b>开启全自动制作</b>
            <p>
              新建时勾选“全自动完成”，或在项目里点击开启。开启即授权本项目不设费用上限，系统可以调用已配置的付费渠道。
            </p>
          </li>
          <li>
            <b>等待完成，下载成片</b>
            <p>
              系统自动做参考图、全部镜头、配音和字幕，检查合格后合成每一集。页面会显示进度，完成后可下载
              MP4 和全部素材。
            </p>
          </li>
          <li>
            <b>需要调整时，展开制作细节</b>
            <p>
              可以查看方案、修改分镜或换自己提供的素材。修改后相关结果会重新制作；随时可以暂停，也可以选择逐步检查。
            </p>
          </li>
        </ol>
        <div class="notice">
          <Icon
            name="help"
          />全自动会锁定人物、场景和物品的参考，检查不过就返工。缺账号、原参考或录音，以及请求结果不明时会说明原因。不会新增定制音色；自动检查仍可能误判。
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
