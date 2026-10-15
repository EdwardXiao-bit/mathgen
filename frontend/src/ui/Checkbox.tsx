/**
 * Checkbox —— §4 的 `Input` 家族里用于「多选」的形态（运算类型 / 数值范围 / 特殊约束）。
 *
 * 与 `Chip` 的分工：`Chip` 是视觉上的胶囊选项（可多选也可单选），`Checkbox` 是
 * 带 `<input type="checkbox">` 的表单控件 —— 需要原生表单语义（读屏、`form` 提交、
 * 键盘 Space）时用它，纯视觉选择用 `Chip`。
 *
 * 状态：默认 / checked / disabled / focus-visible。`focus-visible` 由全局焦点环提供。
 *
 * `className`：默认渲染成胶囊（`chip` / `chip-on`）——那是老师端既有的多选视觉，
 * 也承接着门禁④ `[data-dimension="ops"] .chip` 这类断言；需要列表式布局时传 `mg-checkbox`。
 */

import type { ReactNode } from 'react';

export interface CheckboxProps {
  checked: boolean;
  onChange: (next: boolean) => void;
  children: ReactNode;
  disabled?: boolean;
  /** 无障碍名称；不给时读屏会用 children 文本 */
  ariaLabel?: string;
  testId?: string;
  /** 表单分组用的字段名（门禁按 `[data-testid=<testId>]` 定位） */
  name?: string;
  title?: string;
  /** 覆盖默认外观（默认是胶囊多选） */
  className?: string;
}

export function Checkbox({
  checked,
  onChange,
  children,
  disabled = false,
  ariaLabel,
  testId,
  name,
  title,
  className,
}: CheckboxProps) {
  const appearance = className ?? 'chip';
  const onClass = className ? '' : 'chip-on';
  return (
    <label
      className={[appearance, checked ? onClass : ''].filter(Boolean).join(' ')}
      data-state={disabled ? 'disabled' : checked ? 'on' : 'off'}
      title={title}
    >
      <input
        type="checkbox"
        name={name}
        checked={checked}
        disabled={disabled}
        aria-label={ariaLabel}
        data-testid={testId}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span>{children}</span>
    </label>
  );
}
