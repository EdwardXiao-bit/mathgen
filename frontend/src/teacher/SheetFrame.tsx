/**
 * 卷面预览：**唯一的卷面渲染实现是后端 `backend/app/render/`**（D56 / C3）。
 *
 * 所以这里不画任何卷面 DOM、不写任何卷面 CSS —— 只有一个 `<iframe>`，
 * src 指向 `GET /api/papers/{id}/sheet?mode=question|answer`。
 * 「预览 = 打印 = PDF」在**结构上**成立：三者看的是同一份 HTML。
 *
 * 四条互补的通路：
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
 * 3. **分页（Word 式多页预览）** —— 卷面 HTML 里由后端写入了分页几何
 *    （`.sheet[data-usable-first-px]` / `data-usable-later-px` / `data-page-margin-px`）。
 *    这里按真实 DOM 高度把题行切页，在分页点插入 `.page-seam`（视觉切页，`@media print`
 *    里没有它）。**数值只有后端一处来源**：`MEASURED_BREAK_CEILING_PX` 是 Chromium
 *    打印通道的实测标定，前端再推一套只会和打印漂移。
 *
 * 4. **缩放** —— iframe 的**内容视口永远是真实 A4**（793.7 × 1122.5 CSS px），
 *    于是后端 `.sheet{width:210mm}` 不被容器钳制、纸内几何与打印逐像素一致；
 *    再由外层 `transform: scale(k)` 等比缩到画布大小。k 只有**一个数**同时管宽高（A9）。
 *    档位来自用户确认的七档，默认「适合宽度」。
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

import { CSS_VAR_NAMES, cssVariables, type LayoutIn } from '../shared/api';

/**
 * A4 在 CSS 像素下的尺寸（96dpi）：210mm = 210/25.4*96 = 793.70px；297mm = 1122.52px。
 *
 * 为什么写死这两个数而不是用 `210mm`：需要用它算缩放系数做数值比较，而 `210mm`
 * 在运行时会被浏览器换算成 px，拿不到「与浏览器一致的那个数」的稳定来源。
 */
export const A4_WIDTH_PX = (210 / 25.4) * 96;
export const A4_HEIGHT_PX = (297 / 25.4) * 96;

/** 页间灰缝高度，必须与后端 `app.layout.styles.SEAM_PX` 一致。 */
export const SEAM_GAP_PX = 18;

/** 预览舞台的内边距（`.mg-paper-stage` 的 padding），算可用宽度时要减掉。 */
export const STAGE_PAD_PX = 12;

/** 缩放档位：用户确认的七档（百分比 = 数值 × 100）。 */
export const ZOOM_TIERS = [0.5, 0.67, 0.8, 1, 1.25, 1.5, 2] as const;

export type ZoomMode = 'fit-width' | 'fit-page' | number;

/** 分页结果：页数 + 当前顺位的页（1 起）。 */
export interface SheetPagination {
  pages: number;
  activePage: number;
}

/** 暴露给调用方的命令式入口（页码导航点一下就要能跳）。 */
export interface SheetPreviewHandle {
  scrollToPage(page: number): void;
}

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
  /** 缩放模式：`fit-width`（默认）/ `fit-page` / 具体倍数。 */
  zoom?: ZoomMode;
  /** Ctrl/Alt+滚轮、键盘 +/- 的步进（由调用方决定档位序列）。 */
  onZoomStep?: (direction: 1 | -1) => void;
  /** 分页结果变化（页数或当前页）。 */
  onPaginationChange?: (pagination: SheetPagination) => void;
  /** 实际生效的缩放倍数（给「+／−」从当前视觉尺寸往上走）。 */
  onScaleChange?: (scale: number) => void;
  /** 命令式入口：`scrollToPage(page)`。 */
  previewRef?: React.MutableRefObject<SheetPreviewHandle | null>;
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

/** 一次分页计算的结果（坐标都是**未缩放**的 iframe 内部像素）。 */
export interface PageGeometry {
  pages: number;
  /** 每一页「白纸」的顶部（相对卷面内容原点）。第 1 页恒为 0。 */
  tops: number[];
  /** 卷面文档总高（含页间灰缝与屏幕边距）。 */
  height: number;
}

/**
 * 在 iframe 文档里切页：删掉旧灰缝 → 逐行累加 → 在分页点插灰缝 → 量回页顶。
 *
 * 坐标口径与后端一致：从**正文首行顶部**起算的文档流坐标。
 * 边界取自 `.sheet` 上的 `data-usable-*-px`（后端实测标定），
 * 所以「屏幕上的页」与「打印出来的页」用同一套几何。
 */
