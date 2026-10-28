/**
 * 做老师发的卷子（`/student/paper/:code`）。
 *
 * 页面在两种入口下都要成立：
 * 1. 扫卷子上的二维码（URL 里直接带卷号）—— 最常见；
 * 2. 在首页手输卷号（同样是这个路由）。
 *
 * 取卷失败**不自动重试**：卷号错、卷子没发，重试一万次也一样，不如把原因和下一步告诉学生。
 */
import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import {
  describeError,
  loadPaper,
  newAttemptUuid,
  submitAttempt,
  type FriendlyError,
  type StudentPaper,
} from '../api';
import { Button, Loading, Note, Screen, TopBar } from '../chrome';
import { IdentityForm } from '../IdentityForm';
import { QuestionRunner } from '../QuestionRunner';
import { saveResult } from '../resultStore';
import { useIdentity } from '../useIdentity';

export function PaperPage() {
  const params = useParams();
  const code = (params.code ?? '').trim();
  const { identity, register } = useIdentity();
  const navigate = useNavigate();

  const [paper, setPaper] = useState<StudentPaper | null>(null);
  const [loading, setLoading] = useState(false);
  const [problem, setProblem] = useState<FriendlyError | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const [uuid] = useState(() => newAttemptUuid());

  useEffect(() => {
    // 还没报到就先不取题：报到之后 identity 变化会自动再跑一次
    if (identity === null || code === '') return;
    let alive = true;
    setLoading(true);
    setProblem(null);
    loadPaper(code)
      .then((value) => {
        if (alive) setPaper(value);
      })
      .catch((error: unknown) => {
        if (alive) setProblem(describeError(error));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [code, identity, reloadToken]);

  if (code === '') {
    return (
      <Screen>
        <TopBar title="做卷子" />
        <div className="st-body">
          <Note tone="error" title="没有拿到卷号" hint="请重新扫一遍老师卷子上的二维码。" />
          <Button onClick={() => navigate('/')}>回首页</Button>
        </div>
      </Screen>
    );
  }

  if (identity === null) {
    return (
      <Screen>
        <TopBar title="做卷子" />
        <div className="st-body">
          <p className="st-lead">老师把卷子发给你了。</p>
          <IdentityForm
            title="填一下姓名和学号"
            hint="填错也不要紧，退出重进可以重新填。"
            initial={null}
            submitLabel="开始答题"
            onSubmit={async (name, studentNo) => {
              await register(name, studentNo);
            }}
          />
        </div>
      </Screen>
    );
  }

  if (loading) {
    return (
      <Screen>
        <TopBar title="做卷子" />
        <div className="st-body">
          <Loading text="正在取卷子…" />
        </div>
      </Screen>
    );
  }

  if (paper === null) {
    const fail = problem ?? { title: '没有取到卷子', hint: '请稍后重试。' };
    return (
      <Screen>
        <TopBar title="做卷子" />
        <div className="st-body">
          <Note tone="error" title={fail.title} hint={fail.hint} />
          <Button onClick={() => setReloadToken(reloadToken + 1)}>再试一次</Button>
          <Button variant="ghost" onClick={() => navigate('/')}>
            回首页
          </Button>
        </div>
      </Screen>
    );
  }

  if (paper.items.length === 0) {
    return (
      <Screen>
        <TopBar title="做卷子" />
        <div className="st-body">
          <Note title="这份卷子还没有题目" hint="请告诉老师，让老师重新发一次。" />
          <Button variant="ghost" onClick={() => navigate('/')}>
            回首页
          </Button>
        </div>
      </Screen>
    );
  }

  return (
    <QuestionRunner
      title={paper.title}
      questions={paper.items}
      onExit={() => navigate('/')}
      submit={(answers, durationMs) =>
        submitAttempt({
          studentId: identity.student_id,
          attemptUuid: uuid,
          answers,
          durationMs,
          target: { kind: 'paper', paperId: paper.paper_id },
        })
      }
      onFinish={(result, answers) => {
        const stored = {
          title: paper.title,
          source: 'paper' as const,
          paperCode: code,
          result,
          questions: paper.items,
          answers,
        };
        saveResult(stored);
        navigate('/result', { state: stored });
      }}
    />
  );
}
