/**
 * 成绩列表（FR-T11）＋ 导出（FR-T13）。
 *
 * 同一份组件服务两个路由：
 * - `/teacher/paper/:paperId/attempts` —— 从卷库/卷子页点进来的「这套卷的成绩」；
 * - `/teacher/attempts` —— 教师端的「全部成绩」，此时可以用卷号把范围收窄到一套卷。
 * 两个路由的差异只有「卷子范围从哪来」，列、筛选、导出、分页完全一样 ——
 * 拆成两个组件就等于把同一套判据写两遍，然后各自漂移。
 *
 * 三条刻意的设计决定：
 * 1. **得分直接渲染后端给的 `score`**（D80）。页面里**没有**任何 `correct / total` 的
 *    计算：旧的学生端口径在 `(40, 23)` 上给 57、契约口径给 58，两处各算一遍就会出现
 *    「同一份成绩两个分数」，而且这种错**不会自己报错**。
 * 2. **导出导的是「当前筛选下的全部记录」，不是当前页**。这不是靠记得别写 `offset` ——
 *    `AttemptExportQuery` 类型里根本没有 `limit` / `offset`，传不下去。
 * 3. **筛选一变就回第一页**：否则会停在「第 3 页但结果只有 5 条」这种空页上，
 *    而空页看起来像「没有成绩」——那是静默的错（D65）。
 */

import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';

// 页面级样式：与 `teacher/pages/LibraryPage.css`、`student/student.css` 同一约定。
// 全站样式表 `src/styles.css` 是全站共享的，不该塞页面私有规则。
import './AttemptsPage.css';
import {
  ATTEMPT_BOUNDS,
  type AttemptExportFormat,
  type AttemptSummary,
} from '../../shared/api';
import { ApiError, teacherApi } from '../../shared/apiClient';
import { readError } from '../../shared/errorText';
import { queryKeys } from '../../shared/queryKeys';
import { Button, EmptyState, ErrorState, Table, type TableColumn } from '../../ui';

const PAGE_SIZE = ATTEMPT_BOUNDS.defaultLimit;
/** 输入即查的防抖（ms） */
const SEARCH_DEBOUNCE_MS = 300;

/**
 * 自主练习的显示文案（D75）。
 *
 * 接口**故意不返回**这几个字：`paper_id` / `paper_code` / `paper_title` 三者都是 `null`，
 * 只说「没有挂卷」这个事实。界面该显示什么叫什么是界面的事 —— 所以这几个字在这里，
 * 而且只在这里。
 */
const PRACTICE_LABEL = '自主练习';

function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString('zh-CN', { hour12: false });
}

/**
 * 用时的显示口径与后端导出一致（`attempt_export.py:format_duration`）：
 * 只到「分」，不进位到小时（3661000ms → 「61分1秒」而不是「1时1分1秒」）。
 *
 * 屏幕与导出文件显示同一个数，是为了让老师对着屏幕核对导出的表时不会怀疑自己看错了。
 */
function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return '0秒';
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return `${seconds}秒`;
  return `${Math.floor(seconds / 60)}分${seconds % 60}秒`;
}

/** 防抖：输入停止 `delay` 毫秒后才更新返回值。 */
function useDebounced<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

/**
 * 触发浏览器下载。
 *
 * 不导航到导出 URL：那样后端一报错，老师看到的就是一屏 JSON，而不是一句人话
 * （D65）。所以先把响应取成 `Blob`（失败能被渲染成提示），再临时造一个 `<a download>`。
 */
function triggerDownload(blob: Blob, filename: string, format: AttemptExportFormat): void {
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = filename === '' ? `成绩.${format}` : filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // 立刻 revoke 会让部分浏览器在下载真正开始前就失效；延后一拍再回收。
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
}