export function paginateDocument(doc: Document): PageGeometry | null {
  const sheet = doc.querySelector('.sheet') as HTMLElement | null;
  const view = doc.defaultView;
  if (!sheet || !view) return null;

  // 上一次的灰缝先拆掉，否则会把自己的高度算进下一轮分页
  for (const seam of Array.from(sheet.querySelectorAll(':scope > .page-seam'))) seam.remove();

  const sheetRect = sheet.getBoundingClientRect();
  const usableFirst = Number(sheet.dataset.usableFirstPx ?? '');
  const usableLater = Number(sheet.dataset.usableLaterPx ?? '');
  const marginPx = Number(sheet.dataset.pageMarginPx ?? '');
  if (!(usableFirst > 0) || !(usableLater > 0) || !(marginPx > 0)) {
    // 后端没给分页几何（旧卷面 / 单页）：不切页，但照样把高度量回去
    return { pages: 1, tops: [0], height: Math.ceil(sheetRect.height) };
  }

  const contentTop = sheetRect.top + (parseFloat(view.getComputedStyle(sheet).paddingTop) || 0);
  const answerSection = sheet.querySelector('.answer-section') as HTMLElement | null;
  const answerForced = answerSection?.getAttribute('data-break-before') === 'page';
  // 强制分页点在**答案区标题**上（不是第一条答案）：标题也算高度，漏掉它会整体下移一行
  const forcedBlock =
    answerForced && answerSection
      ? ((answerSection.querySelector('.answer-heading') ?? answerSection) as HTMLElement)
      : null;

  const blocks = Array.from(sheet.querySelectorAll('.q-row, .answer-heading, .a-item')) as HTMLElement[];
  if (blocks.length === 0) {
    return { pages: 1, tops: [0], height: Math.ceil(sheetRect.height) };
  }

  let pageStart = 0;
  let limit = usableFirst;
  const breakBefore: HTMLElement[] = [];
  for (const block of blocks) {
    const rect = block.getBoundingClientRect();
    const top = rect.top - contentTop;
    const bottom = top + rect.height;
    // 打印里 `.answer-section{break-before:page}` 一定另起一页，预览要跟着做
    const forced = block === forcedBlock && top > pageStart + 0.5;
    if (forced || bottom > pageStart + limit + 0.5) {
      if (top > pageStart + 0.5) {
        breakBefore.push(block);
        pageStart = top;
        limit = usableLater;
      }
    }
  }

  const seamHeight = marginPx * 2 + SEAM_GAP_PX;
  for (const block of breakBefore) {
    const seam = doc.createElement('div');
    seam.className = 'page-seam';
    seam.setAttribute('aria-hidden', 'true');
    seam.style.height = `${seamHeight.toFixed(2)}px`;
    block.parentNode?.insertBefore(seam, block);
  }

  const tops: number[] = [0];
  for (const seam of Array.from(sheet.querySelectorAll(':scope > .page-seam'))) {
    const rect = seam.getBoundingClientRect();
    // 下一页「白纸」的顶：灰缝顶 + 上一页下边距 + 灰缝
    tops.push(rect.top - contentTop + marginPx + SEAM_GAP_PX);
  }

  const docHeight = Math.ceil(doc.documentElement.scrollHeight);
  return { pages: tops.length, tops, height: docHeight };
}

/**
 * 卷面预览。
 *
 * 结构：`.mg-paper-stage`（滚动视口，接收焦点与 Ctrl+滚轮）
 *   └ `.mg-paper-scaler`（缩放后的占位盒，居中）
 *       └ `<iframe>`（内容视口 = 真实 A4 宽，高度 = 整份卷面高度）
 */
