/**
 * 学生端入口页（`/student`）：报到 + 两个入口。
 *
 * 「记住身份」与「允许改身份」是一对需求（一个班可能共用一台平板）：
 * 所以已记住时显示身份卡片，并提供「修改」（预填当前值，改错别字用）与
 * 「换一位同学」（清空重填，给下一位同学用）两个动作。
 */
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import type { FriendlyError } from '../api';
import { Button, Card, Note, Screen, TopBar } from '../chrome';
import { IdentityForm } from '../IdentityForm';
import { useIdentity } from '../useIdentity';

type EditMode = null | 'fix' | 'new';

export function EntryPage() {
  const { identity, register, forget } = useIdentity();
  const [editing, setEditing] = useState<EditMode>(null);
  const [askingCode, setAskingCode] = useState(false);
  const [code, setCode] = useState('');
  const [problem, setProblem] = useState<FriendlyError | null>(null);
  const navigate = useNavigate();

  function openPaper() {
    const value = code.trim();
    if (value === '') {
      setProblem({
        title: '还没有填卷号',
        hint: '卷号在老师发下来的卷子右上角，也可以直接扫卷子上的二维码。',
      });
      return;
    }
    setProblem(null);
    navigate(`/paper/${encodeURIComponent(value)}`);
  }

  return (
    <Screen>
      <TopBar title="数学练习" />
      <div className="st-body">
        {identity !== null && editing === null ? (
          <>
            <Card>
              <p className="st-hello">你好，{identity.name}</p>
              <p className="st-hello-sub">学号 {identity.student_no}</p>
              <div className="st-row">
                <button type="button" className="st-quiet" onClick={() => setEditing('fix')}>
                  修改
                </button>
                <button
                  type="button"
                  className="st-quiet"
                  onClick={() => {
                    forget();
                    setEditing('new');
                  }}
                >
                  换一位同学
                </button>
              </div>
            </Card>

            <p className="st-lead">想做哪一种？</p>
            <Button onClick={() => navigate('/practice')}>自主练习</Button>

            {askingCode ? (
              <Card>
                <label className="st-field" htmlFor="st-code">
                  <span className="st-field-label">卷号</span>
                  <input
                    id="st-code"
                    className="st-input"
                    type="text"
                    inputMode="numeric"
                    autoComplete="off"
                    value={code}
                    onChange={(event) => setCode(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') openPaper();
                    }}
                  />
                </label>
                <div className="st-row">
                  <Button
                    variant="ghost"
                    onClick={() => {
                      setAskingCode(false);
                      setProblem(null);
                    }}
                  >
                    取消
                  </Button>
                  <Button onClick={openPaper}>开始做卷子</Button>
                </div>
              </Card>
            ) : (
              <Button
                variant="ghost"
                onClick={() => {
                  setAskingCode(true);
                  setProblem(null);
                }}
              >
                输入卷号做卷子
              </Button>
            )}
          </>
        ) : (
          <IdentityForm
            title={editing === null ? '先填一下姓名和学号' : editing === 'fix' ? '修改姓名和学号' : '换一位同学'}
            hint="填过之后会记住这台设备，连着做几份就不用重复填；下一位同学可以点「换一位同学」重来。"
            initial={editing === 'fix' ? identity : null}
            submitLabel={editing === null ? '开始' : '保存'}
            onSubmit={async (name, studentNo) => {
              await register(name, studentNo);
              setEditing(null);
            }}
          />
        )}

        {problem ? <Note tone="error" title={problem.title} hint={problem.hint} /> : null}
      </div>
    </Screen>
  );
}
