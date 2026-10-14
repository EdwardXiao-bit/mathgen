/**
 * `/teacher/paper/:id` —— 预览与改题 + 右侧排版面板（FR-T2/T3/T4/T5/T6）。
 *
 * **卷面只有一套渲染实现**（D56 / C3）：预览 iframe 与打印都取后端
 * `GET /api/papers/{id}/sheet?mode=` 的 HTML，前端不绘制任何卷面 DOM / CSS。
 * 前端只做两件事：控件（排版面板）与容器（iframe），以及把六个排版变量写到该文档上。
 *
 * 三条互相独立的数据流，故意不互相阻塞：
 *   ① **预览**：iframe 文档根元素的 CSS 变量 ←本地 `layout` 状态（**0 次网络往返**）
 *   ② **持久化**：`layout` 变化后 debounce 一次 `PATCH /api/papers/{id}/layout`
 *   ③ **改题**：`DELETE /items/{seq}` 与 `POST /items/{seq}/regenerate`，用响应更新缓存
 *
 * 后端未启动时：`GET /api/papers/{id}` 失败 → 渲染可读的错误块 + 重试按钮（绝不白屏）。
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import {
  LAYOUT_DEFAULTS,
  clampLayout,
  cssVariables,
  route,
  type LayoutIn,
  type PaperDetail,
  type SheetMode,
} from '../../shared/api';
import { ApiError, teacherApi } from '../../shared/apiClient';
import { queryKeys } from '../../shared/queryKeys';
import { LayoutPanel } from '../LayoutPanel';
import { SheetFrame, applyLayoutToDocument } from '../SheetFrame';

export type PreviewMode = 'question' | 'answer';
export type PrintMode = 'question' | 'answer';

const PERSIST_DEBOUNCE_MS = 400;

function readError(error: unknown): string {
  if (error instanceof ApiError) return `${error.code}：${error.message}`;
  return error instanceof Error ? error.message : String(error);
}

function parsePaperId(raw: string | undefined): number | null {
  if (!raw) return null;
  const value = Number(raw);
  return Number.isInteger(value) && value > 0 ? value : null;
}

export function PaperPage() {
  const params = useParams<{ id: string }>();
  const paperId = parsePaperId(params.id);
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const paperQuery = useQuery({
    queryKey: queryKeys.paper(paperId ?? -1),
    queryFn: () => teacherApi.getPaper(paperId as number),
    enabled: paperId !== null,
  });

  const serverPaper = paperQuery.data;

  // ---- ① 本地排版状态：唯一真源，预览不等服务器 ----------------------------
  const [layout, setLayout] = useState<LayoutIn>(LAYOUT_DEFAULTS);
  /** 与 `layout` 同步的 ref：供事件处理函数在不依赖 setState 时序的情况下取「当前值」。 */
  const layoutRef = useRef<LayoutIn>(layout);

  /** 本地状态与 ref 一起改，避免两者漂移。 */
  const applyLayout = useCallback((next: LayoutIn) => {
    layoutRef.current = next;
    setLayout(next);
  }, []);

  // 换卷 / 首次拿到详情时，用服务端的值初始化本地状态
  const hydratedFor = useRef<number | null>(null);
  useEffect(() => {
    if (!serverPaper) return;
    if (hydratedFor.current === serverPaper.id) return;
    hydratedFor.current = serverPaper.id;
    const initial = clampLayout(serverPaper.layout);
    layoutRef.current = initial;
    setLayout(initial);
  }, [serverPaper]);

  // ---- ② 持久化：debounce 一次 PATCH，失败只提示不阻塞 ---------------------
  const [persistState, setPersistState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [persistError, setPersistError] = useState<string | null>(null);

  const persistMutation = useMutation({
    mutationFn: (next: LayoutIn) => teacherApi.updateLayout(paperId as number, next),
    onMutate: () => {
      setPersistState('saving');
      setPersistError(null);
    },
    onSuccess: (saved) => {
      setPersistState('saved');
      queryClient.setQueryData<PaperDetail>(queryKeys.paper(paperId as number), (previous) =>
        previous ? { ...previous, layout: saved } : previous,
      );
    },
    onError: (error) => {
      setPersistState('error');
      setPersistError(readError(error));
    },
  });

  const pendingLayout = useRef<LayoutIn | null>(null);
  const persistTimer = useRef<number | null>(null);
  const mutateLayout = persistMutation.mutate;

  /**
   * 把待写入的排版落库。
   *
   * **踩过的两个坑**（都实测过）：
   * 1. 定时器回调若捕获局部 `pending` 变量，那个闭包来自「设置定时器那一次渲染」；
   *    debounce 期间的后续改动只更新 ref，回调于是发出第一次的旧值。
   *    回调必须**在执行时刻**读 ref。
   * 2. 不要把「算出新值」放进 setState(updater) 再指望它被同步执行：React 不会在事件处理
   *    函数里立刻调用 updater，于是 `pendingLayout.current` 还是 null，`flushLayout()` 直接早退。
   *    表现是「开关点得动、界面也变，但一次网络请求都不发，状态永远停在『实时生效』那句提示」。
   *    所以先算值、写 ref，再交给 setState 渲染。
   */
  const flushLayout = useCallback(
    (immediate = false) => {
      if (persistTimer.current !== null) {
        window.clearTimeout(persistTimer.current);
        persistTimer.current = null;
      }
      const send = () => {
        const latest = pendingLayout.current;
        if (!latest || paperId === null) return;
        pendingLayout.current = null;
        mutateLayout(latest);
      };
      if (immediate) {
        send();
        return;
      }
      if (pendingLayout.current === null) return;
      persistTimer.current = window.setTimeout(() => {
        persistTimer.current = null;
        send();
      }, PERSIST_DEBOUNCE_MS);
    },
    [mutateLayout, paperId],
  );

  const handleLayoutChange = useCallback(
    (patch: Partial<LayoutIn>) => {
      const next = clampLayout({ ...layoutRef.current, ...patch });
      pendingLayout.current = next;
      applyLayout(next);
      flushLayout();
    },
    [applyLayout, flushLayout],
  );

  const handleLayoutReset = useCallback(() => {
    handleLayoutChange(LAYOUT_DEFAULTS);
  }, [handleLayoutChange]);

  useEffect(() => () => flushLayout(true), [flushLayout]);

  // ---- ③ 改题：删单题 / 换一题 -------------------------------------------
  const [editError, setEditError] = useState<string | null>(null);

  const deleteMutation = useMutation({
    mutationFn: (seq: number) => teacherApi.deleteItem(paperId as number, seq),
    onSuccess: (items) => {
      setEditError(null);
      queryClient.setQueryData<PaperDetail>(queryKeys.paper(paperId as number), (previous) =>
        previous ? { ...previous, items } : previous,
      );
    },
    onError: (error) => setEditError(readError(error)),
  });

  const regenerateMutation = useMutation({
    mutationFn: (seq: number) => teacherApi.regenerateItem(paperId as number, seq),
    onSuccess: (item) => {
      setEditError(null);
      queryClient.setQueryData<PaperDetail>(queryKeys.paper(paperId as number), (previous) =>
        previous
          ? { ...previous, items: previous.items.map((old) => (old.seq === item.seq ? item : old)) }
          : previous,
      );
    },
    onError: (error) => setEditError(readError(error)),
  });

  // ---- 已发布卷：另存为可编辑副本（D32 / D53）----------------------------
  const copyMutation = useMutation({
    mutationFn: () => teacherApi.copyPaper(paperId as number),
    onSuccess: (clone) => {
      setEditError(null);
      queryClient.setQueryData(queryKeys.paper(clone.id), clone);
      void queryClient.invalidateQueries({ queryKey: queryKeys.papers });
      navigate(`/teacher/paper/${clone.id}`);
    },
    onError: (error) => setEditError(readError(error)),
  });

  // ---- 预览 / 打印 ---------------------------------------------------------
  const [previewMode, setPreviewMode] = useState<PreviewMode>('question');
  const [sheetError, setSheetError] = useState<string | null>(null);
  const sheetFrameRef = useRef<HTMLIFrameElement | null>(null);

  /**
   * 答案版形式在 M2 固定为 D20 默认（单独成页）。
   * 契约的 `PaperConfigIn` 里没有这个字段，`paper.answer_layout` 留到 M3——
   * 所以这里不再有任何「本地假装生效」的状态（之前存在 sessionStorage 里，已删除）。
   */
  const answerAllowed = true;

  // 卷面 HTML 的地址：预览 iframe 与打印共用同一个 `/sheet` 接口（D56）
  const sheetSrc = useMemo(() => {
    if (paperId === null) return 'about:blank';
    const mode: SheetMode = previewMode === 'answer' ? 'answer' : 'question';
    return `${route('sheet', { paper_id: paperId })}?mode=${encodeURIComponent(mode)}`;
  }, [paperId, previewMode]);

  /**
   * 排版面板改动 → 立刻写到 **iframe 文档**的根元素（D56 第 2 条 / FR-T4）。
   * 不产生任何请求：只是覆盖后端已写进 `:root` 的那六个变量。
   * 预览 iframe 每次重新加载（换页签 / 换卷）后由 `onLoaded` 再补一次。
   */
  useEffect(() => {
    const doc = sheetFrameRef.current?.contentDocument;
    if (!doc?.documentElement) return;
    applyLayoutToDocument(doc, layout);
  }, [layout, sheetSrc]);

  /**
   * 打印：取**同一个** `/sheet` 接口的 HTML，写进隐藏 iframe，然后打印那个 iframe。
   *
   * 为什么不用 `window.print()` 打主文档：主文档打印会带上整个老师端界面，
   * 而且那样等于要前端再写一遍打印样式 —— 正是 D56 禁止的第二套排版。
   * 打印 iframe 里是后端渲染的完整文档（含 `@page` 几何），因此
   * 「预览 = 打印 = PDF」在结构上成立，而且是同一份 HTML。
   *
   * 实现要点（踩过的坑）：不要先写 `srcdoc=''` 再设 `src` —— 那样 iframe 会停在
   * 那个空文档上，`load` 触发时 `contentDocument` 是空白的，打印出来一张白纸。
   * 这里改成「自己取 HTML → `document.write` 进 about:blank iframe → 轮询 readyState」，
   * 时序完全可控，也不依赖 load 事件是否被派发。
   */
  const handlePrint = useCallback(
    (mode: PrintMode) => {
      if (mode === 'answer' && !answerAllowed || paperId === null) return;
      // 打印用的是后端 HTML：它读库里的排版，所以要把待写入的排版立刻落库（不等 debounce）
      flushLayout(true);
      setSheetError(null);

      const iframe = document.createElement('iframe');
      iframe.setAttribute('data-testid', 'print-frame');
      iframe.setAttribute('aria-hidden', 'true');
      iframe.className = 'print-frame';
      document.body.appendChild(iframe);

      const cleanup = () => window.setTimeout(() => iframe.remove(), 30_000);
      let printed = false;
      const doPrint = () => {
        if (printed) return;
        printed = true;
        const doc = iframe.contentDocument;
        if (doc?.documentElement) applyLayoutToDocument(doc, layoutRef.current);
        iframe.contentWindow?.focus();
        iframe.contentWindow?.print();
      };

      void (async () => {
        try {
          const sheetHtml = await teacherApi.getSheetHtml(paperId, mode);
          const doc = iframe.contentDocument;
          if (!doc) throw new ApiError('PRINT_FRAME_BLOCKED', '打印容器不可用（无法写入 iframe 文档）', 0);
          doc.open();
          doc.write(sheetHtml);
          doc.close();
          // 卷面 HTML 全是内联样式、无子资源；等 readyState 完成即可打印
          let waited = 0;
          const poll = window.setInterval(() => {
            waited += 50;
            if (printed) {
              window.clearInterval(poll);
              return;
            }
            if (iframe.contentDocument?.readyState === 'complete' || waited >= 1500) {
              window.clearInterval(poll);
              doPrint();
              cleanup();
            }
          }, 50);
        } catch (error) {
          iframe.remove();
          setSheetError(readError(error));
        }
      })();
    },
    [answerAllowed, flushLayout, paperId],
  );

  const handleSheetError = useCallback((message: string) => {
    setSheetError(message);
  }, []);

  const [previewVars, setPreviewVars] = useState<string>('');

  // 供验收核对：把六个变量以 data 属性挂在容器上（容器样式，不是卷面排版）
  useEffect(() => {
    setPreviewVars(JSON.stringify(cssVariables(layout)));
  }, [layout]);

  // ---- 渲染 ---------------------------------------------------------------
  if (paperId === null) {
    return (
      <div className="error-card" data-testid="paper-error">
        <h1>卷号不合法</h1>
        <p>地址里的 :id 必须是正整数（当前是「{params.id ?? '(空)'}」）。</p>
        <Link className="btn" to="/teacher">
          回到出题配置
        </Link>
      </div>
    );
  }

  if (paperQuery.isLoading) {
    return (
      <div className="loading-card" data-testid="paper-loading">
        正在读取卷 #{paperId} …
      </div>
    );
  }

  if (paperQuery.isError || !serverPaper) {
    const error = paperQuery.error;
    const offline = error instanceof ApiError && error.isOffline;
    return (
      <div className="error-card" data-testid="paper-error">
        <h1>读不到卷 #{paperId}</h1>
        <p className="error-code">
          {error instanceof ApiError ? `${error.code}（HTTP ${error.status}）` : 'UNKNOWN'}
        </p>
        <p>{readError(error)}</p>
        {offline ? (
          <p className="error-hint">
            后端还没启动。请先执行：
            <code>uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir backend</code>
          </p>
        ) : null}
        <div className="error-actions">
          <button type="button" className="btn btn-primary" onClick={() => void paperQuery.refetch()}>
            重试
          </button>
          <Link className="btn btn-ghost" to="/teacher">
            回到出题配置
          </Link>
        </div>
      </div>
    );
  }

  const paper = serverPaper;
  const editable = paper.status === 'draft';
  const busy = deleteMutation.isPending || regenerateMutation.isPending;

  return (
    <div className="paper-page" data-paper-id={paper.id}>
      <header className="paper-header">
        <div>
          <h1>{paper.title}</h1>
          <p className="paper-meta">
            <span data-testid="paper-status">
              状态：{paper.status === 'draft' ? '草稿（可改题）' : '已发布（只读）'}
            </span>
            <span>种子：{paper.seed}</span>
            <span>题目：{paper.items.length} 道</span>
            {paper.code ? <span>卷号：{paper.code}</span> : null}
          </p>
        </div>
        <div className="paper-actions">
          <Link className="btn btn-ghost" to="/teacher">
            新建一套
          </Link>
          {paper.status === 'draft' ? (
            <Link className="btn btn-ghost" to={`/teacher/paper/${paper.id}/publish`}>
              去发卷
            </Link>
          ) : (
            <button
              type="button"
              className="btn btn-ghost"
              data-testid="copy-paper"
              disabled={copyMutation.isPending}
              onClick={() => copyMutation.mutate()}
            >
              {copyMutation.isPending ? '正在另存…' : '另存为可编辑副本'}
            </button>
          )}
          <button
            type="button"
            className="btn btn-primary"
            data-testid="print-question"
            onClick={() => handlePrint('question')}
          >
            打印题目版
          </button>
          <button
            type="button"
            className="btn"
            data-testid="print-answer"
            disabled={!answerAllowed}
            title={answerAllowed ? undefined : '本卷的答案版形式选了「只要题目版」'}
            onClick={() => handlePrint('answer')}
          >
            打印答案版
          </button>
        </div>
      </header>

      {!editable ? (
        <div className="notice notice-info" data-testid="paper-readonly">
          已发布的卷是只读快照（契约 §6 冻结）：不能删题 / 换题，避免学生的卷子和老师手上的不一致。
          需要改动请点右上「另存为可编辑副本」——它会复制一份新的 draft（新 id、无卷号）。
        </div>
      ) : null}

      {editError ? (
        <div className="notice notice-error" role="alert" data-testid="edit-error">
          改题失败：{editError}
        </div>
      ) : null}

      <div className="paper-body">
        <div className="paper-preview-column">
          <div className="preview-toolbar" role="tablist" aria-label="卷面版本">
            <button
              type="button"
              role="tab"
              aria-selected={previewMode === 'question'}
              className={`tab ${previewMode === 'question' ? 'tab-on' : ''}`}
              data-testid="preview-tab-question"
              onClick={() => setPreviewMode('question')}
            >
              题目版
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={previewMode === 'answer'}
              className={`tab ${previewMode === 'answer' ? 'tab-on' : ''}`}
              data-testid="preview-tab-answer"
              disabled={!answerAllowed}
              onClick={() => setPreviewMode('answer')}
            >
              答案版
            </button>
            <span className="preview-toolbar-note">
              卷面由后端渲染（D56）：预览、打印、PDF 是同一份 HTML
            </span>
          </div>

          {sheetError ? (
            <div className="notice notice-error" role="alert" data-testid="sheet-error">
              卷面加载失败：{sheetError}
            </div>
          ) : null}

          <div className="preview-scroll" data-testid="print-area" data-css-vars={previewVars}>
            <SheetFrame
              src={sheetSrc}
              layout={layout}
              frameRef={sheetFrameRef}
              onLoaded={() => setSheetError(null)}
              onError={handleSheetError}
            />
          </div>

          <details className="css-var-debug" data-testid="css-var-debug" open>
            <summary>当前生效的 CSS 变量（已写到 iframe 文档根元素，供验收核对）</summary>
            <pre data-testid="css-var-dump">{JSON.stringify(JSON.parse(previewVars || '{}'), null, 2)}</pre>
          </details>
        </div>

        <div className="paper-side-column">
          <LayoutPanel
            layout={layout}
            onChange={handleLayoutChange}
            onReset={handleLayoutReset}
            persistState={persistState}
            persistError={persistError}
            localOnly={persistState === 'error' && persistError !== null && persistError.includes('连接不上后端')}
          />

          <section className="item-editor" aria-label="改题">
            <h2>改题（{paper.items.length} 道）</h2>
            {paper.items.length === 0 ? (
              <p className="item-editor-empty">题目已全部删除。</p>
            ) : (
              <ul className="item-list">
                {paper.items.map((item) => (
                  <li className="item-row" key={item.seq} data-item-seq={item.seq}>
                    <span className="item-seq">{item.seq}</span>
                    <span className="item-expr" title={item.expr}>
                      {item.display || item.expr}
                    </span>
                    <span className="item-answer">= {item.answer}</span>
                    <button
                      type="button"
                      className="btn btn-mini"
                      data-testid={`regenerate-${item.seq}`}
                      disabled={!editable || busy}
                      onClick={() => regenerateMutation.mutate(item.seq)}
                    >
                      换一题
                    </button>
                    <button
                      type="button"
                      className="btn btn-mini btn-danger"
                      data-testid={`delete-${item.seq}`}
                      disabled={!editable || busy}
                      onClick={() => deleteMutation.mutate(item.seq)}
                    >
                      删除
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
