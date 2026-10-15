/**
 * `/teacher/library` —— 卷库（FR-T14 / FR-T15）。
 *
 * M3 增强：分页 / 标题搜索（防抖）/ 状态筛选 / **按卷号直接打开**。
 *
 * 两条刻意的设计决定：
 * 1. **按卷号不用「翻列表找」**：走 `ROUTES.get_paper_by_code` 直取。
 *    列表是分页 + 带筛选的，用卷号去列表里搜在过滤条件不匹配时会「明明存在却找不到」。
 * 2. 卷号找不到（404 `CODE_NOT_FOUND`）与后端未启动（网络层）给**不同**提示：
 *    前者是「这个卷号不存在」，后者是「后端没起来」——混在一起会把老师引向错误方向。
 */

import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';

import { LIST_BOUNDS, type PaperStatus } from '../../shared/api';
import { ApiError, teacherApi } from '../../shared/apiClient';
import { queryKeys } from '../../shared/queryKeys';
import { Button, EmptyState, ErrorState, Table, type TableColumn } from '../../ui';

const PAGE_SIZE = LIST_BOUNDS.defaultLimit;
/** 输入即查的防抖（ms）：太短会每个字符打一次请求，太长会让「输入即查」变迟钝 */
const SEARCH_DEBOUNCE_MS = 300;

type StatusFilter = 'all' | PaperStatus;

function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString('zh-CN', { hour12: false });
}

