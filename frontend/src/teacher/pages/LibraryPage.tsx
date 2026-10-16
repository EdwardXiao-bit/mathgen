/**
 * `/teacher/library` —— 卷库（FR-T14 / FR-T15）。
 *
 * M3 增强：分页 / 标题搜索（防抖）/ 状态筛选 / **按卷号直接打开**。
 * M4 增强（D78）：**归档（软删除）/ 恢复 / 删除**，以及「在用 / 已归档」两档视图。
 *
 * 三条刻意的设计决定：
 * 1. **按卷号不用「翻列表找」**：走 `ROUTES.get_paper_by_code` 直取。
 *    列表是分页 + 带筛选的，用卷号去列表里搜在过滤条件不匹配时会「明明存在却找不到」。
 * 2. 卷号找不到（404 `CODE_NOT_FOUND`）与后端未启动（网络层）给**不同**提示：
 *    前者是「这个卷号不存在」，后者是「后端没起来」——混在一起会把老师引向错误方向。
 * 3. **有学生成绩的卷，删除入口事先禁用**（D78）：依据是 `PaperSummary.has_attempts`，
 *    而不是等老师点下去才吃一个 409。这是 D65「报错好过静默错」与
 *    D59「界面不许谎报状态」的同一条原则 —— 静默禁用也会让人以为是 bug，
 *    所以禁用**必须**同时写明人话理由（I4）。
 */

import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

// 页面级样式（D78 新增的操作区 / 删除确认 / 禁用理由）。
// 与 `student/main.tsx` 引入 `student/student.css` 是同一条约定；
// 全站样式表 `src/styles.css` 不在本轮写域内，且它是全站共享的，不该塞页面私有规则。
import './LibraryPage.css';
import { LIST_BOUNDS, type PaperStatus, type PaperSummary } from '../../shared/api';
import { ApiError, teacherApi } from '../../shared/apiClient';
import { readError } from '../../shared/errorText';
import { queryKeys } from '../../shared/queryKeys';
import {
  Button,
  EmptyState,
  ErrorState,
  SegmentedControl,
  Table,
  Toast,
  ToastStack,
  type TableColumn,
} from '../../ui';

const PAGE_SIZE = LIST_BOUNDS.defaultLimit;
/** 输入即查的防抖（ms）：太短会每个字符打一次请求，太长会让「输入即查」变迟钝 */
const SEARCH_DEBOUNCE_MS = 300;

type StatusFilter = 'all' | PaperStatus;
/** 「在用 / 已归档」两档 —— 对应后端的 `archived=exclude` / `archived=only`。 */
type ArchiveTab = 'exclude' | 'only';

/**
 * 有学生成绩时「删除」的禁用理由。
 *
 * 说清两件事：**为什么不能删**（已有学生成绩）＋**那怎么办**（可以归档）——
 * 只说「不能删除」而不给下一步，等于把老师堵在墙上（I3：失败要给下一步）。
 */
const DELETE_BLOCKED_HINT = '这套卷已有学生成绩，不能删除；可以归档。';
const DELETE_BLOCKED_SHORT = '已有学生成绩，不能删除';

/** 顶部的一次性反馈。`undo` 只给**可逆**操作；删除不可逆，永远不给。 */
interface ActionToast {
  tone: 'success' | 'error';
  message: string;
  undo?: () => void;
}

