/**
 * Switch —— §4 必备状态：on / off / disabled / focus-visible。
 *
 * 关键约定（§4）：文案用**人话**（「保留 / 不留」），不得显示 `block` / `none` 之类的 CSS 值。
 * 所以这里要求调用方同时给 `onLabel` / `offLabel`，而不是让组件去翻译布尔值。
 *
 * 用 `role="switch"` + `aria-checked` 而不是 checkbox：读屏会念「开关，已打开」，
 * 比「复选框，已选中」更贴切；同时保留原生 `button` 的键盘行为（Space/Enter）。
 */

import type { ReactNode } from 'react';

export interface SwitchProps {
  checked: boolean;
  onChange: (next: boolean) => void;
  /** 人话文案：打开时显示（如「保留」） */
  onLabel: string;
  /** 人话文案：关闭时显示（如「不留」） */
  offLabel: string;
  disabled?: boolean;
  /** 无障碍名称；不传则用「label + 当前值」拼一个 */
  ariaLabel?: string;
  label?: ReactNode;
  /** 表单内联提示（可选） */
  hint?: string;
  testId?: string;
  /** 面板上用于定位的字段名（门禁按 `[data-control="<name>"]` 找开关） */
  control?: string;
}

export function Switch({
  checked,
  onChange,
  onLabel,
  offLabel,
  disabled = false,
  ariaLabel,
  label,
  hint,
  testId,
  control,
}: SwitchProps) {
  const state = disabled ? 'disabled' : checked ? 'on' : 'off';
  return (
    <div className="mg-switch-row" data-control={control} data-state={state} data-testid={testId}>
      {label ? <span className="mg-switch-label">{label}</span> : null}
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={ariaLabel ?? (typeof label === 'string' ? label : undefined)}
        disabled={disabled}
        className="mg-switch switch"
        onClick={() => onChange(!checked)}
      >
        <span className="mg-switch-track" aria-hidden="true" />
      </button>
      <span className="mg-switch-value">{checked ? onLabel : offLabel}</span>
      {hint ? <span className="mg-switch-hint">{hint}</span> : null}
    </div>
  );
}