function readError(error: unknown): string {
  if (error instanceof ApiError) return `${error.code}：${error.message}`;
  return error instanceof Error ? error.message : String(error);
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

export function LibraryPage() {
  const navigate = useNavigate();

  const [offset, setOffset] = useState(0);
  const [searchInput, setSearchInput] = useState('');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [codeInput, setCodeInput] = useState('');
  const [codeError, setCodeError] = useState<string | null>(null);
  const [codeLoading, setCodeLoading] = useState(false);

  const q = useDebounced(searchInput, SEARCH_DEBOUNCE_MS);

  // 搜索/筛选一变就回到第一页 —— 否则会停在「第 3 页但结果只有 5 条」这种空页上
  useEffect(() => {
    setOffset(0);
  }, [q, statusFilter]);

  const listQuery = useMemo(
    () => ({
      limit: PAGE_SIZE,
      offset,
      ...(q.trim() === '' ? {} : { q: q.trim() }),
      ...(statusFilter === 'all' ? {} : { status: statusFilter }),
    }),
    [offset, q, statusFilter],
  );

  const query = useQuery({
    queryKey: queryKeys.papers(listQuery),
    queryFn: () => teacherApi.listPapers(listQuery),
  });

  const data = query.data?.data;
  const paginationMode = query.data?.paginationMode;
  const total = data?.total ?? 0;
  const canPrev = offset > 0;
  const canNext = offset + PAGE_SIZE < total;
  const rangeStart = total === 0 ? 0 : offset + 1;
  const rangeEnd = Math.min(offset + (data?.papers.length ?? 0), total);

  /** 按卷号打开：走 `get_paper_by_code`；失败区分「卷号不存在」与「后端不可用」 */
  async function openByCode(event: React.FormEvent) {
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
      navigate(`/teacher/paper/${paper.id}`);
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

  return (
    <div className="library-page">
      <header className="page-intro">
        <h1>卷库</h1>
        <p>生成过的卷都在这里，可再次打开、重打印（FR-T14）。也可以直接用卷号打开历史卷（FR-T15）。</p>
      </header>

      <div className="library-toolbar">
        <label className="library-search">
          <span>搜索卷名</span>
          <input
            type="search"
            data-testid="library-search"
            placeholder="输入卷名关键字（输入即查）"
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
          />
        </label>

        <label className="library-status">
          <span>状态</span>
          <select
            data-testid="library-status"
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value as StatusFilter)}
          >
            <option value="all">全部</option>
            <option value="draft">草稿</option>
            <option value="published">已发布</option>
          </select>
        </label>

        <form className="library-code" onSubmit={openByCode}>
          <label>
            <span>按卷号打开</span>
            <input
              type="text"
              data-testid="library-code-input"
              placeholder="例如 2610-0001"
              value={codeInput}
              onChange={(event) => setCodeInput(event.target.value)}
            />
          </label>
          <button
            type="submit"
            className="btn btn-primary"
            data-testid="library-code-open"
            disabled={codeLoading}
          >
            {codeLoading ? '查找中…' : '打开'}
          </button>
        </form>
      </div>

      {codeError ? (
        <p className="form-error" role="alert" data-testid="library-code-error">
          {codeError}
        </p>
      ) : null}

      {/* 分页摘要（M3 门禁⑤按这些 testid 断言区间/总数/回退形态）。
          含义不变：它说的仍是「当前页区间 / 过滤后的总数」。 */}
      {!query.isLoading && !query.isError ? (
        <div className="library-summary" data-testid="library-summary">
          第 <b data-testid="library-range-start">{rangeStart}</b>–
          <b data-testid="library-range-end">{rangeEnd}</b> / 共{' '}
          <b data-testid="library-total">{total}</b> 套
          {q.trim() !== '' ? `（搜索「${q.trim()}」）` : ''}
          {statusFilter !== 'all' ? `（${statusFilter === 'draft' ? '草稿' : '已发布'}）` : ''}
          {paginationMode === 'legacy' ? (
            <span className="library-legacy-note" data-testid="library-pagination-mode">
              （后端仍是 page/size 分页，前端已换算）
            </span>
          ) : null}
        </div>
      ) : null}

      {/* 加载 / 错误 / 数据三态统一走组件层（§4：不得白屏，错误给「发生了什么 + 怎么办」）。
          `data-testid="library-error"` 与文案保留：门禁④按它断言「后端未就绪时不是白屏」，
          含义不变（错误态容器 + 错误文案），只是换成统一组件。 */}
      <Table
        testId="library-table"
        ariaLabel="卷库列表"
        bodyTestId="library-rows"
        isLoading={query.isLoading}
        loadingText="正在读取卷库…"
        columns={
          [
            { key: 'title', header: '卷名' },
            { key: 'status', header: '状态' },
            { key: 'code', header: '卷号' },
            { key: 'count', header: '题量', numeric: true },
            { key: 'created', header: '创建时间' },
            { key: 'actions', header: '操作' },
          ] satisfies TableColumn[]
        }
        rows={
          query.isError
            ? []
            : (data?.papers ?? []).map((paper) => ({
                key: paper.id,
                testAttrs: { 'data-paper-id': String(paper.id), 'data-paper-title': paper.title },
                cells: [
                  <Link to={`/teacher/paper/${paper.id}`}>{paper.title}</Link>,
                  paper.status === 'draft' ? '草稿' : '已发布',
                  paper.code ?? '—',
                  String(paper.item_count),
                  formatTime(paper.created_at),
                  <Link className="btn btn-mini" to={`/teacher/paper/${paper.id}`}>
                    打开
                  </Link>,
                ],
              }))
        }
        // 错误态走**独立槽位**：wrapper 的 data-state 必须是 `error` 而不是 `empty`。
        // 「请求失败了」与「库里确实没有数据」是两件不同的事，对外必须说实话
        // （与 D59「界面谎报已保存」、D65「静默拿到第一页」同一条原则）。
        errorAction={
          query.isError ? (
            <ErrorState
              testId="library-error"
              title="读不到卷库"
              what={readError(query.error)}
              how="先确认后端已启动（uvicorn 一条命令即可托管本页面），然后点「重试」。"
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
          q.trim() !== '' || statusFilter !== 'all' ? (
            <EmptyState
              testId="library-empty"
              title="没有符合条件的卷子"
              hint={`当前筛选：${q.trim() !== '' ? `搜索「${q.trim()}」` : ''}${statusFilter !== 'all' ? ` 状态=${statusFilter === 'draft' ? '草稿' : '已发布'}` : ''}`}
              action={
                <Button
                  onClick={() => {
                    setSearchInput('');
                    setStatusFilter('all');
                  }}
                >
                  清空筛选条件
                </Button>
              }
            />
          ) : (
            <EmptyState
              testId="library-empty"
              title="还没有任何卷子"
              hint="卷库会列出你生成过的每一套卷，可随时回来重新打开、打印。"
              action={
                <Link className="btn btn-primary" to="/teacher">
                  去出题配置生成一套
                </Link>
              }
            />
          )
        }
      />

      {!query.isLoading && !query.isError ? (
        <div className="library-pager">
          <Button
            data-testid="library-prev"
            disabled={!canPrev}
            onClick={() => setOffset((current) => Math.max(0, current - PAGE_SIZE))}
          >
            上一页
          </Button>
          <span className="library-pager-info" data-testid="library-page-info">
            每页 {PAGE_SIZE} 套 · 第 {Math.floor(offset / PAGE_SIZE) + 1} 页
          </span>
          <Button
            data-testid="library-next"
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
