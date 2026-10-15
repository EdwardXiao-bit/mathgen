/**
 * Input / NumberInput —— §4 必备状态：默认 / focus / disabled / **invalid**。
 *
 * `invalid` 的关键约定（§4）：**必须同时给出文字原因**，不能只变红。
 * 所以这里的 API 是 `invalidReason?: string` 而不是 `invalid?: boolean` ——
 * 想让输入框变红就必须同时说清为什么，接口层面杜绝「只变红不说原因」。
 * 原因通过 `aria-describedby` 关联到消息元素上（读屏与门禁都能读到）。
 */

import { useId, type InputHTMLAttributes } from 'react';

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> {
  /** 校验失败的文字原因；给了就进 invalid 态并渲染这段文字 */
  invalidReason?: string | null;
  /** 输入框上方的标签（可选；给了就自动关联 label/for） */
  label?: string;
}

export function Input({
  invalidReason,
  label,
  className,
  id,
  ...rest
}: InputProps) {
  const autoId = useId();
  const inputId = id ?? autoId;
  const reasonId = `${inputId}-reason`;
  const invalid = Boolean(invalidReason);
  return (
    <span className="mg-field">
      {label ? (
        <label className="mg-field-label" htmlFor={inputId}>
          {label}
        </label>
      ) : null}
      <input
        {...rest}
        id={inputId}
        className={['mg-input', className ?? ''].filter(Boolean).join(' ')}
        data-state={rest.disabled ? 'disabled' : invalid ? 'invalid' : 'default'}
        aria-invalid={invalid || undefined}
        aria-describedby={invalid ? reasonId : undefined}
      />
      {invalid ? (
        <span className="mg-field-reason" id={reasonId} role="alert">
          {invalidReason}
        </span>
      ) : null}
    </span>
  );
}

export interface NumberInputProps extends Omit<InputProps, 'type' | 'onChange' | 'value'> {
  value: number | '';
  onValueChange: (next: number | '') => void;
  min?: number;
  max?: number;
  /** 量纲后缀，如 `题/行`、`cm`、`题` */
  unit?: string;
  /** 门禁定位用（写在里面的 `<input>` 上） */
  testId?: string;
}

/** 数字输入：与 `Input` 同一套状态，额外保证 `min`/`max` 越界时进 `invalid` 并说明原因。 */
export function NumberInput({
  value,
  onValueChange,
  min,
  max,
  unit,
  invalidReason,
  testId,
  ...rest
}: NumberInputProps) {
  const numeric = typeof value === 'number' ? value : null;
  let ownReason: string | null = null;
  if (numeric !== null) {
    if (min !== undefined && numeric < min) ownReason = `不能小于 ${min}`;
    else if (max !== undefined && numeric > max) ownReason = `不能大于 ${max}`;
  }
  const reason = invalidReason ?? ownReason;
  return (
    <span className="mg-number-field">
      <Input
        {...rest}
        type="number"
        inputMode="numeric"
        value={value}
        min={min}
        max={max}
        invalidReason={reason}
        data-testid={testId}
        onChange={(event) => {
          const raw = event.target.value;
          onValueChange(raw === '' ? '' : Number(raw));
        }}
      />
      {unit ? <span className="mg-number-unit">{unit}</span> : null}
    </span>
  );
}
