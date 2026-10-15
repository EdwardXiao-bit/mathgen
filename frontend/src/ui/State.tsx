/**
 * EmptyState / ErrorState —— §4：**必须给出可执行的下一步**或错误码，不得白屏。
 *
 * 两个组件的 API 是刻意「不好用」的：
 *   - `EmptyState` 要求 `action`（下一步动作）；
 *   - `ErrorState` 要求同时给 `what`（发生了什么）与 `how`（怎么办），以及 `code`（错误码）。
 * 类型层面卡住，就不会再出现 M2 那种「只有一句 TimeoutError」的提示（I3）。
 */

import type { ReactNode } from 'react';

export interface EmptyStateProps {
  title: string;
  /** 下一步动作：链接或按钮 */
  action: ReactNode;
  hint?: string;
  testId?: string;
}

export function EmptyState({ title, action, hint, testId }: EmptyStateProps) {
  return (
    <div className="mg-empty" data-state="empty" data-testid={testId} role="status">
      <p className="mg-empty-title">{title}</p>
      {hint ? <p className="mg-empty-hint">{hint}</p> : null}
      <div className="mg-empty-action">{action}</div>
    </div>
  );
}

export interface ErrorStateProps {
  title: string;
  /** 发生了什么（人话） */
  what: string;
  /** 怎么办（下一步） */
  how: string;
  /** 错误码，便于老师报障时对上号 */
  code?: string | null;
  actions?: ReactNode;
  testId?: string;
}

export function ErrorState({ title, what, how, code, actions, testId }: ErrorStateProps) {
  return (
    <div className="mg-error" data-state="error" data-testid={testId} role="alert">
      <h2 className="mg-error-title">{title}</h2>
      <p className="mg-error-what">{what}</p>
      <p className="mg-error-how">{how}</p>
      {code ? (
        <p className="mg-error-code">
          错误码：
          {/* 同时挂 `error-code` class：门禁④按 `.error-code` 断言「错误卡片带错误码」。
              含义不变 —— 这里仍是「错误码文本的元素」。视觉轮若要统一 class 名，
              需要同时改门禁选择器并说明理由。 */}
          <code className="error-code" data-testid={testId ? `${testId}-code` : undefined}>
            {code}
          </code>
        </p>
      ) : null}
      {actions ? <div className="mg-error-actions">{actions}</div> : null}
    </div>
  );
}
