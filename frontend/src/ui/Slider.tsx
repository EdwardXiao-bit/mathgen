/**
 * Slider —— §4 必备状态：默认 / dragging / disabled / focus-visible。
 *
 * 关键约定（§4）：**必须显示当前数值与量纲**（如 `4 题/行`、`2.0 cm`）。
 * 所以 `valueText` 是必填 prop —— 组件层面不允许出现「只有一个光秃秃滑块、看不出当前值」的控件。
 *
 * `dragging` 状态用原生 pointer 事件维护：门禁可以据此断言「拖动中」这个状态真的存在
 * （视觉轮要给它加实时反馈，结构轮先把状态挂上）。
 */

import { useState } from 'react';

export interface SliderProps {
  value: number;
  min: number;
  max: number;
  step: number;
  /** 当前值的人话表达，含量纲：`4 题/行` */
  valueText: string;
  onChange: (next: number) => void;
  disabled?: boolean;
  label: string;
  /** 门禁按 `[data-testid=<testId>]` 定位这个 range 输入 */
  testId?: string;
  /** 面板字段名（门禁按 `[data-control="<name>"]` 找控件） */
  control?: string;
  /** 量纲两端刻度文案 */
  scaleLabels?: [string, string];
}

export function Slider({
  value,
  min,
  max,
  step,
  valueText,
  onChange,
  disabled = false,
  label,
  testId,
  control,
  scaleLabels,
}: SliderProps) {
  const [dragging, setDragging] = useState(false);
  const state = disabled ? 'disabled' : dragging ? 'dragging' : 'default';
  const inputId = `slider-${control ?? label}`;
  return (
    <div className="mg-slider" data-control={control} data-state={state}>
      <div className="mg-slider-head">
        <label className="mg-slider-label" htmlFor={inputId}>
          {label}
        </label>
        <span className="mg-slider-value">{valueText}</span>
      </div>
      <input
        id={inputId}
        data-testid={testId}
        className="mg-slider-input"
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
        onPointerDown={() => setDragging(true)}
        onPointerUp={() => setDragging(false)}
        onPointerCancel={() => setDragging(false)}
        onBlur={() => setDragging(false)}
      />
      {scaleLabels ? (
        <div className="mg-slider-scale">
          <span>{scaleLabels[0]}</span>
          <span>{scaleLabels[1]}</span>
        </div>
      ) : null}
    </div>
  );
}
