import { onMounted, onUnmounted } from "vue";

// One request at a time; hidden tabs don't keep fetching private project data.
export function usePolling(task: () => Promise<unknown>, interval: number, immediate = true) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let active = false;
  let running = false;
  let refreshOnReturn = false;

  async function run() {
    if (!active || document.hidden) return;
    if (running) {
      refreshOnReturn = true;
      return;
    }
    clearTimeout(timer);
    running = true;
    try {
      await task();
    } finally {
      running = false;
      if (active && !document.hidden) {
        timer = setTimeout(run, refreshOnReturn ? 0 : interval);
        refreshOnReturn = false;
      }
    }
  }
  function visibilityChanged() {
    clearTimeout(timer);
    if (!document.hidden) void run();
  }
  onMounted(() => {
    active = true;
    document.addEventListener("visibilitychange", visibilityChanged);
    if (immediate) void run();
    else if (!document.hidden) timer = setTimeout(run, interval);
  });
  onUnmounted(() => {
    active = false;
    clearTimeout(timer);
    document.removeEventListener("visibilitychange", visibilityChanged);
  });
}
