/** `/teacher/library` —— 卷库（FR-T14）：生成过的卷都在这里，可再次打开、重打印。 */

import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { ApiError, teacherApi } from '../../shared/apiClient';
import { queryKeys } from '../../shared/queryKeys';

function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString('zh-CN', { hour12: false });
}

export function LibraryPage() {
  const query = useQuery({ queryKey: queryKeys.papers, queryFn: () => teacherApi.listPapers() });

  if (query.isLoading) {
    return <div className="loading-card">正在读取卷库…</div>;
  }

  if (query.isError) {
    const error = query.error;
    return (
      <div className="error-card" data-testid="library-error">
        <h1>读不到卷库</h1>
        <p>{error instanceof ApiError ? `${error.code}：${error.message}` : String(error)}</p>
        <button type="button" className="btn btn-primary" onClick={() => void query.refetch()}>
          重试
        </button>
      </div>
    );
  }

  const data = query.data;
  return (
    <div className="library-page">
      <header className="page-intro">
        <h1>卷库</h1>
        <p>共 {data?.total ?? 0} 套卷。点击卷名回到预览改题页（FR-T14 / FR-T15）。</p>
      </header>

      {!data || data.papers.length === 0 ? (
        <p className="empty-state">
          还没有任何卷子。先去 <Link to="/teacher">出题配置</Link> 生成一套。
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
          <tbody>
            {data.papers.map((paper) => (
              <tr key={paper.id} data-paper-id={paper.id}>
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
    </div>
  );
}