export function SheetFrame({
  src,
  layout,
  reloadToken = 0,
  zoom = 'fit-width',
  onZoomStep,
  onPaginationChange,
  onScaleChange,
  previewRef,
  onReloaded,
  onLoaded,
  onError,
  frameRef,
}: SheetFrameProps) {
  const innerRef = useRef<HTMLIFrameElement | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const [scale, setScale] = useState(1);
  const [docHeight, setDocHeight] = useState(A4_HEIGHT_PX);

  const layoutRef = useRef(layout);
  layoutRef.current = layout;
  const onLoadedRef = useRef(onLoaded);
  onLoadedRef.current = onLoaded;
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;
  const onScaleChangeRef = useRef(onScaleChange);
  onScaleChangeRef.current = onScaleChange;
  const onPaginationChangeRef = useRef(onPaginationChange);
  onPaginationChangeRef.current = onPaginationChange;
  const onZoomStepRef = useRef(onZoomStep);
  onZoomStepRef.current = onZoomStep;

  /** 分页结果的「页顶」表：滚动定位与当前页判定都读它（用 ref 避免闭包拿到旧值）。 */
  const topsRef = useRef<number[]>([0]);
  const pagesRef = useRef(1);
  const scaleRef = useRef(1);
  scaleRef.current = scale;
  const activePageRef = useRef(1);
  /** 当前 iframe 高度。分页量完会拿新高度回头比一次：高度一变，滚动条的有无也会变。 */
  const heightRef = useRef(A4_HEIGHT_PX);
  /** 收敛计数：量高 → 改高 → 再量，最多两轮，避免「量高改变量高」的自我循环。 */
  const passesRef = useRef(0);
  const repaginateRef = useRef<() => void>(() => {});
  /** 排版变化后的重排要节流：拖动滑块时每帧都重算会让手势发涩。 */
  const repaginateTimer = useRef<number | null>(null);

  const setRef = useCallback(
    (node: HTMLIFrameElement | null) => {
      innerRef.current = node;
      if (frameRef) frameRef.current = node;
    },
    [frameRef],
  );

  /** 分页 + 回写高度/页数/当前页。 */
  const repaginate = useCallback(() => {
    const doc = innerRef.current?.contentDocument;
    if (!doc) return;
    const geometry = paginateDocument(doc);
    if (!geometry) return;
    topsRef.current = geometry.tops;
    pagesRef.current = geometry.pages;
    const clamped = Math.min(activePageRef.current, geometry.pages);
    activePageRef.current = clamped;
    onPaginationChangeRef.current?.({ pages: geometry.pages, activePage: clamped });

    const nextHeight = Math.max(geometry.height, A4_HEIGHT_PX);
    if (Math.abs(nextHeight - heightRef.current) > 1 && passesRef.current < 2) {
      // 高度变了：先应用，再量一遍。典型来源是「出现/消失的滚动条让内容区窄了 15px」。
      passesRef.current += 1;
      heightRef.current = nextHeight;
      setDocHeight(nextHeight);
      window.setTimeout(() => repaginateRef.current?.(), 0);
      return;
    }
    passesRef.current = 0;
    heightRef.current = nextHeight;
    setDocHeight(nextHeight);
  }, []);
  repaginateRef.current = repaginate;

  const scheduleRepaginate = useCallback(() => {
    if (repaginateTimer.current !== null) window.clearTimeout(repaginateTimer.current);
    repaginateTimer.current = window.setTimeout(() => {
      repaginateTimer.current = null;
      repaginate();
    }, 90);
  }, [repaginate]);

  /**
   * 缩放：`k` 同时管宽高（A9 的「等比」）。
   * 可用区 = 舞台减去自己的内边距；「适合宽度」只看宽，「整页」取宽高较小者。
   */
  const recomputeScale = useCallback(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const availW = stage.clientWidth - STAGE_PAD_PX * 2;
    const availH = stage.clientHeight - STAGE_PAD_PX * 2;
    if (availW <= 0 || availH <= 0) return;
    const fitWidth = availW / A4_WIDTH_PX;
    const fitPage = Math.min(fitWidth, availH / A4_HEIGHT_PX);
    const next = zoom === 'fit-width' ? fitWidth : zoom === 'fit-page' ? fitPage : zoom;
    setScale((prev) => (Math.abs(prev - next) < 0.0001 ? prev : next));
    onScaleChangeRef.current?.(next);
  }, [zoom]);

  useLayoutEffect(() => {
    recomputeScale();
    const stage = stageRef.current;
    if (!stage || typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', recomputeScale);
      return () => window.removeEventListener('resize', recomputeScale);
    }
    const observer = new ResizeObserver(recomputeScale);
    observer.observe(stage);
    return () => observer.disconnect();
  }, [recomputeScale]);

  // 通路 1：排版变化 → 直接在 iframe 文档上改变量；高度变了要重新分页（节流）。
  useEffect(() => {
    const doc = innerRef.current?.contentDocument;
    if (!doc?.documentElement) return;
    applyLayoutToDocument(doc, layout);
    scheduleRepaginate();
  }, [layout, scheduleRepaginate]);

  // 通路 2：保存成功 → 重新加载 iframe，让预览等于后端当前渲染结果。
  useEffect(() => {
    if (reloadToken === 0) return;
    const frame = innerRef.current;
    if (!frame) return;
    // eslint-disable-next-line no-self-assign
    frame.src = frame.src;
  }, [reloadToken]);

  /** 滚动 → 当前页；滚过某页页顶就算到了那一页。 */
  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return undefined;
    const onScroll = () => {
      // 判定「正在看哪一页」用**视口中线**而不是上边缘：
      // 纸面比视口高时（适合宽度下就是如此），上边缘可能还停在第 1 页，
      // 而视口里主要露出的已经是第 2 页了 —— 那时页码栏该点亮 2。
      const inner = (stage.scrollTop + stage.clientHeight / 2 - STAGE_PAD_PX) / (scaleRef.current || 1);
      let next = 1;
      for (let i = 0; i < topsRef.current.length; i += 1) {
        if (topsRef.current[i] <= inner) next = i + 1;
      }
      if (next !== activePageRef.current) {
        activePageRef.current = next;
        onPaginationChangeRef.current?.({ pages: pagesRef.current, activePage: next });
      }
    };
    stage.addEventListener('scroll', onScroll, { passive: true });
    return () => stage.removeEventListener('scroll', onScroll);
  }, []);

  /**
   * Ctrl/Alt + 滚轮缩放。
   *
   * 必须是**原生非 passive** 监听器：React 的 `onWheel` 在根容器上是 passive，
   * `preventDefault()` 会被忽略，那时浏览器会把自己的整页缩放也做一遍
   * （用户实测过「旁边变大、中间变小」）。挂在舞台而不是 window 上，
   * 指针离开预览区时 Ctrl+滚轮仍归浏览器。
   */
  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return undefined;
    const onWheel = (event: WheelEvent) => {
      if (!(event.ctrlKey || event.altKey)) return;
      event.preventDefault();
      if (!event.deltaY) return;
      onZoomStepRef.current?.(event.deltaY < 0 ? -1 : 1);
    };
    stage.addEventListener('wheel', onWheel, { passive: false });
    return () => stage.removeEventListener('wheel', onWheel);
  }, []);

  // 命令式入口：点页码跳页
  useEffect(() => {
    if (!previewRef) return;
    previewRef.current = {
      scrollToPage: (page: number) => {
        const stage = stageRef.current;
        const top = topsRef.current[page - 1];
        if (!stage || top === undefined) return;
        const k = scaleRef.current || 1;
        const viewport = stage.clientHeight - STAGE_PAD_PX * 2;
        const pageHeight = A4_HEIGHT_PX * k;
        // 一页比视口矮时把它摆在中间；比视口高时把页顶对齐（再多也滚不到，交给浏览器夹紧）
        const centering = pageHeight < viewport ? (viewport - pageHeight) / 2 : 0;
        stage.scrollTo({ top: Math.max(0, top * k + STAGE_PAD_PX - centering), behavior: 'smooth' });
      },
    };
    return () => {
      if (previewRef) previewRef.current = null;
    };
  }, [previewRef]);

  const scaledW = A4_WIDTH_PX * scale;
  const scaledH = Math.max(docHeight, A4_HEIGHT_PX) * scale;

  return (
    <div
      className="mg-paper-stage"
      ref={stageRef}
      tabIndex={0}
      aria-label="卷面预览（只读）"
      data-testid="paper-stage"
      onKeyDown={(event) => {
        if (!onZoomStep) return;
        if (event.key === '+' || event.key === '=') {
          event.preventDefault();
          onZoomStep(1);
        } else if (event.key === '-' || event.key === '_') {
          event.preventDefault();
          onZoomStep(-1);
        }
      }}
    >
      <div className="mg-paper-scaler" style={{ width: `${scaledW}px`, height: `${scaledH}px` }}>
        <iframe
          ref={setRef}
          id="sheet-frame"
          data-testid="sheet-frame"
          className="sheet-frame"
          title="卷面预览（后端渲染）"
          src={src}
          tabIndex={-1}
          style={{
            width: `${A4_WIDTH_PX}px`,
            height: `${Math.max(docHeight, A4_HEIGHT_PX)}px`,
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
            if (!doc.querySelector('.sheet') && text.trim().startsWith('{')) {
              onErrorRef.current?.(`卷面渲染未就绪或返回了错误：${text.slice(0, 200)}`);
              return;
            }
            applyLayoutToDocument(doc, layoutRef.current);
            // 首次加载 / 换 mode / 重载：立刻分页（不等节流，避免先看到单页再跳两页）
            repaginate();
            onLoadedRef.current?.();
            onReloaded?.();
          }}
        />
      </div>
    </div>
  );
}
