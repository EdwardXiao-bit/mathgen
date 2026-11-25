/**
 * 模式 C 校对页（M18-05）：识别结果逐题核对，确认完入档出分。
 *
 * 三条不可让步的规则（`docs/10` §3 / §10、`docs/00` D91）：
 *   ① **识别不出不静默判 0**：没识别到的题保持「待确认」，必须老师填答案或显式标「看不清」；
 *   ② **两遍一致才自动通过**：`agreed` 的题预填并已确认，但老师仍可改；
 *   ③ 批次没入档前可删；入档后只能去看成绩。
 *
 * 键盘（Q15）：输入框里 `Enter` 存并下一题、`Shift+Enter` 上一题；`Ctrl+Enter` 入档。
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate, useParams } from 'react-router-dom';

import { readError } from '../../shared/errorText';
import { queryKeys } from '../../shared/queryKeys';
import type { OcrBatchDetail, OcrItemOut, PaperDetail } from '../../shared/api';
import {
  ApiError,
  commitOcrBatch,
  deleteOcrBatch,
  getOcrBatch,
  teacherApi,
  updateOcrItem,
} from '../../shared/apiClient';
import { ErrorState } from '../../ui';

import './OcrReviewPage.css';

const POLLING_STATUSES = new Set(['pending', 'processing']);

function statusLabel(status: string): string {
  switch (status) {
    case 'pending':
      return '排队中';
    case 'processing':
      return '识别中';
    case 'reviewing':
      return '待核对';
    case 'committed':
      return '已计分';
    case 'failed':
      return '识别失败';
    default:
      return status;
  }
}

function percent(value: number | null): string {
  if (value === null) return '—';
  return `${Math.round(value * 100)}%`;
}

/** 只在页面上做「像不像」的预览；真正判分仍由后端在入档时算（不在前端算分）。 */
function sameNumber(left: string, right: string): boolean | null {
  const a = Number(left.replace(/[×xX]/g, '*').replace(/÷/g, '/').trim());
  const b = Number(right.replace(/[×xX]/g, '*').replace(/÷/g, '/').trim());
  if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
  return Math.abs(a - b) < 1e-9;
}

function judgeLabel(answer: string, correct: string): { text: string; state: string } {
  if (answer.trim() === '') return { text: '待填答案', state: 'empty' };
  const numeric = sameNumber(answer, correct);
  const equal = numeric ?? answer.replace(/\s+/g, '') === correct.replace(/\s+/g, '');
  return equal ? { text: '判对了', state: 'ok' } : { text: '判错了', state: 'bad' };
}

