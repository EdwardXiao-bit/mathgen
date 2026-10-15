/**
 * 卷面预览：**唯一的卷面渲染实现是后端 `backend/app/render/`**（D56 / C3）。
 *
 * 所以这里不画任何卷面 DOM、不写任何卷面 CSS —— 只有一个 `<iframe>`，
 * src 指向 `GET /api/papers/{id}/sheet?mode=question|answer`。
 * 「预览 = 打印 = PDF」在**结构上**成立：三者看的是同一份 HTML。
 *
 * 两条互补的通路：
 *
 * 1. **拖动时的即时反馈** —— 在 iframe 文档的 `documentElement` 上设置同一批 CSS 变量
 *    （变量名来自 `shared/api.ts` 的 `CSS_VAR_NAMES`，与后端 `css_variables()` 同名同值格式）。
 *    即时、0 请求，手感来源。内联 style 优先级高于样式表里的 `:root`，所以覆盖一定生效。
 *
 * 2. **保存成功后的重载（D59 兜底）** —— 服务端渲染的 HTML 是**加载时定格**的：
 *    只要有任何一个参数不是由 CSS 变量驱动（历史上 `answer_line` / `header_bar` 就是），
 *    第 1 条通路就会让预览与后端不一致，而界面还显示「已保存」——这比直接报错更糟。
 *    所以 `reloadToken` 变化时重新加载 iframe，让预览**永远等于后端当前的渲染结果**。
 *    由调用方在 `PATCH /layout` 成功返回后自增该 token（不在拖动过程中触发）。
 *
 * ---------------------------------------------------------------------------
 * A9（D68）：**等比缩小，不是压扁**
 * ---------------------------------------------------------------------------
 * 后端那份已冻结的屏幕 CSS 是：
 *
 *     @media screen { .sheet { width: 210mm; max-width: 100%; padding: var(--page-margin) } }
 *
 * 如果 iframe 的内容宽度只有容器宽（例如 459px），`max-width: 100%` 会把这张「210mm 的纸」
 * **横向钳制**到 459px，而 `padding`（2cm=75.59px）与 `font-size`（14pt）仍是**绝对长度**。
 * 于是内容区只剩 459−2×75.59≈308px，4 列每列 77px，而 `243 × 12 ____` 需要约 118px →
 * **题面必然异常换行**。屏幕上是乱的、打印出来却是好的 —— 正是 C3 要防的「预览 ≠ 打印」。
 *
 * 修法（只在前端，不动后端 CSS）：
 *   ① 让 **iframe 的内容视口就是真实 A4**（210mm ≈ 793.7px 宽、297mm ≈ 1122.5px 高），
 *      于是 `.sheet` 的计算宽度 = 793.7px，`max-width:100%` 不再触发钳制，
 *      **纸内一切几何与打印逐像素一致**；
 *   ② 再由**外层整体等比缩放**适配画布：`transform: scale(k)`，
 *      `k = min(可用宽/793.7, 可用高/1122.5)`，**宽高同一个 k**（绝不只压一边）。
 *
 * 这样「屏幕上看到的」就是「打印出来的」按同一个比例缩小 —— 这才是等比。
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

import { CSS_VAR_NAMES, cssVariables, type LayoutIn } from '../shared/api';

/**
 * A4 在 CSS 像素下的尺寸（96dpi）：210mm = 210/25.4*96 = 793.70px；297mm = 1122.52px。
 *
 * 为什么写死这两个数而不是用 `210mm`：需要用它算缩放系数做数值比较（`min(宽比, 高比)`），
 * 而 `210mm` 在运行时会被浏览器换算成 px，拿不到「与浏览器一致的那个数」的稳定来源。
 * 这两个值与 `/25.4*96` 完全一致，且由门禁断言 `.sheet` 的计算宽度必须落在 793.7±2。
 */
export const A4_WIDTH_PX = (210 / 25.4) * 96;
export const A4_HEIGHT_PX = (297 / 25.4) * 96;

export interface SheetFrameProps {
  /** 卷面 HTML 的 URL（由调用方用 `ROUTES.sheet` 拼，前端不写路径字面量）。 */
  src: string;
  /** 当前排版；变化时立刻写进 iframe 文档，不重新加载。 */
  layout: LayoutIn;
  /**
   * 自增即触发 iframe 重新加载。
   * 语义是「后端已经接受了新排版，把定格的 HTML 换成最新的」——
   * 所以只在 `PATCH /layout` **成功返回后**自增，不要在拖动过程中动它。
   */
  reloadToken?: number;
  /** 卷面重载完成后回调（调用方据此把状态文案从「正在刷新」推进到「已刷新」）。 */
  onReloaded?: () => void;
  /** 卷面加载完成（首次 / 换 mode / 重载都会触发）。 */
  onLoaded?: () => void;
  /** 加载失败 / 内容不是卷面时的错误信息。 */
  onError?: (message: string) => void;
  frameRef?: React.MutableRefObject<HTMLIFrameElement | null>;
}

/** 把六个变量写到任意文档根元素上（iframe 内 / 单元测试都能用）。 */
export function applyLayoutToDocument(doc: Document, layout: LayoutIn): void {
  const root = doc.documentElement;
  const vars = cssVariables(layout);
  for (const name of Object.values(CSS_VAR_NAMES)) {
    root.style.setProperty(name, vars[name]);
  }
}

