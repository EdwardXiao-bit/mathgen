/**
 * 结果页（`/student/result`）：当场看分 + 逐题对照。
 *
 * 数据来自交卷那一刻存下的结果（`resultStore`），不是重新请求后端：
 * 契约里没有「学生按 attempt_id 取自己结果」的接口。
 *
 * 得分只给整数分（答对题数 / 总题数），不显示小数点：小学生看得懂的才是有效的反馈。
 */
import { useMemo } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import { Button, Card, Note, Screen, TopBar } from '../chrome';
import { asStoredResult, readResult, type StoredResult } from '../resultStore';

/** 未作答在界面上的说法，不显示空字符串或 null。 */
const NO_ANSWER = '没有作答';

export function ResultPage() {
  const navigate = useNavigate();
  const location = useLocation();

  // 先从会话里读；读不到就用交卷时随路由带过来的那份（存储被禁用时的兜底）
  const stored: StoredResult | null = useMemo(
    () => readResult() ?? asStoredResult(location.state),
    [location.state],
  );

  const questionText = useMemo(() => {
    const map = new Map<number, string>();
    for (const question of stored?.questions ?? []) map.set(question.seq, question.display);
    return map;
  }, [stored]);

  const myAnswer = useMemo(() => {
    const map = new Map<number, string | null>();
    for (const entry of stored?.answers ?? []) map.set(entry.seq, entry.answer);
    return map;
  }, [stored]);

  if (stored === null) {
    return (
      <Screen>
        <TopBar title="练习结果" />
        <div className="st-body">
          <Note title="这里暂时没有结果" hint="重新做一份练习，交卷后就能看到分数。" />
          <Button onClick={() => navigate('/')}>回首页</Button>
        </div>
      </Screen>
    );
  }

  const { result, title, source, paperCode } = stored;
  const total = result.total > 0 ? result.total : result.items.length;
  const correct = Math.max(0, Math.min(result.correct, total));
  // D80：得分**直接用后端给的 `score`**，不再用 `correct / total` 复算。
  // 两套口径在 `(40, 23)`、`(80, 46)` 这类点上真的会分叉（旧口径 57、契约 58），
  // 而分叉不会自己报错 —— 只会让老师那边和这边显示同一个成绩两个分数。
  const score = result.score;

  // 「没作答」与「答错」是两回事：0 分里既可能是全错，也可能是一题没写就交了卷
  // （T-M8-02 那条 0 分卷就是后者）。判据只依赖契约已有的 `student_answer`
  // （`null` 或空串即没作答），不新增字段、不动冻结的 DTO。
  const mineOf = (item: (typeof result.items)[number]): string | null =>
    item.student_answer ?? myAnswer.get(item.seq) ?? null;
  const unanswered = result.items.filter((item) => {
    const mine = mineOf(item);
    return mine === null || mine === '';
  }).length;

  return (
    <Screen>
      <TopBar title="练习结果" />
      <div className="st-body">
        <Card>
          <p className="st-result-title">{title}</p>
          <p className="st-score">
            {score}
            <span className="st-score-unit">分</span>
          </p>
          <p className="st-score-sub">
            答对 {correct} 题，共 {total} 题
            {unanswered > 0 ? `，未作答 ${unanswered} 题` : ''}
          </p>
        </Card>

        <ul className="st-result-list">
          {result.items.map((item) => {
            const mine = mineOf(item);
            const text = item.display ?? questionText.get(item.seq) ?? '这道题';
            const blank = mine === null || mine === '';
            const tone = blank ? 'muted' : item.is_correct ? 'ok' : 'bad';
            return (
              <li key={item.seq} className={`st-result-row st-result-row--${tone}`}>
                <div className="st-result-head">
                  <span className="st-result-question">{text}</span>
                  <span className={`st-badge st-badge--${tone}`}>
                    {blank ? '未作答' : item.is_correct ? '答对' : '答错'}
                  </span>
                </div>
                <div className="st-result-answers">
                  <span className="st-result-mine">
                    你的答案：{mine === null || mine === '' ? NO_ANSWER : mine}
                  </span>
                  {item.is_correct || item.answer === null ? null : (
                    <span className="st-result-right">正确答案：{item.answer}</span>
                  )}
                </div>
              </li>
            );
          })}
        </ul>

        <Button
          onClick={() =>
            navigate(source === 'paper' && paperCode !== null ? `/paper/${encodeURIComponent(paperCode)}` : '/practice')
          }
        >
          再做一遍
        </Button>
        <Button variant="ghost" onClick={() => navigate('/')}>
          回首页
        </Button>
      </div>
    </Screen>
  );
}
