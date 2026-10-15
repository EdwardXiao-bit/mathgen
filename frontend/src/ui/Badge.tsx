/**
 * Badge / Chip —— §4 必备状态：默认 / 语义色。
 * 用途：卷号、题量、状态（§4）。
 *
 * 语义色一律走令牌（`--mg-accent` / `--mg-success` / `--mg-danger`），
 * 组件不接受颜色值 —— 这样「赭橙只用于强调」这条纪律在接口层面就成立（§3：克制使用）。
 */

import type { ReactNode } from 'react';

export type Tone = 'neutral' | 'accent' | 'success' | 'danger';

export interface BadgeProps {
  tone?: Tone;
  children: ReactNode;
  testId?: string;
}

export function Badge({ tone = 'neutral', children, testId }: BadgeProps) {
  return (
    <span className="mg-badge" data-tone={tone} data-testid={testId}>
      {children}
    </span>
  );
}

export interface ChipProps {
  tone?: Tone;
  children: ReactNode;
  /** 可点选（用于「运算类型 / 题型」这类多选） */
  selected?: boolean;
  disabled?: boolean;
  onClick?: () => void;
  /** 只读展示用（不渲染成按钮） */
  static?: boolean;
  title?: string;
  testId?: string;
}

export function Chip({
  tone = 'neutral',
  children,
  selected = false,
  disabled = false,
  onClick,
  static: isStatic = false,
  title,
  testId,
}: ChipProps) {
  const state = disabled ? 'disabled' : selected ? 'on' : 'off';
  if (isStatic || !onClick) {
    return (
      <span
        className={['mg-chip', selected ? 'mg-chip-on' : ''].filter(Boolean).join(' ')}
        data-tone={tone}
        data-state={state}
        title={title}
        data-testid={testId}
      >
        {children}
      </span>
    );
  }
  return (
    <button
      type="button"
      className={['mg-chip', selected ? 'mg-chip-on' : ''].filter(Boolean).join(' ')}
      data-tone={tone}
      data-state={state}
      title={title}
      disabled={disabled}
      aria-pressed={selected}
      onClick={onClick}
      data-testid={testId}
    >
      {children}
    </button>
  );
}