export function SheetFrame({
  src,
  layout,
  reloadToken = 0,
  onReloaded,
  onLoaded,
  onError,
  frameRef,
}: SheetFrameProps) {
  const innerRef = useRef<HTMLIFrameElement | null>(null);
  /**
   * 测量容器（填满画布可用区）。
   *
   * 为什么需要单独一层：缩放后的纸面比可用区**小**，如果直接量外层就会量到「上一次缩放后的尺寸」，
   * 形成反馈循环。这里让测量层始终是「可用区」本身，iframe 绝对定位居中 —— 测量结果不受缩放影响。
   */
  const stageRef = useRef<HTMLDivElement | null>(null);
  const [scale, setScale] = useState(1);
  /** 最近一次渲染的排版 / 错误回调：给 onLoad 用，避免把回调写进 effect 依赖。 */
  const layoutRef = useRef(layout);
  layoutRef.current = layout;
  const onLoadedRef = useRef(onLoaded);
  onLoadedRef.current = onLoaded;
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  /** 本次加载是不是由重载触发的（用来在 onLoad 里区分「首次/mode 切换」与「保存后刷新」）。 */
  const pendingReloadRef = useRef(false);

  const setRef = useCallback(
    (node: HTMLIFrameElement | null) => {
      innerRef.current = node;
      if (frameRef) frameRef.current = node;
    },
    [frameRef],
  );

  /**
   * A9 的核心：按可用区算**同一个** k（宽高共用），再整体 scale。
   *
   * 用 `useLayoutEffect` + `ResizeObserver`：首帧就先把 k 算出来，避免「先铺满再缩小」的闪动；
   * 容器尺寸变化（窗口缩放 / 侧栏收窄 / 面板折叠）时重算。
   */
  useLayoutEffect(() => {
    const stage = stageRef.current;
    if (!stage) return undefined;

    const recompute = () => {
      const availableW = stage.clientWidth;
      const availableH = stage.clientHeight;
      if (availableW <= 0 || availableH <= 0) return;
      // 同一个小 k 同时管宽高 —— 这就是「等比」的定义；只压宽或只压高都会变成「压扁」
      const k = Math.min(availableW / A4_WIDTH_PX, availableH / A4_HEIGHT_PX);
      setScale((prev) => (Math.abs(prev - k) < 0.0001 ? prev : k));
    };

    recompute();
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', recompute);
      return () => window.removeEventListener('resize', recompute);
    }
    const observer = new ResizeObserver(recompute);
    observer.observe(stage);
    return () => observer.disconnect();
  }, []);

  // 通路 1：排版变化 → 直接在 iframe 文档上改变量。**不产生请求**，也不重新加载 iframe。
  useEffect(() => {
    const doc = innerRef.current?.contentDocument;
    if (!doc?.documentElement) return;
    applyLayoutToDocument(doc, layout);
  }, [layout]);

  // 通路 2：保存成功 → 重新加载 iframe，让预览等于后端当前渲染结果。
  useEffect(() => {
    if (reloadToken === 0) return; // 0 是初始值，不触发重载
    const frame = innerRef.current;
    if (!frame) return;
    pendingReloadRef.current = true;
    // 自赋 src 即可强制重新加载（保持同一 URL，浏览器不会走缓存里的旧文档）
    // eslint-disable-next-line no-self-assign
    frame.src = frame.src;
  }, [reloadToken]);

  const scaledW = A4_WIDTH_PX * scale;
  const scaledH = A4_HEIGHT_PX * scale;

  return (
    // 测量层：始终占满可用区（不随缩放变化），缩放后的纸面在其中居中
    <div className="mg-paper-stage" ref={stageRef}>
      {/* 缩放后的占位盒：明确给出缩放后的尺寸，让「居中」按真实视觉尺寸算
          （若直接居中未缩放的 793.7px 元素，居中结果会偏） */}
      <div className="mg-paper-scaler" style={{ width: `${scaledW}px`, height: `${scaledH}px` }}>
        <iframe
          ref={setRef}
          id="sheet-frame"
          data-testid="sheet-frame"
          className="sheet-frame"
          title="卷面预览（后端渲染）"
          src={src}
          // iframe 的内容视口 = 真实 A4（不再被容器钳制），这是 A9 的根修
          style={{
            width: `${A4_WIDTH_PX}px`,
            height: `${A4_HEIGHT_PX}px`,
            transform: `scale(${scale})`,
            transformOrigin: 'top left',
          }}
          onLoad={() => {
            const doc = innerRef.current?.contentDocument;
            if (!doc) {
              onErrorRef.current?.('无法读取卷面文档（跨域？）');
              return;
            }
            const text = doc.body?.textContent ?? '';
            // 后端渲染未就绪时可能返回一段 JSON 错误体；这种情况要报错而不是当卷面展示
            if (!doc.querySelector('.sheet') && text.trim().startsWith('{')) {
              onErrorRef.current?.(`卷面渲染未就绪或返回了错误：${text.slice(0, 200)}`);
              return;
            }
            // 卷面 HTML 自带 @page / :root 变量；这里把当前面板值再覆盖一次，
            // 保证「先调面板、后加载卷面」的顺序也不会丢排版。
            applyLayoutToDocument(doc, layoutRef.current);
            onLoadedRef.current?.();
            if (pendingReloadRef.current) {
              pendingReloadRef.current = false;
              onReloaded?.();
            }
          }}
        />
      </div>
    </div>
  );
}
