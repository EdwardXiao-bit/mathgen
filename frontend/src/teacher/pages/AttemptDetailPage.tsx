/**
 * 逐题对错明细（FR-T12）：`/teacher/attempts/:attemptId`。
 *
 * 回答三个问题：**谁**做的、**对了几题**、**哪几题错了而且他答的是什么**。
 *
 * 三条刻意的设计决定：
 * 1. **不把「正确答案」藏起来，也不把「学生答案」补成空串**：学生没作答时后端给
 *    `null`，这里显示「未作答」——`null` 与「答了但写的是空」是两件事，
 *    渲染成 `undefined` 更是 A10 明令禁止的。
 * 2. **得分直接渲染后端给的 `score`**（D80），页面里没有任何 `correct / total` 计算。
 *    这样「列表说 58、明细说 57」这种不可能自己报错的错就结构性地不存在。
 * 3. 明细的题目来自**作答时保存的题目快照**（`attempt_item` 冗余存题），所以卷子后来
 *    改了或删了，这份历史成绩依然能还原成当时的样子（docs/02 §5.2 的关键取舍）。
 */

import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';

import './AttemptsPage.css';
import type { AttemptItemOut } from '../../shared/api';
import { ApiError, teacherApi } from '../../shared/apiClient';
import { readError } from '../../shared/errorText';
import { queryKeys } from '../../shared/queryKeys';
import { Button, EmptyState, ErrorState, Table, type TableColumn } from '../../ui';

const PRACTICE_LABEL = '自主练习';

function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString('zh-CN', { hour12: false });
}

function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return '0秒';
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return `${seconds}秒`;
  return `${Math.floor(seconds / 60)}分${seconds % 60}秒`;
}

export function AttemptDetailPage() {
  const params = useParams();
  const rawId = params.attemptId;
  const attemptId = Number(rawId);

  const query = useQuery({
    queryKey: queryKeys.attempt(Number.isInteger(attemptId) ? attemptId : 0),
    queryFn: () => teacherApi.getAttempt(attemptId),
    enabled: Number.isInteger(attemptId) && attemptId > 0,
  });

  const detail = query.data;
  const wrongCount = detail === undefined ? 0 : detail.total - detail.correct;

  return (
    <div className="attempts-page">
      <header className="page-intro">
        <h1>这份成绩的逐题明细</h1>
        <p>
          每题都对了一遍：学生对的是什么、错在哪、正确答案是什么。
          错题会排在明面上，不用一行行找。
        </p>
      </header>

      {query.isError ? (
        <ErrorState
          testId="attempt-detail-error"
          title="读不到这份明细"
          what={readError(query.error)}
          how="先确认题目服务正在运行（窗口不要关），然后点「重试」。"
          code={query.error instanceof ApiError ? query.error.code : null}
          actions={
            <>
              <Button variant="primary" onClick={() => void query.refetch()}>
                重试
              </Button>
              <Link className="btn" to="/teacher/attempts">
                回成绩列表
              </Link>
            </>
          }
        />
      ) : null}

      {query.isLoading ? (
        <p className="attempts-loading" role="status">
          正在读取明细…
        </p>
      ) : null}

      {detail !== undefined ? (
        <>
          <section className="attempt-detail-head" data-testid="attempt-detail-summary">
            <div className="attempt-detail-score-box">
              <span className="attempt-detail-score-label">得分</span>
              {/* D80：这个数直接来自后端，页面里没有第二处算法。 */}
              <b className="attempt-detail-score" data-testid="attempt-detail-score">
                {detail.score}
              </b>
            </div>
            <dl className="attempt-detail-pairs">
              <div>
                <dt>学生</dt>
                <dd data-testid="attempt-detail-student">
                  {detail.student_name}（学号 {detail.student_no}）
                </dd>
              </div>
              <div>
                <dt>卷子</dt>
                {/* 自主练习三者都是 null：显示界面层补的文案，不显示「—」也不显示空。 */}
                <dd data-testid="attempt-detail-paper">
                  {detail.paper_title === null ? PRACTICE_LABEL : detail.paper_title}
                </dd>
              </div>
              <div>
                <dt>答对</dt>
                <dd data-testid="attempt-detail-correct">
                  {detail.correct} / {detail.total}
                </dd>
              </div>
              <div>
                <dt>错题</dt>
                <dd data-testid="attempt-detail-wrong-count">{wrongCount}</dd>
              </div>
              <div>
                <dt>用时</dt>
                <dd data-testid="attempt-detail-duration">{formatDuration(detail.duration_ms)}</dd>
              </div>
              <div>
                <dt>提交时间</dt>
                <dd data-testid="attempt-detail-submitted">{formatTime(detail.submitted_at)}</dd>
              </div>
            </dl>
          </section>

          <Table
            testId="attempt-detail-table"
            ariaLabel="逐题对错"
            bodyTestId="attempt-detail-rows"
            columns={
              [
                { key: 'seq', header: '题号', numeric: true },
                { key: 'display', header: '题目' },
                { key: 'student_answer', header: '学生答案' },
                { key: 'answer', header: '正确答案' },
                { key: 'verdict', header: '对错' },
              ] satisfies TableColumn[]
            }
            rows={detail.items.map((item: AttemptItemOut) => ({
              key: item.seq,
              testAttrs: {
                'data-seq': String(item.seq),
                'data-correct': item.is_correct ? 'true' : 'false',
              },
              cells: [
                String(item.seq),
                <span className="attempt-detail-display">{item.display}</span>,
                // `null` = 没作答。写成「未作答」而不是空白：空白看上去像界面坏了。
                <span
                  className={item.is_correct ? undefined : 'attempt-detail-wrong'}
                  data-testid="attempt-detail-row-student-answer"
                >
                  {item.student_answer === null ? '未作答' : item.student_answer}
                </span>,
                <span data-testid="attempt-detail-row-answer">{item.answer}</span>,
                <span
                  className={item.is_correct ? 'attempt-detail-ok' : 'attempt-detail-wrong'}
                  data-testid="attempt-detail-row-verdict"
                >
                  {item.is_correct ? '对' : '错'}
                </span>,
              ],
            }))}
            emptyAction={
              <EmptyState
                testId="attempt-detail-empty"
                title="这次作答没有留下逐题记录"
                hint="可能是很久以前的记录。成绩的分数仍然有效。"
                action={
                  <Link className="btn btn-primary" to="/teacher/attempts">
                    回成绩列表
                  </Link>
                }
              />
            }
          />

          <div className="attempt-detail-foot">
            <Link className="btn" data-testid="attempt-detail-back" to="/teacher/attempts">
              回成绩列表
            </Link>
            {detail.paper_id === null ? null : (
              <Link className="btn" to={`/teacher/paper/${detail.paper_id}/attempts`}>
                看这套卷的全部成绩
              </Link>
            )}
          </div>
        </>
      ) : null}
    </div>
  );
}
