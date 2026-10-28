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
  DEFAULT_ANSWER_LAYOUT,
  LAYOUT_DEFAULTS,
  clampLayout,
  cssVariables,
  route,
  type AnswerLayoutIn,
  type LayoutIn,
  type PaperDetail,
  type SheetMode,
} from '../../shared/api';
import { ApiError, teacherApi } from '../../shared/apiClient';
import { readError } from '../../shared/errorText';
import { queryKeys } from '../../shared/queryKeys';
import { Card, EmptyState, ErrorState, FloatingPanel, List, StatusBar, Toolbar, ToolbarDivider } from '../../ui';
import type { StatusBarState } from '../../ui';
import { AnswerLayoutEditor } from '../AnswerLayoutEditor';
import { LayoutPanel } from '../LayoutPanel';
import {
  SheetFrame,
  ZOOM_TIERS,
  applyLayoutToDocument,
  type SheetPagination,
  type SheetPreviewHandle,
  type ZoomMode,
} from '../SheetFrame';

export type PreviewMode = 'question' | 'answer';
export type PrintMode = 'question' | 'answer';

/** 排版持久化的状态机（在 PaperPage 顶部声明，状态条组件也要用同一套类型） */
export type PersistState = 'idle' | 'saving' | 'refreshing' | 'refreshed' | 'error';

const PERSIST_DEBOUNCE_MS = 400;

/**
 * 预览缩放的记忆：档位是「这台设备的偏好」，不是卷子属性，所以存本机。
 *
 * 存的是**模式**（`fit-width` / `fit-page` / 具体倍数）而不是算出来的像素比 ——
 * 像素比取决于窗口大小，换一台电脑打开就不该被沿用。
 * 隐私模式下 `localStorage` 会抛，读写都包住：缩放偏好丢了不影响任何功能。
 */
const ZOOM_STORAGE_KEY = 'mathgen.paper.zoom';

function readStoredZoom(): ZoomMode {
  try {
    const raw = window.localStorage.getItem(ZOOM_STORAGE_KEY);
    if (raw === 'fit-page') return 'fit-page';
    if (raw === 'fit-width' || raw === null) return 'fit-width';
    const value = Number(raw);
    return Number.isFinite(value) && value >= ZOOM_TIERS[0] && value <= ZOOM_TIERS[ZOOM_TIERS.length - 1]
      ? value
      : 'fit-width';
  } catch {
    return 'fit-width';
  }
}

