/**
 * `/teacher/paper/:id/publish` —— 发卷（FR-T10：二维码 + 卷号同屏）。
 *
 * M2 的任务卡只要求配置 / 预览改题 / 排版面板 / 打印，本页是**接口已存在**的最小闭环
 * （`POST /api/papers/{id}/publish` 与 `GET /api/papers/{id}/qr`）——
 * 真正完整的发卷页（投屏样式、学生入口说明）属于后续里程碑。
 */

import { useMutation, useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';

import { route } from '../../shared/api';
import { ApiError, teacherApi } from '../../shared/apiClient';
import { queryKeys } from '../../shared/queryKeys';

export function PublishPage() {
  const params = useParams<{ id: string }>();
  const paperId = Number(params.id);

  const paperQuery = useQuery({
    queryKey: queryKeys.paper(paperId),
    queryFn: () => teacherApi.getPaper(paperId),
    enabled: Number.isInteger(paperId) && paperId > 0,
  });

  const publishMutation = useMutation({
    mutationFn: () => teacherApi.publishPaper(paperId),
  });

  if (!Number.isInteger(paperId) || paperId <= 0) {
    return (
      <div className="error-card">
        <h1>卷号不合法</h1>
        <Link className="btn" to="/teacher">
          回到出题配置
        </Link>
      </div>
    );
  }

  const paper = paperQuery.data;

  return (
    <div className="publish-page">
      <header className="page-intro">
        <h1>发卷</h1>
        <p>生成卷号与二维码后同屏展示，供投屏给学生（FR-T10 / D23）。</p>
      </header>

      {paperQuery.isLoading ? <div className="loading-card">正在读取卷 #{paperId} …</div> : null}

      {paperQuery.isError ? (
        <div className="error-card" data-testid="publish-error">
          <h1>读不到卷 #{paperId}</h1>
          <p>
            {paperQuery.error instanceof ApiError
              ? `${paperQuery.error.code}：${paperQuery.error.message}`
              : String(paperQuery.error)}
          </p>
          <Link className="btn" to="/teacher">
            回到出题配置
          </Link>
        </div>
      ) : null}

      {paper ? (
        <div className="publish-body">
          <p>
            卷名：<strong>{paper.title}</strong> · 题目 {paper.items.length} 道 · 当前状态{' '}
            {paper.status === 'draft' ? '草稿' : '已发布'}
            {paper.code ? ` · 卷号 ${paper.code}` : ''}
          </p>

          <button
            type="button"
            className="btn btn-primary"
            data-testid="publish-button"
            disabled={publishMutation.isPending}
            onClick={() => publishMutation.mutate()}
          >
            {publishMutation.isPending ? '正在发布…' : '发布并生成二维码'}
          </button>

          {publishMutation.isError ? (
            <p className="form-error" role="alert" data-testid="publish-failure">
              {publishMutation.error instanceof ApiError
                ? `${publishMutation.error.code}：${publishMutation.error.message}`
                : String(publishMutation.error)}
            </p>
          ) : null}

          {publishMutation.data ? (
            <div className="publish-result" data-testid="publish-result">
              <div className="publish-code">
                卷号 <strong data-testid="publish-code">{publishMutation.data.code}</strong>
              </div>
              <img
                className="publish-qr"
                data-testid="publish-qr"
                alt={`卷 ${publishMutation.data.code} 的二维码`}
                src={route('qr', { paper_id: publishMutation.data.id })}
              />
              <p className="publish-url">
                学生入口：<code>{publishMutation.data.student_url}</code>
              </p>
            </div>
          ) : null}

          <p className="page-intro-foot">
            <Link to={`/teacher/paper/${paperId}`}>回到预览改题</Link>
          </p>
        </div>
      ) : null}
    </div>
  );
}
