/**
 * 卷面预览：**唯一的卷面渲染实现是后端 `backend/app/render/`**（D56 / C3）。
 *
 * 所以这里不画任何卷面 DOM、不写任何卷面 CSS —— 只有一个 `<iframe>`，
 * src 指向 `GET /api/papers/{id}/sheet?mode=question|answer`。
 * 「预览 = 打印 = PDF」在**结构上**成立：三者看的是同一份 HTML。
 *
 * 排版面板的实时反馈通过**在 iframe 文档的 `documentElement` 上设置同一批 CSS 变量**实现
 * （变量名来自 `shared/api.ts` 的 `CSS_VAR_NAMES`，与后端 `css_variables()` 同名同值格式）：
 *   - 即时生效：直接在 DOM 上写自定义属性，不产生任何请求
 *   - 不改版式逻辑：只是把后端已经写进 `:root` 的那几个变量覆盖成老师选的值
 *   - 内联 style 优先级高于样式表里的 `:root`，所以覆盖一定生效
 */

import { useCallback, useEffect, useRef } from 'react';

import { CSS_VAR_NAMES, cssVariables, type LayoutIn } from '../shared/api';

export interface SheetFrameProps {
  /** 卷面 HTML 的 URL（由调用方用 `ROUTES.sheet` 拼，前端不写路径字面量）。 */
  src: string;
  /** 当前排版；变化时立刻写进 iframe 文档，不重新加载。 */
  layout: LayoutIn;
  /** 卷面加载完成（打印通道要等它）。 */
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

export function SheetFrame({ src, layout, onLoaded, onError, frameRef }: SheetFrameProps) {
  const innerRef = useRef<HTMLIFrameElement | null>(null);

  const setRef = useCallback(
    (node: HTMLIFrameElement | null) => {
      innerRef.current = node;
      if (frameRef) frameRef.current = node;
    },
    [frameRef],
  );

  // 排版变化 → 直接在 iframe 文档上改变量。**不产生请求**，也不重新加载 iframe。
  useEffect(() => {
    const doc = innerRef.current?.contentDocument;
    if (!doc?.documentElement) return;
    applyLayoutToDocument(doc, layout);
  }, [layout]);

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
          onError?.('无法读取卷面文档（跨域？）');
          return;
        }
        const text = doc.body?.textContent ?? '';
        // 后端渲染未就绪时可能返回一段 JSON 错误体；这种情况要报错而不是当卷面展示
        if (!doc.querySelector('.sheet') && text.trim().startsWith('{')) {
          onError?.(`卷面渲染未就绪或返回了错误：${text.slice(0, 200)}`);
          return;
        }
        // 卷面 HTML 自带 @page / :root 变量；这里把当前面板值再覆盖一次，
        // 保证「先调面板、后加载卷面」的顺序也不会丢排版。
        applyLayoutToDocument(doc, layout);
        onLoaded?.();
      }}
    />
  );
}