export function OcrReviewPage() {
  const { batchId = '' } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const inputsRef = useRef<Record<number, HTMLInputElement | null>>({});

  const batchQuery = useQuery({
    queryKey: queryKeys.ocrBatch(batchId),
    queryFn: () => getOcrBatch(batchId),
    // 识别期间轮询：批次状态在后端推进，页面不主动问就永远停在「已接收」。
    refetchInterval: (query) => {
      const data = query.state.data as OcrBatchDetail | undefined;
      return data && POLLING_STATUSES.has(data.batch.status) ? 1500 : false;
    },
  });

  const detail = batchQuery.data ?? null;

  // 判分预览：老师端详情里带正确答案（契约 §9 只对学生端剔除）。
  // 这里只做「像对不对」的提示，真正判分发生在入档时由后端完成。
  const paperId = detail?.batch.paper_id ?? 0;
  const paperQuery = useQuery({
    queryKey: queryKeys.paper(paperId),
    queryFn: () => teacherApi.getPaper(paperId),
    enabled: paperId > 0,
  });

  const correctAnswers = useMemo(() => {
    const map: Record<number, string> = {};
    const paper = paperQuery.data as PaperDetail | undefined;
    for (const item of paper?.items ?? []) map[item.seq] = item.answer;
    return map;
  }, [paperQuery.data]);

  /** 每题的输入框内容：首次拿到数据时用自动结果填，之后由用户编辑。 */
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [focusedSeq, setFocusedSeq] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busySeq, setBusySeq] = useState<number | null>(null);

  const signature = useMemo(() => {
    if (!detail) return '';
    return detail.items.map((item) => `${item.seq}:${item.final_answer ?? ''}:${item.reviewed}`).join('|');
  }, [detail]);

  useEffect(() => {
    if (!detail) return;
    const seeded: Record<number, string> = {};
    for (const item of detail.items) seeded[item.seq] = item.final_answer ?? '';
    setAnswers(seeded);
  }, [detail?.batch.id, signature]); // eslint-disable-line react-hooks/exhaustive-deps

  function applyDetail(next: OcrBatchDetail) {
    queryClient.setQueryData(queryKeys.ocrBatch(batchId), next);
  }

  const saveMutation = useMutation({
    mutationFn: (input: { seq: number; answer?: string; reviewed?: boolean; unreadable?: boolean }) =>
      updateOcrItem(batchId, input.seq, {
        answer: input.answer,
        reviewed: input.reviewed,
        unreadable: input.unreadable,
      }),
    onSuccess: (next) => applyDetail(next),
    onError: (error) => setActionError(readError(error)),
    onSettled: () => setBusySeq(null),
  });

  const commitMutation = useMutation({
    mutationFn: () => commitOcrBatch(batchId),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ['ocr'] });
      navigate(`/teacher/attempts/${result.attempt_id}`);
    },
    onError: (error) => setActionError(readError(error)),
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteOcrBatch(batchId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['ocr'] });
      navigate('/teacher/ocr');
    },
    onError: (error) => setActionError(readError(error)),
  });

  function focusSeq(seq: number) {
    const node = inputsRef.current[seq];
    if (node) {
      node.focus();
      node.select();
      setFocusedSeq(seq);
    }
  }

  function saveItem(item: OcrItemOut, nextSeq: number | null) {
    const answer = (answers[item.seq] ?? '').trim();
    setActionError(null);
    setBusySeq(item.seq);
    saveMutation.mutate(
      { seq: item.seq, answer, reviewed: true },
      {
        onSuccess: (next) => {
          applyDetail(next);
          if (nextSeq !== null) focusSeq(nextSeq);
        },
      },
    );
  }

  function markUnreadable(item: OcrItemOut) {
    setActionError(null);
    setBusySeq(item.seq);
    saveMutation.mutate({ seq: item.seq, unreadable: true });
  }

  const items = detail?.items ?? [];
  const reviewedCount = items.filter((item) => item.reviewed).length;
  const allReviewed = items.length > 0 && reviewedCount === items.length;
  const editable = detail !== null && detail.batch.status === 'reviewing';
  const committed = detail?.batch.status === 'committed';

  // Ctrl+Enter 入档：焦点在哪都能用（Q15），但不劫持输入框里的普通按键。
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (!(event.ctrlKey || event.metaKey) || event.key !== 'Enter') return;
      if (!editable || !allReviewed || commitMutation.isPending) return;
      event.preventDefault();
      commitMutation.mutate();
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [allReviewed, commitMutation, editable]);

  if (batchQuery.isLoading) {
    return (
      <div className="ocr-review-page">
        <div className="loading-card" data-testid="ocr-review-loading">
          正在读取这一批…
        </div>
      </div>
    );
  }

  if (batchQuery.isError) {
    const error = batchQuery.error;
    const notFound = error instanceof ApiError && error.code === 'OCR_BATCH_NOT_FOUND';
    return (
      <div className="ocr-review-page">
        <ErrorState
          testId="ocr-review-load-error"
          title={notFound ? '这一批不存在' : '读取不到这一批'}
          what={readError(error)}
          how={
            notFound
              ? '可能已经删掉了。回拍照批改重新上传一份即可。'
              : '确认后端已启动，再刷新这一页。'
          }
          code={error instanceof ApiError ? error.code : null}
          actions={
            <Link className="btn" to="/teacher/ocr">
              回拍照批改
            </Link>
          }
        />
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="ocr-review-page">
        <div className="loading-card">正在读取这一批…</div>
      </div>
    );
  }

  return (
    <div className="ocr-review-page">
      <section className="page-intro">
        <h1>核对识别结果</h1>
        <p>
          {detail.batch.student_name ?? '这位学生'} · {detail.batch.page_count} 页 ·{' '}
          {statusLabel(detail.batch.status)}
          {editable ? `：${reviewedCount} / ${items.length} 题已确认` : ''}
        </p>
      </section>

      {POLLING_STATUSES.has(detail.batch.status) ? (
        <div className="ocr-status" data-testid="ocr-review-progress" role="status">
          正在识别，页面会自动刷新。
        </div>
      ) : null}

      {detail.batch.status === 'failed' ? (
        <div className="ocr-status ocr-status-warn" data-testid="ocr-review-failed" role="alert">
          <p>{detail.batch.message ?? '这一批识别失败了。'}</p>
          <p>
            <Link className="btn" to="/teacher/ocr">
              重新上传一份
            </Link>
          </p>
        </div>
      ) : null}

      {committed ? (
        <div className="ocr-status" data-testid="ocr-review-committed" role="status">
          本批已计分。
          {detail.batch.committed_attempt_id ? (
            <>
              {' '}
              <Link to={`/teacher/attempts/${detail.batch.committed_attempt_id}`}>查看成绩</Link>
            </>
          ) : null}
        </div>
      ) : null}

      <ol className="ocr-items" data-testid="ocr-item-list">
        {items.map((item) => {
          const pending = !item.reviewed;
          const correct = correctAnswers[item.seq];
          const judge = correct === undefined ? null : judgeLabel(answers[item.seq] ?? '', correct);
          return (
            <li
              className={`ocr-item${pending ? ' ocr-item-pending' : ''}`}
              key={item.seq}
              data-testid={`ocr-item-${item.seq}`}
              data-agreed={item.agreed ? '1' : '0'}
              data-reviewed={item.reviewed ? '1' : '0'}
            >
              <div className="ocr-item-head">
                <span className="ocr-item-seq">第 {item.seq} 题</span>
                <span className="ocr-item-page">第 {item.page_no} 页</span>
                {item.agreed ? (
                  <span className="ocr-chip" data-testid={`ocr-item-agreed-${item.seq}`}>
                    自动一致
                  </span>
                ) : (
                  <span className="ocr-chip ocr-chip-warn" data-testid={`ocr-chip-manual-${item.seq}`}>
                    需核对
                  </span>
                )}
                {item.reviewed ? <span className="ocr-chip ocr-chip-ok">已确认</span> : null}
                {judge ? (
                  <span
                    className={`ocr-chip ocr-chip-judge ocr-chip-judge-${judge.state}`}
                    data-testid={`ocr-judge-${item.seq}`}
                  >
                    {judge.text}
                  </span>
                ) : null}
              </div>

              <div className="ocr-item-body">
                {item.crop_url ? (
                  <img className="ocr-item-crop" src={item.crop_url} alt={`第 ${item.seq} 题答案区`} />
                ) : (
                  <div className="ocr-item-crop ocr-item-crop-empty">没有裁图</div>
                )}

                <div className="ocr-item-reads">
                  <span>
                    第一次：{item.raw_text_p1 ?? '没认出来'}（{percent(item.confidence_p1)}）
                  </span>
                  <span>
                    第二次：{item.raw_text_p2 ?? '没认出来'}（{percent(item.confidence_p2)}）
                  </span>
                </div>

                <label className="ocr-item-answer">
                  <span className="ocr-field-label">学生答案</span>
                  <input
                    ref={(node) => {
                      inputsRef.current[item.seq] = node;
                    }}
                    className="mg-input"
                    type="text"
                    inputMode="text"
                    data-testid={`ocr-answer-${item.seq}`}
                    value={answers[item.seq] ?? ''}
                    disabled={!editable}
                    onFocus={() => setFocusedSeq(item.seq)}
                    onKeyDown={(event) => {
                      if (event.key !== 'Enter') return;
                      event.preventDefault();
                      const index = items.findIndex((row) => row.seq === item.seq);
                      const nextItem = event.shiftKey ? items[index - 1] : items[index + 1];
                      saveItem(item, nextItem ? nextItem.seq : null);
                    }}
                    onChange={(event) =>
                      setAnswers((prev) => ({ ...prev, [item.seq]: event.target.value }))
                    }
                  />
                </label>

                <div className="ocr-item-actions">
                  <button
                    className="btn btn-primary"
                    type="button"
                    data-testid={`ocr-confirm-${item.seq}`}
                    disabled={!editable || busySeq === item.seq}
                    onClick={() => saveItem(item, null)}
                  >
                    确认本题
                  </button>
                  <button
                    className="btn"
                    type="button"
                    data-testid={`ocr-unreadable-${item.seq}`}
                    disabled={!editable || busySeq === item.seq}
                    onClick={() => markUnreadable(item)}
                  >
                    标记为未作答
                  </button>
                </div>
              </div>
            </li>
          );
        })}
      </ol>

      {actionError ? (
        <p className="form-error" role="alert" data-testid="ocr-review-action-error">
          {actionError}
        </p>
      ) : null}

      <div className="ocr-review-actions">
        <span className="ocr-review-progress" data-testid="ocr-review-counts">
          已确认 {reviewedCount} / {items.length} 题
          {focusedSeq !== null ? ` · 正在第 ${focusedSeq} 题` : ''}
        </span>
        <div className="ocr-review-buttons">
          {!committed ? (
            <button
              className="btn"
              type="button"
              data-testid="ocr-delete-batch"
              disabled={deleteMutation.isPending}
              onClick={() => {
                if (window.confirm('删除本批？上传的图片和识别结果会一起删除，不能恢复。')) {
                  deleteMutation.mutate();
                }
              }}
            >
              删除本批
            </button>
          ) : null}
          <button
            className="btn btn-primary"
            type="button"
            data-testid="ocr-commit"
            disabled={!editable || !allReviewed || commitMutation.isPending}
            onClick={() => commitMutation.mutate()}
          >
            {commitMutation.isPending ? '正在计分…' : '确认并计分'}
          </button>
        </div>
      </div>
    </div>
  );
}
