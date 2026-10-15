/**
 * 自主练习（`/student/practice`）：选难度 + 题量 → 出题 → 答题 → 判分。
 *
 * 为什么只给两档难度：学生自己在手机上选，选项必须一眼看懂。
 * 「20 以内」「100 以内」是老师上课就在用的说法；更细的档位组合（进位/退位、乘除、
 * 带括号）留给老师出卷时用，学生端不铺开——决策见交接说明。
 *
 * 关键实现点：出题接口回来的 `seed` 必须留到交卷时原样回传（服务端靠它重跑引擎判分，
 * D75），所以出题结果和 `seed` 一起放进同一个 session 状态里，不拆开存。
 */
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import {
  describeError,
  newAttemptUuid,
  startPractice,
  submitAttempt,
  type FriendlyError,
  type PracticeConfig,
  type PracticePaper,
} from '../api';
import { Button, Card, Note, Screen, TopBar } from '../chrome';
import { IdentityForm } from '../IdentityForm';
import { QuestionRunner } from '../QuestionRunner';
import { saveResult } from '../resultStore';
import { useIdentity } from '../useIdentity';

/** 两档练习。`ranges` 的取值是接口约定的档位名，只进请求体，不作为界面文字。 */
const LEVELS = [
  { range: 'R1', label: '20 以内加减法', hint: '只算 20 以内的数' },
  { range: 'R2', label: '100 以内加减法', hint: '含进位和退位' },
];

interface Session {
  paper: PracticePaper;
  config: PracticeConfig;
  uuid: string;
}

export function PracticePage() {
  const { identity, register } = useIdentity();
  const navigate = useNavigate();
  const [range, setRange] = useState(LEVELS[0].range);
  const [count, setCount] = useState(10);
  const [starting, setStarting] = useState(false);
  const [problem, setProblem] = useState<FriendlyError | null>(null);
  const [session, setSession] = useState<Session | null>(null);

  async function begin() {
    if (identity === null) return;
    const config: PracticeConfig = {
      ops: ['+', '-'],
      ranges: [range],
      question_type: 'oral',
      max_value: null,
      require_carry: false,
      require_borrow: false,
      exact_division: false,
      allow_remainder: false,
      parentheses: false,
      count,
    };
    setStarting(true);
    setProblem(null);
    try {
      const paper = await startPractice(config);
      if (paper.items.length === 0) {
        setProblem({ title: '这次的题目没出出来', hint: '把题量调小一点，或者换一种难度再试。' });
        return;
      }
      setSession({ paper, config, uuid: newAttemptUuid() });
    } catch (error) {
      setProblem(describeError(error));
    } finally {
      setStarting(false);
    }
  }

  // 还没报到：先填姓名学号，填完自动回到本页的选择界面
  if (identity === null) {
    return (
      <Screen>
        <TopBar title="自主练习" />
        <div className="st-body">
          <p className="st-lead">先告诉老师你是谁，做完的题才能算到你的名下。</p>
          <IdentityForm
            title="填一下姓名和学号"
            hint="填过之后这台设备会记住，下次不用再填。"
            initial={null}
            submitLabel="继续"
            onSubmit={async (name, studentNo) => {
              await register(name, studentNo);
            }}
          />
        </div>
      </Screen>
    );
  }

  if (session !== null) {
    return (
      <QuestionRunner
        title="自主练习"
        questions={session.paper.items}
        onExit={() => navigate('/')}
        submit={(answers, durationMs) =>
          submitAttempt({
            studentId: identity.student_id,
            attemptUuid: session.uuid,
            answers,
            durationMs,
            target: { kind: 'practice', config: session.config, seed: session.paper.seed },
          })
        }
        onFinish={(result, answers) => {
          const stored = {
            title: '自主练习',
            source: 'practice' as const,
            paperCode: null,
            result,
            questions: session.paper.items,
            answers,
          };
          saveResult(stored);
          navigate('/result', { state: stored });
        }}
      />
    );
  }

  return (
    <Screen>
      <TopBar title="自主练习" />
      <div className="st-body">
        <Card>
          <p className="st-hello">你好，{identity.name}</p>
          <p className="st-hello-sub">学号 {identity.student_no}</p>
        </Card>

        <Card>
          <p className="st-section-title">练什么</p>
          <div className="st-choices">
            {LEVELS.map((level) => (
              <button
                key={level.range}
                type="button"
                className={`st-choice${level.range === range ? ' st-choice--on' : ''}`}
                aria-pressed={level.range === range}
                onClick={() => setRange(level.range)}
              >
                <span className="st-choice-label">{level.label}</span>
                <span className="st-choice-hint">{level.hint}</span>
              </button>
            ))}
          </div>
        </Card>

        <Card>
          <p className="st-section-title">做几题</p>
          <div className="st-slider-row">
            <input
              className="st-slider"
              type="range"
              min={5}
              max={30}
              step={5}
              value={count}
              aria-label="题目数量"
              onChange={(event) => setCount(Number(event.target.value))}
            />
            <span className="st-slider-value">{count} 题</span>
          </div>
        </Card>

        {problem ? <Note tone="error" title={problem.title} hint={problem.hint} /> : null}

        <Button onClick={() => void begin()} disabled={starting}>
          {starting ? '正在出题…' : '开始练习'}
        </Button>
        <Button variant="ghost" onClick={() => navigate('/')}>
          回首页
        </Button>
      </div>
    </Screen>
  );
}
