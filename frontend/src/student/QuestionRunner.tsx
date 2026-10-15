/**
 * 答题主体：一屏一题、数字键盘、计时、进度、可回上一题改答案。
 *
 * 几个刻意的取舍：
 * 1. **不画卷面**（D56 / C3）：题面直接用后端 `display` 字段的文本，字号只做放大，
 *    不在这里拼算式排版——卷面排版只有 `backend/app/render/` 一个来源。
 * 2. **不自己做数字键盘**：手机原生键盘（`inputMode="numeric"`）更快、更省电，
 *    而且能用学生自己的输入法习惯；自绘键盘在 390px 宽的屏幕上会挤掉题面。
 * 3. **未作答要拦一下**：直接交卷会让学生莫名其妙丢分，所以最后一步先告知还有几题没答，
 *    再由学生确认——但**不阻止**他交卷（有的题确实是不会）。
 */
import { useEffect, useRef, useState } from 'react';

import {
  describeError,
  type AnswerEntry,
  type AttemptResult,
  type FriendlyError,
  type StudentQuestion,
} from './api';
import { Button, Card, Note, Screen, TopBar } from './chrome';

export interface RunnerProps {
  title: string;
  questions: StudentQuestion[];
  onExit: () => void;
  /** 交卷：由页面决定走「老师发的卷」还是「自主练习」两种提交模式 */
  submit: (answers: AnswerEntry[], durationMs: number) => Promise<AttemptResult>;
  onFinish: (result: AttemptResult, answers: AnswerEntry[]) => void;
}

function formatClock(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

export function QuestionRunner({ title, questions, onExit, submit, onFinish }: RunnerProps) {
  const [index, setIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [problem, setProblem] = useState<FriendlyError | null>(null);
  const [elapsed, setElapsed] = useState(0);

  const startedAt = useRef(Date.now());
  const answerRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setElapsed(Math.floor((Date.now() - startedAt.current) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  // 换一题就聚焦答案框：键盘留在屏幕上，学生不用每题都点一下输入框
  useEffect(() => {
    answerRef.current?.focus();
  }, [index]);

  const total = questions.length;
  const current = questions[index];
  if (!current) return null;

  const answeredCount = questions.filter((question) => (answers[question.seq] ?? '') !== '').length;
  const unanswered = total - answeredCount;
  const percent = total === 0 ? 0 : Math.round(((index + 1) / total) * 100);

  const entries: AnswerEntry[] = questions.map((question) => {
    const value = answers[question.seq] ?? '';
    return { seq: question.seq, answer: value === '' ? null : value };
  });

  function typeAnswer(raw: string) {
    // 只留数字：手机键盘上仍可能粘进来空格或符号，直接挡住比事后校验更省事
    const digits = raw.replace(/[^0-9]/g, '').slice(0, 8);
    setAnswers((prev) => ({ ...prev, [current.seq]: digits }));
  }

  async function doSubmit() {
    setSubmitting(true);
    setProblem(null);
    try {
      const result = await submit(entries, Date.now() - startedAt.current);
      onFinish(result, entries);
    } catch (error) {
      setProblem(describeError(error));
      setSubmitting(false);
      setConfirming(false);
    }
  }

  function next() {
    if (index < total - 1) {
      setIndex(index + 1);
      return;
    }
    if (unanswered > 0) {
      setConfirming(true);
      return;
    }
    void doSubmit();
  }

  return (
    <Screen>
      <TopBar title={title} right={<span className="st-clock">{formatClock(elapsed)}</span>} />

      <div className="st-runner">
        <div className="st-progress">
          <div className="st-progress-track">
            <div className="st-progress-fill" style={{ width: `${percent}%` }} />
          </div>
          <p className="st-progress-text">
            第 {index + 1} 题，共 {total} 题
          </p>
        </div>

        <Card>
          <p className="st-question">{current.display}</p>
          <label className="st-field" htmlFor="st-answer">
            <span className="st-field-label">你的答案</span>
            <input
              id="st-answer"
              ref={answerRef}
              className="st-input st-input--answer"
              type="text"
              inputMode="numeric"
              pattern="[0-9]*"
              autoComplete="off"
              enterKeyHint={index < total - 1 ? 'next' : 'done'}
              value={answers[current.seq] ?? ''}
              onChange={(event) => typeAnswer(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault();
                  next();
                }
              }}
            />
          </label>
        </Card>

        {problem ? <Note tone="error" title={problem.title} hint={problem.hint} /> : null}

        {confirming ? (
          <Card>
            <p className="st-confirm-title">还有 {unanswered} 题没有作答</p>
            <p className="st-confirm-hint">这些题会按答错算分。确定现在交卷吗？</p>
            <div className="st-row">
              <Button variant="ghost" onClick={() => setConfirming(false)}>
                回去补一补
              </Button>
              <Button onClick={() => void doSubmit()} disabled={submitting}>
                {submitting ? '正在判分…' : '确定交卷'}
              </Button>
            </div>
          </Card>
        ) : null}
      </div>

      <footer className="st-footer">
        <div className="st-row">
          <Button variant="ghost" onClick={() => setIndex(index - 1)} disabled={index === 0}>
            上一题
          </Button>
          <Button onClick={next} disabled={submitting}>
            {index === total - 1 ? (submitting ? '正在判分…' : '交卷') : '下一题'}
          </Button>
        </div>
        <button type="button" className="st-quiet" onClick={onExit}>
          退出
        </button>
      </footer>
    </Screen>
  );
}
