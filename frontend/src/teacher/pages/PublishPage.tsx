/**
 * `/teacher/paper/:id/publish` —— 发卷：卷号 + 二维码同屏，投屏给学生扫。
 *
 * 两条设计约束（都踩过坑）：
 *
 * 1. **已发布的卷也必须能拿到二维码。** 发布结果（`PublishResult`）是会话态，
 *    刷新页面就没了；旧实现在 `publishMutation.data` 不在时整块不渲染，于是
 *    「已经发布过的卷」再进这一页只剩一行卷名 + 一个写着「发布并生成二维码」
 *    的按钮——而它其实早已发布。
 *    现在一律按 `paper.code` 用 `GET /api/papers/{id}/qr` 现取二维码：
 *    该接口每次请求都按**当前访问地址**重新编码，所以从局域网地址打开这一页时，
 *    二维码里就是局域网的地址（老师用本机地址打开时反过来——这一点必须讲清楚，
 *    否则学生扫出来连不上）。
 *
 * 2. 网络里有 `127.0.0.1` / `localhost` 时**不显示学生入口地址**：
 *    那个地址只对老师自己这台机器有效。
 */

import { useMutation, useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';

import { route } from '../../shared/api';
import { ApiError, teacherApi } from '../../shared/apiClient';
import { readError } from '../../shared/errorText';
import { queryKeys } from '../../shared/queryKeys';
import { ErrorState } from '../../ui';

/** 老师是不是用「本机地址」打开的这一页；是的话二维码手机扫不到。 */
function openedAtLocalAddress(): boolean {
  if (typeof window === 'undefined') {
    return false;
  }
  const host = window.location.hostname;
  return host === 'localhost' || host === '127.0.0.1' || host === '::1' || host === '[::1]';
}

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
  const published = paper?.status === 'published' && Boolean(paper.code);
  // 刚发布出来的结果优先（里面有学生入口地址），否则用卷详情里已有的卷号。
  const code = publishMutation.data?.code ?? (published ? paper?.code ?? null : null);
  const studentUrl = openedAtLocalAddress() ? null : publishMutation.data?.student_url ?? null;

  return (
    <div className="publish-page">
      <header className="page-intro">
        <h1>发卷</h1>
        <p>卷号和二维码在同一屏，直接投屏给学生扫；也可以让学生输入卷号。</p>
      </header>

      {paperQuery.isLoading ? <div className="loading-card">正在读取卷 #{paperId} …</div> : null}

      {paperQuery.isError ? (
        // 统一走组件层 ErrorState：说人话 + 给下一步 + 单列错误码。
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
          <section className="publish-main">
            <p className="publish-meta">
              卷名 <strong>{paper.title}</strong> · 题目 {paper.items.length} 道 ·{' '}
              {published ? '已发布' : '草稿，还没发布'}
            </p>

            {published ? (
              <p className="publish-note">
                这套卷已经发布过了。下面的卷号一直有效，二维码随时可以重新取；学生扫码或输入卷号就能开始答题。
              </p>
            ) : (
              <>
                <p className="publish-note">
                  发布只做一件事：把这份草稿固定下来并分配卷号。之后改题请回预览页「另存为可编辑副本」，不会动到学生手上这一份。
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
              </>
            )}

            {publishMutation.isError ? (
              // 用共享的 readError：说人话，错误码只在末尾单列一次。
              <p className="form-error" role="alert" data-testid="publish-failure">
                发卷失败：{readError(publishMutation.error)}
              </p>
            ) : null}

            {code ? (
              <div className="publish-entry" data-testid="publish-result">
                <p className="publish-entry-hint">
                  {studentUrl
                    ? '学生手机连同一个局域网，扫码或打开下面的地址，填一次姓名和学号就能答题。'
                    : '这一页是用本机地址打开的，二维码里也是本机地址，手机扫了连不上。换成局域网地址重开这一页（启动窗口里会给出来），二维码就会跟着换。'}
                </p>
                {studentUrl ? (
                  <p className="publish-url">
                    学生入口：<code>{studentUrl}</code>
                  </p>
                ) : null}
              </div>
            ) : null}

            <p className="page-intro-foot">
              <Link to={`/teacher/paper/${paperId}`}>回到预览改题</Link>
            </p>
          </section>

          <aside className="publish-card">
            <p className="publish-card-title">{code ? '学生扫码答题' : '发布后出现二维码'}</p>
            {code ? (
              <>
                <div className="publish-code">
                  卷号 <strong data-testid="publish-code">{code}</strong>
                </div>
                <img
                  className="publish-qr"
                  data-testid="publish-qr"
                  alt={`卷 ${code} 的二维码`}
                  src={route('qr', { paper_id: paperId })}
                />
              </>
            ) : (
              <div className="publish-qr-pending" aria-hidden="true" />
            )}
          </aside>
        </div>
      ) : null}
    </div>
  );
}