function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString('zh-CN', { hour12: false });
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
  const queryClient = useQueryClient();

  const [offset, setOffset] = useState(0);
  const [searchInput, setSearchInput] = useState('');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [archiveTab, setArchiveTab] = useState<ArchiveTab>('exclude');
  const [codeInput, setCodeInput] = useState('');
  const [codeError, setCodeError] = useState<string | null>(null);
  const [codeLoading, setCodeLoading] = useState(false);
  /** 正在二次确认删除的那一行（`null` = 没有确认进行中）。 */
  const [confirmingId, setConfirmingId] = useState<number | null>(null);
  const [toast, setToast] = useState<ActionToast | null>(null);

  const q = useDebounced(searchInput, SEARCH_DEBOUNCE_MS);

  // 搜索/筛选一变就回到第一页 —— 否则会停在「第 3 页但结果只有 5 条」这种空页上。
  // **切「在用 / 已归档」同样要回第一页**：两档的总数不同，停在第 3 页很可能
  // 正好是空的，而空页看起来像「归档里什么都没有」——那是静默的错。
  useEffect(() => {
    setOffset(0);
  }, [q, statusFilter, archiveTab]);

  const listQuery = useMemo(
    () => ({
      limit: PAGE_SIZE,
      offset,
      ...(q.trim() === '' ? {} : { q: q.trim() }),
      ...(statusFilter === 'all' ? {} : { status: statusFilter }),
      archived: archiveTab,
    }),
    [offset, q, statusFilter, archiveTab],
  );

  const query = useQuery({
    queryKey: queryKeys.papers(listQuery),
    queryFn: () => teacherApi.listPapers(listQuery),
  });

  const data = query.data?.data;
  const paginationMode = query.data?.paginationMode;
  const paginationProbe = query.data?.paginationProbe;
  // M6（task-46）：探测没成功时**如实说**，不谎报成「版本较旧」。
  // 谎报的代价不是文案难看：老师会以为「页码已自动换算」，而真实原因是这次探测
  // 没答上来（可能后端刚刚重启），下一次刷新就可能变。
  const probeFailed = paginationProbe?.status === 'failed' ? paginationProbe.reason : null;
  const total = data?.total ?? 0;
  const canPrev = offset > 0;
  const canNext = offset + PAGE_SIZE < total;
  const rangeStart = total === 0 ? 0 : offset + 1;
  const rangeEnd = Math.min(offset + (data?.papers.length ?? 0), total);

  /** 写操作成功后都要让「卷库列表 + 各卷详情」一起失效（归档不改 status，但列表要变）。 */
  const refreshPapers = () => queryClient.invalidateQueries({ queryKey: queryKeys.papersRoot });

  const archiveMutation = useMutation({
    mutationFn: (paper: PaperSummary) => teacherApi.archivePaper(paper.id),
    onSuccess: (_detail, paper) => {
      setToast({
        tone: 'success',
        message: `已把《${paper.title}》归档，它不再出现在「在用」里。`,
        // 归档可逆，所以给「撤销」；点了就是再调一次 unarchive，不是只改本地状态。
        undo: () => unarchiveMutation.mutate(paper),
      });
      void refreshPapers();
    },
    onError: (error) => setToast({ tone: 'error', message: `归档没成功：${readError(error)}` }),
  });

  const unarchiveMutation = useMutation({
    mutationFn: (paper: PaperSummary) => teacherApi.unarchivePaper(paper.id),
    onSuccess: (_detail, paper) => {
      setToast({
        tone: 'success',
        message: `已把《${paper.title}》恢复到「在用」。`,
        undo: () => archiveMutation.mutate(paper),
      });
      void refreshPapers();
    },
    onError: (error) => setToast({ tone: 'error', message: `恢复没成功：${readError(error)}` }),
  });

  const deleteMutation = useMutation({
    mutationFn: (paper: PaperSummary) => teacherApi.deletePaper(paper.id),
    onSuccess: (_result, paper) => {
      setConfirmingId(null);
      // 删除**不可逆**，所以没有「撤销」——给了就是谎报它可逆。
      setToast({ tone: 'success', message: `已删除《${paper.title}》。` });
      void refreshPapers();
    },
    onError: (error) => {
      setConfirmingId(null);
      setToast({ tone: 'error', message: `删除没成功：${readError(error)}` });
    },
  });

  const busy = archiveMutation.isPending || unarchiveMutation.isPending || deleteMutation.isPending;

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

  /** 一行里的操作区：归档 / 恢复 / 删除（删除可能要二次确认，或事先禁用）。 */
  function renderActions(paper: PaperSummary) {
    if (confirmingId === paper.id) {
      // I4「不可逆操作要明示」：写清**删掉就找不回来了**，并**显示卷名**——
      // 「确定吗？」这种没有宾语的确认等于没确认。
      return (
        <span className="library-confirm" data-testid="library-delete-confirm">
          <span className="library-confirm-text">
            删掉《{paper.title}》就找不回来了。
          </span>
          <Button
            size="sm"
            variant="danger"
            data-testid="library-delete-confirm-yes"
            disabled={deleteMutation.isPending}
            onClick={() => deleteMutation.mutate(paper)}
          >
            {deleteMutation.isPending ? '删除中…' : '确认删除'}
          </Button>
          <Button
            size="sm"
            data-testid="library-delete-confirm-cancel"
            disabled={deleteMutation.isPending}
            onClick={() => setConfirmingId(null)}
          >
            取消
          </Button>
        </span>
      );
    }

    const archived = paper.archived_at !== null;
    return (
      <span className="library-actions">
        <Link className="btn btn-mini" to={`/teacher/paper/${paper.id}`}>
          打开
        </Link>
        {archived ? (
          <Button
            size="sm"
            data-testid="library-unarchive"
            disabled={busy}
            onClick={() => unarchiveMutation.mutate(paper)}
          >
            恢复
          </Button>
        ) : (
          <Button
            size="sm"
            data-testid="library-archive"
            disabled={busy}
            onClick={() => archiveMutation.mutate(paper)}
          >
            归档
          </Button>
        )}
        {/* 有成绩 → 事先禁用，并把理由**写在明面上**（不是只挂在 tooltip 上：
            触屏与键盘用户看不到 tooltip，而这条理由是老师做决定唯一需要的依据）。 */}
        <span
          className="library-delete-guard"
          data-delete-guard={paper.has_attempts ? 'blocked' : 'allowed'}
          title={paper.has_attempts ? DELETE_BLOCKED_HINT : undefined}
        >
          <Button
            size="sm"
            variant="danger"
            data-testid="library-delete"
            disabled={paper.has_attempts || busy}
            onClick={() => setConfirmingId(paper.id)}
          >
            删除
          </Button>
        </span>
        {paper.has_attempts ? (
          <span className="library-row-hint" data-testid="library-delete-blocked-reason">
            {DELETE_BLOCKED_SHORT}
          </span>
        ) : null}
      </span>
    );
  }

  return (
    <div className="library-page">
      <header className="page-intro">
        <h1>卷库</h1>
        <p>
          生成过的卷都在这里，可以再次打开、重新打印，也可以直接用卷号打开一套历史卷。
          用不上的卷可以归档收起来，需要时再恢复。
        </p>
        {/* M6：从卷库这一侧再说一遍同一件事（出题页也写了）。
            真实用户是走完出题流程后不知道东西存到哪了，两个入口都要能自答。 */}
        <p>
          出题页点「生成」的时候，卷子就已经存到这里了，没有单独的保存步骤；之后改题、调排版也是立刻生效。
        </p>
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

        <div className="library-archive-filter">
          <span className="library-filter-label">查看</span>
          {/* 复用组件层的分段控件（`ui/**`）：选中 / 未选 / disabled 三态与焦点环
              都由它保证，页面不再自己写一套。切档回第一页见上面的 effect。 */}
          <SegmentedControl<ArchiveTab>
            name="library-archive"
            ariaLabel="在用或已归档"
            testId="library-archive-filter"
            value={archiveTab}
            onChange={setArchiveTab}
            options={[
              { value: 'exclude', label: '在用' },
              { value: 'only', label: '已归档' },
            ]}
          />
        </div>

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
          {archiveTab === 'only' ? '（已归档）' : ''}
          {q.trim() !== '' ? `（搜索「${q.trim()}」）` : ''}
          {statusFilter !== 'all' ? `（${statusFilter === 'draft' ? '草稿' : '已发布'}）` : ''}
          {paginationMode === 'legacy' ? (
            <span className="library-legacy-note" data-testid="library-pagination-mode">
              （当前服务版本较旧，页码已自动换算）
            </span>
          ) : null}
          {probeFailed !== null ? (
            <span className="library-probe-note" data-testid="library-pagination-probe">
              （没能确认服务版本：{probeFailed}。本次按新版参数读取，列表本身是好的；刷新一次会重新确认。）
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
                  // 归档与 status **正交**，所以两者一起显示：归档不会把「已发布」变成别的状态。
                  paper.status === 'draft' ? '草稿' : '已发布',
                  paper.code ?? '—',
                  String(paper.item_count),
                  formatTime(paper.created_at),
                  renderActions(paper),
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
          q.trim() !== '' || statusFilter !== 'all' || archiveTab === 'only' ? (
            <EmptyState
              testId="library-empty"
              title={archiveTab === 'only' ? '还没有归档的卷子' : '没有符合条件的卷子'}
              hint={
                archiveTab === 'only'
                  ? '在「在用」里点某一行的「归档」，它就会收进这里。'
                  : `当前筛选：${q.trim() !== '' ? `搜索「${q.trim()}」` : ''}${statusFilter !== 'all' ? ` 状态=${statusFilter === 'draft' ? '草稿' : '已发布'}` : ''}`
              }
              action={
                <Button
                  onClick={() => {
                    setSearchInput('');
                    setStatusFilter('all');
                    setArchiveTab('exclude');
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

      {toast ? (
        <ToastStack>
          <Toast
            tone={toast.tone}
            testId="library-toast"
            onDismiss={() => setToast(null)}
          >
            {toast.message}
            {toast.undo ? (
              <>
                {' '}
                <Button
                  size="sm"
                  data-testid="library-toast-undo"
                  disabled={busy}
                  onClick={() => {
                    toast.undo?.();
                    setToast(null);
                  }}
                >
                  撤销
                </Button>
              </>
            ) : null}
          </Toast>
        </ToastStack>
      ) : null}
    </div>
  );
}