function storeZoom(zoom: ZoomMode): void {
  try {
    window.localStorage.setItem(ZOOM_STORAGE_KEY, typeof zoom === 'number' ? String(zoom) : zoom);
  } catch {
    // 存不了就算了：下一次打开回到默认的「适合宽度」
  }
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

  // ---- ② 持久化：debounce 一次 PATCH，成功后再重载卷面（D59 兜底）-----------
  const [persistState, setPersistState] = useState<PersistState>('idle');
  const [persistError, setPersistError] = useState<string | null>(null);
  /**
   * 自增即让 `SheetFrame` 重新加载卷面。
   *
   * 为什么要这一步（D59 的兜底）：iframe 里的 HTML 是**加载时定格**的，
   * 拖动时的实时覆盖只作用于 CSS 变量。只要有一个参数不由变量驱动（历史上
   * `answer_line` / `header_bar` 就是），预览就会与后端不一致——而界面还显示「已保存」，
   * 这比直接报错更糟。所以保存成功后重载一次，让预览永远等于后端当前的渲染结果。
   */
  const [sheetReloadToken, setSheetReloadToken] = useState(0);

  const persistMutation = useMutation({
    mutationFn: (next: LayoutIn) => teacherApi.updateLayout(paperId as number, next),
    onMutate: () => {
      setPersistState('saving');
      setPersistError(null);
    },
    onSuccess: (saved) => {
      setPersistError(null);
      queryClient.setQueryData<PaperDetail>(queryKeys.paper(paperId as number), (previous) =>
        previous ? { ...previous, layout: saved } : previous,
      );
      // 保存成功 → 让 iframe 重新拉一次后端渲染结果；刷新完成后才说「已保存，预览已刷新」
      setPersistState('refreshing');
      setSheetReloadToken((token) => token + 1);
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
      // 卷面是后端渲染的 HTML（D56），改了题必须让它重新拉一次；
      // 否则老师删完一题，左边的列表变了、右边的纸面还是旧的那一张（用户实测反馈）。
      setSheetReloadToken((token) => token + 1);
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
      // 同理：换了一题，纸面要立刻换成新的那道，不用老师手动点刷新。
      setSheetReloadToken((token) => token + 1);
    },
    onError: (error) => setEditError(readError(error)),
  });

  // ---- 已发布卷：另存为可编辑副本（D32 / D53）----------------------------
  const copyMutation = useMutation({
    mutationFn: () => teacherApi.copyPaper(paperId as number),
    onSuccess: (clone) => {
      setEditError(null);
      queryClient.setQueryData(queryKeys.paper(clone.id), clone);
      void queryClient.invalidateQueries({ queryKey: queryKeys.papersRoot });
      navigate(`/teacher/paper/${clone.id}`);
    },
    onError: (error) => setEditError(readError(error)),
  });

  // ---- 第六维「答案版形式」（M3 起正式接入）-------------------------------
  const serverAnswerLayout = serverPaper?.answer_layout;
  const [answerLayout, setAnswerLayout] = useState<AnswerLayoutIn>(DEFAULT_ANSWER_LAYOUT);
  const answerLayoutHydratedFor = useRef<number | null>(null);
  useEffect(() => {
    if (!serverPaper || !serverAnswerLayout) return;
    if (answerLayoutHydratedFor.current === serverPaper.id) return;
    answerLayoutHydratedFor.current = serverPaper.id;
    setAnswerLayout(serverAnswerLayout);
  }, [serverPaper, serverAnswerLayout]);

  const [answerLayoutState, setAnswerLayoutState] = useState<
    'idle' | 'saving' | 'saved' | 'error'
  >('idle');
  const [answerLayoutError, setAnswerLayoutError] = useState<string | null>(null);

  const answerLayoutMutation = useMutation({
    mutationFn: (next: AnswerLayoutIn) =>
      teacherApi.updateAnswerLayout(paperId as number, next),
    onMutate: () => {
      setAnswerLayoutState('saving');
      setAnswerLayoutError(null);
    },
    onSuccess: (saved) => {
      setAnswerLayoutState('saved');
      setAnswerLayout(saved);
      queryClient.setQueryData<PaperDetail>(queryKeys.paper(paperId as number), (previous) =>
        previous ? { ...previous, answer_layout: saved } : previous,
      );
      // 答案版页面的呈现由后端按 answer_layout 渲染 → 重载 answer 预览，别让老师看到旧样子
      setSheetReloadToken((token) => token + 1);
    },
    onError: (error) => {
      setAnswerLayoutState('error');
      setAnswerLayoutError(readError(error));
    },
  });

  // ---- 预览 / 打印 ---------------------------------------------------------
  const [previewMode, setPreviewMode] = useState<PreviewMode>('question');
  const [sheetError, setSheetError] = useState<string | null>(null);
  const sheetFrameRef = useRef<HTMLIFrameElement | null>(null);

  const answerAllowed = true;

  // 卷面 HTML 的地址：预览 iframe 与打印共用同一个 `/sheet` 接口（D56）
  const sheetSrc = useMemo(() => {
    if (paperId === null) return 'about:blank';
    const mode: SheetMode = previewMode === 'answer' ? 'answer' : 'question';
    return `${route('sheet', { paper_id: paperId })}?mode=${encodeURIComponent(mode)}`;
  }, [paperId, previewMode]);

  /**
   * 多页预览：页数 / 当前页由 `SheetFrame` 量真实 DOM 后回报。
   *
   * 不在这里另算一套页数 —— 那是后端 `MEASURED_BREAK_CEILING_PX` 的标定值决定的，
   * 前端再推一套只会和打印漂移。
   */
  const [pagination, setPagination] = useState<SheetPagination>({ pages: 1, activePage: 1 });

  /**
   * 缩放：只存「模式」，不存像素值 —— 像素值取决于窗口大小，换屏就失效。
   * 默认「适合宽度」（用户确认）；档位是用户定死的七档，Ctrl+滚轮与键盘 +/- 走同一序列。
   */
  const [zoom, setZoom] = useState<ZoomMode>(() => readStoredZoom());
  const [effectiveScale, setEffectiveScale] = useState(1);
  const effectiveScaleRef = useRef(1);
  effectiveScaleRef.current = effectiveScale;
  const previewRef = useRef<SheetPreviewHandle | null>(null);

  // 缩放档位是「设备偏好」而不是卷子属性，所以存在本机
  useEffect(() => {
    storeZoom(zoom);
  }, [zoom]);

  /** 步进到相邻档位：从当前**视觉**倍数往上/往下找，所以「适合宽度」下按 + 也能进档。 */
  const stepZoom = useCallback((direction: 1 | -1) => {
    setZoom((current) => {
      const basis = typeof current === 'number' ? current : effectiveScaleRef.current;
      if (direction === 1) {
        const next = ZOOM_TIERS.find((tier) => tier > basis + 0.001);
        return next ?? ZOOM_TIERS[ZOOM_TIERS.length - 1];
      }
      const below = ZOOM_TIERS.filter((tier) => tier < basis - 0.001);
      return below.length > 0 ? below[below.length - 1] : ZOOM_TIERS[0];
    });
  }, []);

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
      <div className="paper-page" data-testid="paper-loading">
        <div className="config-page">
          <Card title={`正在读取卷 #${paperId} …`}>
            <p className="dim-hint">稍候片刻。如果长时间没有响应，页面会给出错误原因与下一步。</p>
          </Card>
        </div>
      </div>
    );
  }

  if (paperQuery.isError || !serverPaper) {
    const error = paperQuery.error;
    const offline = error instanceof ApiError && error.isOffline;
    // `data-testid="paper-error"` + 「读不到卷」标题 + `.error-code` 都保留：
    // 门禁④按它们断言「后端 500 / 代理 502 时渲染错误卡片而不是白屏，并带错误码」。
    // 换成 ErrorState 只是把「说人话 + 给下一步」这件事统一到组件层，语义不变。
    return (
      <div className="paper-page">
        <div className="config-page">
          <ErrorState
            testId="paper-error"
            title={`读不到卷 #${paperId}`}
            what={readError(error)}
            how={
              offline
                ? '题目服务还没连上。请确认服务程序正在运行（窗口不要关），然后点「重试」。'
                : '如果是刚重启过服务，点「重试」；仍然打不开请把下面的错误码告诉维护人员。'
            }
            code={error instanceof ApiError ? `${error.code}（HTTP ${error.status}）` : 'UNKNOWN'}
            actions={
              <>
                <button type="button" className="btn btn-primary" onClick={() => void paperQuery.refetch()}>
                  重试
                </button>
                <Link className="btn btn-ghost" to="/teacher">
                  回到出题配置
                </Link>
              </>
            }
          />
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
            <span className="meta-secondary">种子：{paper.seed}</span>
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
        </div>
      </header>

      {!editable ? (
        <div className="notice notice-info" data-testid="paper-readonly">
          已发布的卷是一份快照，题目不能再改，这样学生手上的卷子和你这边始终对得上。
          要改动请点右上「另存为可编辑副本」：它会复制出一份新的草稿（有自己的编号，还没分配卷号），改好再发。
        </div>
      ) : null}

      {editError ? (
        <div className="notice notice-error" role="alert" data-testid="edit-error">
          改题失败：{editError}
        </div>
      ) : null}

      {/* C1 三栏工作台（§7）：左题目列表 / 中画布 / 右排版面板。
          三栏由**卷页**排（外壳只负责顶栏与状态条）——只有卷页知道每栏放什么。 */}
      <div className="paper-workbench">
        {/* 左：题目列表（改题是最高频动作，所以常驻而不是抽屉） */}
        <FloatingPanel
          ariaLabel="题目列表"
          layer="item-list"
          className="item-editor"
          testId="item-editor"
        >
          <div className="mg-float-head">
            <h2 className="mg-float-title">改题（{paper.items.length} 道）</h2>
          </div>
          <div className="mg-float-body">
            <List
              ariaLabel="题目列表"
              isEmpty={paper.items.length === 0}
              emptyAction={
                <EmptyState
                  title="题目已全部删除"
                  hint="这张卷子已经没有题了，无法打印。"
                  action={
                    <Link className="btn btn-primary" to="/teacher">
                      回去重新生成一套
                    </Link>
                  }
                />
              }
            >
              {paper.items.map((item) => (
                <li className="item-row" key={item.seq} data-item-seq={item.seq}>
                  <span className="item-seq">{item.seq}</span>
                  <span className="item-expr" title={item.expr}>
                    {item.display || item.expr}
                  </span>
                  {/* 答案与两个按钮放在第二行：右侧栏只有 300px，
                      一行里挤下「算式 + 答案 + 换一题 + 删除」必然把算式截断
                      （四位数的题就被截成「3309 +…」，用户实测反馈）。 */}
                  <span className="item-meta">
                    <span className="item-answer">= {item.answer}</span>
                    <span className="item-actions">
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
                    </span>
                  </span>
                </li>
              ))}
            </List>
          </div>
        </FloatingPanel>

        {/* 中：画布。工具栏是画布列的**第一行**（文档流），不是绝对定位盖上去 ——
            这样「工具栏不压住纸面」是布局的自然结果（判据 A7），不用调 top 数值去凑。 */}
        <div className="mg-canvas mg-canvas-column">
          <Toolbar ariaLabel="卷面工具栏" testId="paper-toolbar">
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
            </div>
            <ToolbarDivider />
            <span className="mg-toolbar-meta">打印和 PDF 与这里看到的一致</span>
            <ToolbarDivider />
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
            <ToolbarDivider />
            <div className="zoom-group" role="group" aria-label="预览缩放" data-testid="zoom-group">
              <button
                type="button"
                className="btn btn-mini zoom-step"
                data-testid="zoom-out"
                aria-label="缩小预览"
                disabled={effectiveScale <= ZOOM_TIERS[0] + 0.001}
                onClick={() => stepZoom(-1)}
              >
                −
              </button>
              <span className="zoom-value" data-testid="zoom-value" aria-live="polite">
                {Math.round(effectiveScale * 100)}%
              </span>
              <button
                type="button"
                className="btn btn-mini zoom-step"
                data-testid="zoom-in"
                aria-label="放大预览"
                disabled={effectiveScale >= ZOOM_TIERS[ZOOM_TIERS.length - 1] - 0.001}
                onClick={() => stepZoom(1)}
              >
                +
              </button>
              <button
                type="button"
                className={`btn btn-mini${zoom === 'fit-width' ? ' is-on' : ''}`}
                data-testid="zoom-fit"
                aria-pressed={zoom === 'fit-width'}
                onClick={() => setZoom('fit-width')}
              >
                适合宽度
              </button>
            </div>
          </Toolbar>

          {sheetError ? (
            <div className="notice notice-error" role="alert" data-testid="sheet-error">
              卷面加载失败：{sheetError}
            </div>
          ) : null}

          {/* 预览区：**灰底画布本身就是预览区**（用户 2026-10-28 明确要求）。
              这一层是透明的弹性区域，只给 `data-css-vars` 与 `data-testid="print-area"` 挂个语义；
              纸页由 `SheetFrame` 直接铺在灰底上，按 A4 等比缩放、多页竖向排列、滚动区就是整块灰区。
              iframe 里的卷面 HTML 来自后端 `/sheet`，前端不自己画（D56 / I6）。
              「纸是 A4」由 iframe 内的几何保证（门禁 ⑧ A9-1/A9-3 量 `.sheet` 宽与单行题面）。 */}
          <div className="mg-paper-frame" data-testid="print-area" data-css-vars={previewVars}>
            <SheetFrame
              src={sheetSrc}
              layout={layout}
              reloadToken={sheetReloadToken}
              zoom={zoom}
              onZoomStep={stepZoom}
              onPaginationChange={setPagination}
              onScaleChange={setEffectiveScale}
              previewRef={previewRef}
              frameRef={sheetFrameRef}
              onLoaded={() => setSheetError(null)}
              onReloaded={() => setPersistState('refreshed')}
              onError={handleSheetError}
            />
          </div>
        </div>

        {/* 中右：页码栏（用户指定的位置：灰底预览与右侧排版面板之间）。
            多页卷子改到第 3 页时，没有它只能靠滚轮找；点一下就能跳过去。
            页数来自预览实测的 DOM 分页，不是另算的估计值。 */}
        <nav className="mg-paper-pager" aria-label="卷面页码" data-testid="paper-pager">
          <ol className="pager-list">
            {Array.from({ length: pagination.pages }, (_, index) => index + 1).map((page) => (
              <li key={page}>
                <button
                  type="button"
                  className={`pager-page${page === pagination.activePage ? ' pager-on' : ''}`}
                  aria-current={page === pagination.activePage ? 'page' : undefined}
                  aria-label={`跳到第 ${page} 页`}
                  data-testid={`pager-page-${page}`}
                  onClick={() => previewRef.current?.scrollToPage(page)}
                >
                  {page}
                </button>
              </li>
            ))}
          </ol>
          <span className="pager-count" data-testid="pager-count">
            {pagination.activePage} / {pagination.pages}
          </span>
        </nav>

        {/* 右：排版面板 + 答案版形式 + 验收用的变量 dump */}
        <div className="paper-side-column">
          <LayoutPanel
            layout={layout}
            onChange={handleLayoutChange}
            onReset={handleLayoutReset}
            persistState={persistState}
            persistError={persistError}
            localOnly={persistState === 'error' && persistError !== null && persistError.includes('连接不上后端')}
          />

          <section className="answer-layout-panel" aria-label="答案版形式" data-testid="answer-layout-panel">
            <div className="layout-panel-head">
              <h2>答案版形式</h2>
              <span
                className="answer-layout-state"
                data-testid="answer-layout-state"
                data-state={editable ? answerLayoutState : 'locked'}
              >
                {!editable
                  ? '已发布 · 不可改'
                  : answerLayoutState === 'saving'
                    ? '正在保存…'
                    : answerLayoutState === 'saved'
                      ? '已保存，答案版预览已刷新'
                      : answerLayoutState === 'error'
                        ? '保存失败'
                        : '改动后立即保存'}
              </span>
            </div>

            <AnswerLayoutEditor
              value={answerLayout}
              onChange={(next) => {
                // 已发布卷：控件本身是 disabled，这里再兜一层，避免任何路径发出必然 409 的请求
                if (!editable) return;
                setAnswerLayout(next);
                answerLayoutMutation.mutate(next);
              }}
              readOnly={!editable}
              idPrefix={`paper-${paper.id}-answer-layout`}
            />

            {answerLayoutError ? (
              <p className="form-error" role="alert" data-testid="answer-layout-error">
                {answerLayoutMutation.error instanceof ApiError &&
                answerLayoutMutation.error.code === 'PAPER_NOT_EDITABLE'
                  ? '这套卷已经发布，答案版形式不能再改。请先点右上「另存为可编辑副本」，在副本上调整。'
                  : `答案版形式保存失败：${answerLayoutError}`}
              </p>
            ) : null}

            {answerLayoutState === 'saved' ? (
              <p className="dim-hint" data-testid="answer-layout-saved-hint">
                切到「答案版」页签可看到最新呈现（预览由后端按 answer_layout 渲染）。
              </p>
            ) : null}
          </section>

          {/* 验收用的变量 dump：**默认收起**。
              为什么从画布挪到侧栏：它常开时占掉约 160px 高度，而纸面按剩余高度缩放，
              结果 A4 纸被压到 329×465（D62 明确要求「纸面按剩余高度缩放、不要扁块」）。
              放到侧栏并收起后，画布高度全部给纸面。
              `data-testid="css-var-dump"` 保留：门禁③读的是它的 `textContent`（不是可见性），
              所以收起不影响那条断言的语义 —— 它守的仍是「前端写进 iframe 的六个变量是哪些」。 */}
          <details className="css-var-debug" data-testid="css-var-debug">
            <summary>当前生效的 CSS 变量（已写到 iframe 文档根元素，供验收核对）</summary>
            <pre data-testid="css-var-dump">{JSON.stringify(JSON.parse(previewVars || '{}'), null, 2)}</pre>
          </details>
        </div>
      </div>

      <PaperStatusBar
        layout={layout}
        itemCount={paper.items.length}
        pagination={pagination}
        persistState={persistState}
        persistError={persistError}
        answerLayoutState={answerLayoutState}
      />
    </div>
  );
}

