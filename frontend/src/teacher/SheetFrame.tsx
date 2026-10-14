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
 */

import { useCallback, useEffect, useRef } from 'react';

import { CSS_VAR_NAMES, cssVariables, type LayoutIn } from '../shared/api';

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

  return (
    <iframe
      ref={setRef}
      id="sheet-frame"
      data-testid="sheet-frame"
      className="sheet-frame"
      title="卷面预览（后端渲染）"
      src={src}
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
  );
}
