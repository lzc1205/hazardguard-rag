import { onUnmounted, ref } from "vue";

/**
 * 可取消的轮询器。
 *
 * 为什么需要它：
 * 审查工作台要轮询异步任务进度，但页面切换/组件卸载时，
 * 原来的 `setTimeout` 循环会继续跑完 180 次。后果是
 * 用户已经离开页面，旧轮询却在后台改写 `taskProgress`、`taskStage`，
 * 回到页面时看到的是别的任务的进度；更糟的是它会持续发请求。
 *
 * 这里用模块级 token 记录「当前轮询批次」，
 * 组件卸载或新一轮开始时作废旧批次，旧循环在下一次迭代时自行退出。
 *
 * 用法：
 *   const { progress, stage, stop } = usePolling()
 *   await poll(fn, { interval: 1000, max: 180 })
 */
export function usePolling() {
  const progress = ref(0);
  const stage = ref("");
  const polling = ref(false);

  // 每次 start 自增；旧循环发现批次已变就退出
  let token = 0;

  function stop() {
    token += 1;
    polling.value = false;
    progress.value = 0;
    stage.value = "";
  }

  async function poll<T>(
    fn: () => Promise<T>,
    opts: {
      interval?: number;
      max?: number;
      onTick?: (r: T) => void;
      /** 返回 true 表示任务结束，无需继续 */
      isDone?: (r: T) => boolean;
      onDone?: (r: T) => void;
      /** 返回 true 表示已失败，提前结束；不返回或返回 false 则继续轮询 */
      onFail?: (r: T) => boolean | void;
    } = {},
  ): Promise<boolean> {
    const { interval = 1000, max = 180, onTick, isDone, onDone, onFail } = opts;

    const my = ++token;
    polling.value = true;

    try {
      for (let i = 0; i < max; i++) {
        if (my !== token) return false; // 已被更新的轮询取代

        await new Promise((r) => setTimeout(r, interval));
        if (my !== token) return false;

        try {
          const res = await fn();
          if (my !== token) return false;
          onTick?.(res);
          if (isDone?.(res)) {
            onDone?.(res);
            return true;
          }
          if (onFail?.(res) === true) return false;
        } catch {
          // 单次失败不中断，等下一次轮询
        }
      }
      return false;
    } finally {
      if (my === token) {
        polling.value = false;
        progress.value = 0;
        stage.value = "";
      }
    }
  }

  onUnmounted(() => {
    token += 1;
  });

  return { progress, stage, polling, poll, stop };
}
