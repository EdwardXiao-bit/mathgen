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
import { readError } from '../../shared/errorText';
import { queryKeys } from '../../shared/queryKeys';
import { ErrorState } from '../../ui';

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
      <div className="publish-page">
        <div className="page-intro">
          <h1>卷号不合法</h1>
          <p>请从卷库列表里选一套卷子再发卷。</p>
        </div>
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
        <p>生成卷号和二维码后一起显示，可以直接投屏给学生。</p>
      </header>

      {paperQuery.isLoading ? <div className="loading-card">正在读取卷 #{paperId} …</div> : null}

      {paperQuery.isError ? (
        // 统一走组件层 ErrorState：说人话 + 给下一步 + 单列错误码。
        // 之前是 `.error-card` 里拼一行 `CODE：message`，既是另一套卡片样式，
        // 也把服务端原文直接怼给老师（用户反馈「面向开发者的文本」）。
        <ErrorState
          testId="publish-error"
          title={`读不到卷 #${paperId}`}
          what={readError(paperQuery.error)}
          how="先确认题目服务正在运行（窗口不要关），然后重试；仍然不行请把这个卷号告诉维护人员。"
          code={paperQuery.error instanceof ApiError ? paperQuery.error.code : null}
          actions={
            <Link className="btn btn-primary" to="/teacher">
              回到出题配置
            </Link>
          }
        />
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
            // 用共享的 readError：说人话，错误码只在末尾单列一次。
            <p className="form-error" role="alert" data-testid="publish-failure">
              发卷失败：{readError(publishMutation.error)}
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
