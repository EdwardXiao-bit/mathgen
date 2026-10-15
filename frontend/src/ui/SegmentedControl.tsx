/**
 * SegmentedControl —— §4 必备状态：选中 / 未选 / disabled。
 * 用于「紧凑 / 常规 / 宽松」这类**互斥**选择（§4）。
 *
 * 用 radiogroup 语义：读屏会念「单选组」，且方向键可在组内移动（原生 radio 行为）。
 * `data-state` 只标当前值，供门禁断言选中真的变了。
 */

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  disabled?: boolean;
}

export interface SegmentedControlProps<T extends string> {
  name: string;
  options: ReadonlyArray<SegmentedOption<T>>;
  value: T;
  onChange: (next: T) => void;
  ariaLabel: string;
  testId?: string;
}

export function SegmentedControl<T extends string>({
  name,
  options,
  value,
  onChange,
  ariaLabel,
  testId,
}: SegmentedControlProps<T>) {
  return (
    <div
      className="mg-segmented"
      role="radiogroup"
      aria-label={ariaLabel}
      data-state={value}
      data-testid={testId}
    >
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <label
            key={option.value}
            className={['mg-segment', selected ? 'mg-segment-on' : ''].filter(Boolean).join(' ')}
            data-state={option.disabled ? 'disabled' : selected ? 'on' : 'off'}
          >
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={selected}
              disabled={option.disabled}
              onChange={() => onChange(option.value)}
            />
            <span>{option.label}</span>
          </label>
        );
      })}
    </div>
  );
}