export function AttemptsPage() {
  const params = useParams();
  const rawPaperId = params.paperId;
  /** 路由里带的卷子 id（`/teacher/paper/:paperId/attempts`）；非法值当没给。 */
  const routePaperId = useMemo(() => {
    if (rawPaperId === undefined) return null;
    const parsed = Number(rawPaperId);
    return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
  }, [rawPaperId]);

  const [offset, setOffset] = useState(0);
  const [searchInput, setSearchInput] = useState('');
  /** 「全部成绩」页里用卷号收窄范围时选定的卷（`null` = 不限定）。 */
  const [paperFilter, setPaperFilter] = useState<number | null>(null);
  const [codeInput, setCodeInput] = useState('');
  const [codeError, setCodeError] = useState<string | null>(null);
  const [codeLoading, setCodeLoading] = useState(false);
  const [exporting, setExporting] = useState<AttemptExportFormat | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exportNote, setExportNote] = useState<string | null>(null);

  const q = useDebounced(searchInput, SEARCH_DEBOUNCE_MS);
  /** 卷子范围：路由给的就用路由的，否则用「全部成绩」页里筛出来的。 */
  const paperId = routePaperId ?? paperFilter;

  // 筛选一变就回第一页。
  useEffect(() => {
    setOffset(0);
  }, [q, paperId]);

  const listQuery = useMemo(
    () => ({
      limit: PAGE_SIZE,
      offset,
      ...(paperId === null ? {} : { paper_id: paperId }),
      ...(q.trim() === '' ? {} : { q: q.trim() }),
    }),
    [offset, paperId, q],
  );

  const query = useQuery({
    queryKey: queryKeys.attempts(listQuery),
    queryFn: () => teacherApi.listAttempts(listQuery),
  });

  // 卷子范围来自路由时，把卷名读出来放进标题：老师从卷子页跳过来，
  // 需要一眼确认「我看的是哪一套的成绩」，而不是一串 id。
  const paperQuery = useQuery({
    queryKey: queryKeys.paper(paperId ?? 0),
    queryFn: () => teacherApi.getPaper(paperId as number),
    enabled: paperId !== null,
  });

  const data = query.data;
  const total = data?.total ?? 0;
  const canPrev = offset > 0;
  const canNext = offset + PAGE_SIZE < total;
  const rangeStart = total === 0 ? 0 : offset + 1;
  const rangeEnd = Math.min(offset + (data?.attempts.length ?? 0), total);

  const paperTitle =
    paperId === null ? null : (paperQuery.data?.title ?? null);

  /** 按卷号收窄范围：走 `get_paper_by_code`，与卷库「按卷号打开」同一条路。 */
  async function applyPaperFilter(event: React.FormEvent) {
    event.preventDefault();
    const code = codeInput.trim();
    if (code === '') {
      setCodeError('请先填写卷号，例如 2610-0001');
      return;
    }
    setCodeLoading(true);
    setCodeError(null);
    try {
      const paper = await teacherApi.getPaperByCode(code);
      setPaperFilter(paper.id);
    } catch (error) {
      if (error instanceof ApiError) {
        if (error.code === 'CODE_NOT_FOUND' || error.status === 404) {
          setCodeError(`没有找到卷号「${code}」对应的卷子。请检查卷号是否输错（格式形如 2610-0001）。`);
        } else if (error.isBackendUnreachable) {
          setCodeError(`暂时连不上后端，无法按卷号查找：${error.message}`);
        } else {
          setCodeError(readError(error));
        }
      } else {
        setCodeError(readError(error));
      }
    } finally {
      setCodeLoading(false);
    }
  }

  async function runExport(format: AttemptExportFormat) {
    setExporting(format);
    setExportError(null);
    setExportNote(null);
    try {
      // 只带 format / paper_id / q —— `AttemptExportQuery` 里没有分页字段。
      const { blob, filename } = await teacherApi.exportAttempts({
        format,
        ...(paperId === null ? {} : { paper_id: paperId }),
        ...(q.trim() === '' ? {} : { q: q.trim() }),
      });
      triggerDownload(blob, filename, format);
      setExportNote(
        query.isSuccess
          ? `已按当前筛选导出 ${total} 条记录（不是当前这一页）。文件名：${filename || `成绩.${format}`}`
          : `已按当前筛选导出。文件名：${filename || `成绩.${format}`}`,
      );
    } catch (error) {
      // 导出失败必须让人看见后端给的**真正原因**，不许只弹一句「操作失败」。
      setExportError(`导出没成功：${readError(error)}`);
    } finally {
      setExporting(null);
    }
  }

  const filtered = q.trim() !== '' || paperId !== null;
  const filterSummary = [
    q.trim() === '' ? null : `搜索「${q.trim()}」`,
    paperId === null ? null : `只看《${paperTitle ?? '所选卷子'}》`,
  ]
    .filter((part): part is string => part !== null)
    .join(' · ');

  return (
    <div className="attempts-page">
      <header className="page-intro">
        <h1>{routePaperId === null ? '成绩' : '这套卷的成绩'}</h1>
        <p>
          {routePaperId === null
            ? '学生做完卷子或自主练习后，成绩会汇总在这里。可以按姓名或学号找，也可以只看某一套卷。'
            : '这套卷每一份交上来的成绩都在这里，点「看明细」能看到每题他答了什么、对不对。'}
        </p>
        {paperTitle !== null ? (
          <p className="attempts-paper-title" data-testid="attempts-paper-title">
            当前卷子：{paperTitle}
          </p>
        ) : null}
      </header>

      <div className="attempts-toolbar">
        <label className="attempts-search">
          <span>搜索姓名或学号</span>
          <input
            type="search"
            data-testid="attempts-search"
            placeholder="例如 张三 或 9001（输入即查）"
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
          />
        </label>

        {routePaperId === null ? (
          <form className="attempts-code" onSubmit={applyPaperFilter}>
            <label>
              <span>只看某套卷</span>
              <input
                type="text"
                data-testid="attempts-code-input"
                placeholder="填卷号，例如 2610-0001"
                value={codeInput}
                onChange={(event) => setCodeInput(event.target.value)}
              />
            </label>
            <button
              type="submit"
              className="btn btn-primary"
              data-testid="attempts-code-apply"
              disabled={codeLoading}
            >
              {codeLoading ? '查找中…' : '筛选用'}
            </button>
          </form>
        ) : null}

        <div className="attempts-export">
          {/* 这句不是装饰：老师最怕的是「导出只导了当前页」，所以要写在按钮旁边。 */}
          <span className="attempts-export-hint" data-testid="attempts-export-hint">
            导出的是当前筛选下的<b>全部</b>记录，不受分页影响。
          </span>
          <Button
            data-testid="attempts-export-csv"
            disabled={exporting !== null}
            onClick={() => void runExport('csv')}
          >
            {exporting === 'csv' ? '正在导出…' : '导出 CSV'}
          </Button>
          <Button
            data-testid="attempts-export-xlsx"
            disabled={exporting !== null}
            onClick={() => void runExport('xlsx')}
          >
            {exporting === 'xlsx' ? '正在导出…' : '导出 Excel'}
          </Button>
        </div>
      </div>

      {codeError ? (
        <p className="form-error" role="alert" data-testid="attempts-code-error">
          {codeError}
        </p>
      ) : null}

      {exportError ? (
        <p className="form-error" role="alert" data-testid="attempts-export-error">
          {exportError}
        </p>
      ) : null}

      {exportNote ? (
        <p className="attempts-export-note" role="status" data-testid="attempts-export-note">
          {exportNote}
        </p>
      ) : null}

      {paperId !== null ? (
        <p className="attempts-filter-note" data-testid="attempts-filter-note">
          {filterSummary}
          {routePaperId === null ? (
            <>
              {' '}
              <Button
                size="sm"
                data-testid="attempts-code-clear"
                onClick={() => {
                  setPaperFilter(null);
                  setCodeInput('');
                  setCodeError(null);
                }}
              >
                看全部成绩
              </Button>
            </>
          ) : null}
        </p>
      ) : null}

      {!query.isLoading && !query.isError ? (
        <div className="attempts-summary" data-testid="attempts-summary">
          第 <b data-testid="attempts-range-start">{rangeStart}</b>–
          <b data-testid="attempts-range-end">{rangeEnd}</b> / 共{' '}
          <b data-testid="attempts-total">{total}</b> 条成绩
          {filtered ? `（${filterSummary}）` : ''}
        </div>
      ) : null}

      <Table
        testId="attempts-table"
        ariaLabel="成绩列表"
        bodyTestId="attempts-rows"
        isLoading={query.isLoading}
        loadingText="正在读取成绩…"
        columns={
          [
            { key: 'student', header: '学生' },
            { key: 'paper', header: '卷子' },
            { key: 'score', header: '得分', numeric: true },
            { key: 'correct', header: '答对', numeric: true },
            { key: 'duration', header: '用时', numeric: true },
            { key: 'submitted', header: '提交时间' },
            { key: 'actions', header: '操作' },
          ] satisfies TableColumn[]
        }
        rows={
          query.isError
            ? []
            : (data?.attempts ?? []).map((attempt: AttemptSummary) => ({
                key: attempt.id,
                testAttrs: {
                  'data-attempt-id': String(attempt.id),
                  // 门禁直接读行上的分数：它必须等于后端给的 `score`（D80），
                  // 页面里没有任何地方再算一遍。
                  'data-score': String(attempt.score),
                  'data-correct': String(attempt.correct),
                  'data-total': String(attempt.total),
                  'data-practice': attempt.paper_id === null ? 'true' : 'false',
                  'data-paper-code': attempt.paper_code ?? '',
                },
                cells: [
                  <span>
                    {attempt.student_name}
                    <span className="attempts-student-no">{attempt.student_no}</span>
                  </span>,
                  // 自主练习没有卷：显示界面层补的文案，而不是 `undefined`。
                  attempt.paper_title === null ? (
                    <span data-testid="attempts-practice-label">{PRACTICE_LABEL}</span>
                  ) : (
                    <span>
                      {attempt.paper_title}
                      <span className="attempts-paper-code">{attempt.paper_code ?? '—'}</span>
                    </span>
                  ),
                  <span data-testid="attempts-score">{String(attempt.score)}</span>,
                  `${attempt.correct} / ${attempt.total}`,
                  formatDuration(attempt.duration_ms),
                  formatTime(attempt.submitted_at),
                  <Link
                    className="btn btn-mini"
                    data-testid="attempts-open-detail"
                    to={`/teacher/attempts/${attempt.id}`}
                  >
                    看明细
                  </Link>,
                ],
              }))
        }
        errorAction={
          query.isError ? (
            <ErrorState
              testId="attempts-error"
              title="读不到成绩"
              what={readError(query.error)}
              how="先确认题目服务正在运行（窗口不要关），然后点「重试」。"
              code={query.error instanceof ApiError ? query.error.code : null}
              actions={
                <Button variant="primary" onClick={() => void query.refetch()}>
                  重试
                </Button>
              }
            />
          ) : null
        }
        emptyAction={
          filtered ? (
            <EmptyState
              testId="attempts-empty"
              title="没有符合条件的成绩"
              hint={`当前筛选：${filterSummary}。换个关键字，或者看全部成绩。`}
              action={
                <Button
                  data-testid="attempts-clear-filters"
                  onClick={() => {
                    setSearchInput('');
                    setPaperFilter(null);
                    setCodeInput('');
                    setCodeError(null);
                  }}
                >
                  清空筛选条件
                </Button>
              }
            />
          ) : (
            <EmptyState
              testId="attempts-empty"
              title="还没有学生做过这套卷"
              hint="学生扫码作答、或在学生端做一次自主练习，成绩就会出现在这里。"
              action={
                routePaperId === null ? (
                  <Link className="btn btn-primary" to="/teacher/library">
                    去卷库看看
                  </Link>
                ) : (
                  <Link className="btn btn-primary" to={`/teacher/paper/${routePaperId}`}>
                    回到这套卷
                  </Link>
                )
              }
            />
          )
        }
      />

      {!query.isLoading && !query.isError && total > PAGE_SIZE ? (
        <div className="attempts-pager">
          <Button
            data-testid="attempts-prev"
            disabled={!canPrev}
            onClick={() => setOffset((current) => Math.max(0, current - PAGE_SIZE))}
          >
            上一页
          </Button>
          <span className="attempts-pager-info" data-testid="attempts-page-info">
            每页 {PAGE_SIZE} 条 · 第 {Math.floor(offset / PAGE_SIZE) + 1} 页
          </span>
          <Button
            data-testid="attempts-next"
            disabled={!canNext}
            onClick={() => setOffset((current) => current + PAGE_SIZE)}
          >
            下一页
          </Button>
        </div>
      ) : null}
    </div>
  );
}