/**
 * C1 底部状态条（34px）：题目数 / 页码 / 每行几题共几行 / 种子 / 保存状态 / 纸面尺寸。
 *
 * 抽成独立组件而不是塞进主 return：主 return 已经很长，而状态条的内容完全由
 * 「当前 layout + 题目数 + 持久化状态」决定，与三栏骨架无关。
 */
function PaperStatusBar({
  layout,
  itemCount,
  pagination,
  persistState,
  persistError,
  answerLayoutState,
}: {
  layout: LayoutIn;
  itemCount: number;
  pagination: SheetPagination;
  persistState: PersistState;
  persistError: string | null;
  answerLayoutState: 'idle' | 'saving' | 'saved' | 'error';
}) {
  // 状态条只说**真的发生过**的事（I2）：
  //   saving/refreshing → 正在保存；refreshed → 已保存且预览已刷新；error → 保存失败（并给出下一步）
  const state: StatusBarState =
    persistState === 'error' || answerLayoutState === 'error'
      ? 'error'
      : persistState === 'saving' || persistState === 'refreshing' || answerLayoutState === 'saving'
        ? 'saving'
        : persistState === 'refreshed' || answerLayoutState === 'saved'
          ? 'saved'
          : 'idle';

  const message =
    persistState === 'error'
      ? `排版保存失败：${persistError ?? '未知原因'}。预览仍可调参，但没落库——请重试或检查后端。`
      : answerLayoutState === 'error'
        ? '答案版形式保存失败（预览仍按后端渲染）。请重试或检查后端。'
        : persistState === 'refreshing'
          ? '已保存，正在按后端渲染结果刷新预览…'
          : persistState === 'saving'
            ? '正在保存排版到后端…'
            : persistState === 'refreshed'
              ? '已保存，预览已刷新（当前显示 = 后端渲染结果）'
              : answerLayoutState === 'saved'
                ? '已保存，答案版预览已刷新'
                : '拖动或拨动开关时预览立刻变化';

  const rows = Math.ceil(itemCount / Math.max(1, layout.cols_per_row));
  return (
    <StatusBar
      state={state}
      testId="paper-statusbar"
      items={[
        <>
          题目 <b>{itemCount}</b> 道
        </>,
        <>
          页码 <b>{pagination.activePage} / {pagination.pages}</b>
        </>,
        <>
          每行 <b>{layout.cols_per_row}</b> 题 · 共 <b>{rows}</b> 行
        </>,
        <span className="meta-secondary" key="seed">
          纸面 <b>210 × 297 mm</b>（A4）
        </span>,
      ]}
      message={message}
    />
  );
}
