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

      {query.isLoading ? (
        <div className="loading-card">正在读取卷库…</div>
      ) : query.isError ? (
        <div className="error-card" data-testid="library-error">
          <h1>读不到卷库</h1>
          <p>{readError(query.error)}</p>
          <button type="button" className="btn btn-primary" onClick={() => void query.refetch()}>
            重试
          </button>
        </div>
      ) : (
        <>
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

          {!data || data.papers.length === 0 ? (
            <p className="empty-state" data-testid="library-empty">
              {q.trim() !== '' || statusFilter !== 'all' ? (
                <>没有符合条件的卷子。可以清空搜索或把状态切回「全部」。</>
              ) : (
                <>
                  还没有任何卷子。先去 <Link to="/teacher">出题配置</Link> 生成一套。
                </>
              )}
            </p>
          ) : (
            <table className="library-table">
              <thead>
                <tr>
                  <th>卷名</th>
                  <th>状态</th>
                  <th>卷号</th>
                  <th>题量</th>
                  <th>创建时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody data-testid="library-rows">
                {data.papers.map((paper) => (
                  <tr key={paper.id} data-paper-id={paper.id} data-paper-title={paper.title}>
                    <td>
                      <Link to={`/teacher/paper/${paper.id}`}>{paper.title}</Link>
                    </td>
                    <td>{paper.status === 'draft' ? '草稿' : '已发布'}</td>
                    <td>{paper.code ?? '—'}</td>
                    <td>{paper.item_count}</td>
                    <td>{formatTime(paper.created_at)}</td>
                    <td>
                      <Link className="btn btn-mini" to={`/teacher/paper/${paper.id}`}>
                        打开
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <div className="library-pager">
            <button
              type="button"
              className="btn"
              data-testid="library-prev"
              disabled={!canPrev}
              onClick={() => setOffset((current) => Math.max(0, current - PAGE_SIZE))}
            >
              上一页
            </button>
            <span className="library-pager-info" data-testid="library-page-info">
              每页 {PAGE_SIZE} 套 · 第 {Math.floor(offset / PAGE_SIZE) + 1} 页
            </span>
            <button
              type="button"
              className="btn"
              data-testid="library-next"
              disabled={!canNext}
              onClick={() => setOffset((current) => current + PAGE_SIZE)}
            >
              下一页
            </button>
          </div>
        </>
      )}
    </div>
  );
}
