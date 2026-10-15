/**
 * Toast / ToastStack —— §4 必备状态：info / success / warning / error。
 * 约定：错误必须**可辨识、可关闭**（§4）。
 *
 * 结构轮只提供容器与「可关闭」语义；不做自动消失计时器 ——
 * 自动消失会让「错误提示被吞掉」，而这是 D59 那类问题的温床（界面说成功、其实失败）。
 * 需要自动消失的场合由调用方显式传入 `onDismiss` 自行安排，组件不替它决定。
 */

import type { ReactNode } from 'react';

export type ToastTone = 'info' | 'success' | 'warning' | 'error';

export interface ToastProps {
  tone: ToastTone;
  children: ReactNode;
  onDismiss: () => void;
  testId?: string;
}

export function Toast({ tone, children, onDismiss, testId }: ToastProps) {
  return (
    <div className="mg-toast" data-tone={tone} data-state={tone} role="status" data-testid={testId}>
      <span className="mg-toast-body">{children}</span>
      <button
        type="button"
        className="mg-toast-close"
        aria-label="关闭提示"
        onClick={onDismiss}
        data-testid={testId ? `${testId}-close` : undefined}
      >
        ×
      </button>
    </div>
  );
}

export function ToastStack({ children }: { children: ReactNode }) {
  return (
    <div className="mg-toast-stack" data-layer="toast">
      {children}
    </div>
  );
}
