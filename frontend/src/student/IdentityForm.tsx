/**
 * 报到表单：姓名 + 学号（**均必填**，D41）。
 *
 * 两处入口共用它：首页报到、以及扫码直接落到卷子页时先补填身份。
 * 学号用 `inputMode="numeric"` 只是给出数字键盘倾向——`type` 仍是文本，
 * 因为学号形态由老师定（有的班级带字母前缀），不能在前端替老师做限制。
 */
import { useState, type FormEvent } from 'react';

import { describeError, type FriendlyError } from './api';
import { Button, Note } from './chrome';

export interface IdentityFormProps {
  title: string;
  hint: string;
  /** 已记住的身份，用来预填（仍然允许改） */
  initial?: { name: string; student_no: string } | null;
  submitLabel: string;
  onSubmit: (name: string, studentNo: string) => Promise<void>;
}

export function IdentityForm({ title, hint, initial, submitLabel, onSubmit }: IdentityFormProps) {
  const [name, setName] = useState(initial?.name ?? '');
  const [studentNo, setStudentNo] = useState(initial?.student_no ?? '');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<FriendlyError | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (name.trim() === '' || studentNo.trim() === '') {
      setProblem({ title: '姓名和学号都要填', hint: '学号是老师用来区分同名同学的，请照着老师给的写。' });
      return;
    }
    setBusy(true);
    setProblem(null);
    try {
      await onSubmit(name, studentNo);
    } catch (error) {
      setProblem(describeError(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="st-form" onSubmit={handleSubmit}>
      <h2 className="st-form-title">{title}</h2>
      <p className="st-form-hint">{hint}</p>

      <label className="st-field" htmlFor="st-name">
        <span className="st-field-label">姓名</span>
        <input
          id="st-name"
          className="st-input"
          type="text"
          autoComplete="name"
          enterKeyHint="next"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </label>

      <label className="st-field" htmlFor="st-student-no">
        <span className="st-field-label">学号</span>
        <input
          id="st-student-no"
          className="st-input"
          type="text"
          inputMode="numeric"
          enterKeyHint="done"
          value={studentNo}
          onChange={(event) => setStudentNo(event.target.value)}
        />
      </label>

      {problem ? <Note tone="error" title={problem.title} hint={problem.hint} /> : null}

      <Button type="submit" disabled={busy}>
        {busy ? '正在记录…' : submitLabel}
      </Button>
    </form>
  );
}
