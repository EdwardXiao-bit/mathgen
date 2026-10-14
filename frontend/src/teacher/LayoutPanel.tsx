/**
 * 排版面板 —— 六个控件（FR-T4 / D30）。
 *
 * **实时生效**：控件 `onChange` 直接调 `onChange(patch)`，把新值写进页面级状态，
 * 预览区随之立刻改变 CSS 变量。持久化（`PATCH /api/papers/{id}/layout`）是**另一条路**，
 * 由页面用 debounce 异步做，面板不等待它的结果。
 *
 * 控件的 id / 标签 / CSS 变量名与 `shared/api.ts` 的 `CSS_VAR_NAMES`、`LAYOUT_BOUNDS` 绑定，
 * 保证「界面控件 ↔ 契约字段 ↔ CSS 变量」三者不会各自漂移。
 */

import {
  CSS_VAR_NAMES,
  LAYOUT_BOUNDS,
  type LayoutBoolField,
  type LayoutIn,
  type LayoutNumberField,
} from '../shared/api';

interface NumberControl {
  kind: 'number';
  field: LayoutNumberField;
  label: string;
  unit: string;
  step: number;
  decimals: number;
  hint: string;
}

interface BoolControl {
  kind: 'bool';
  field: LayoutBoolField;
  label: string;
  onLabel: string;
  offLabel: string;
  hint: string;
}

type Control = NumberControl | BoolControl;

/** 六项控件，顺序与 FR-T4 列举一致。 */
export const LAYOUT_CONTROLS: readonly Control[] = [
  {
    kind: 'number',
    field: 'cols_per_row',
    label: '每行题数',
    unit: '题/行',
    step: 1,
    decimals: 0,
    hint: 'CSS Grid 的列数',
  },
  {
    kind: 'number',
    field: 'row_gap',
    label: '行间距',
    unit: '倍',
    step: 0.1,
    decimals: 1,
    hint: '按 calc(var(--row-gap) * 1em) 作用于题块间距',
  },
  {
    kind: 'number',
    field: 'font_size',
    label: '字号',
    unit: 'pt',
    step: 1,
    decimals: 0,
    hint: '题目文本字号',
  },
  {
    kind: 'number',
    field: 'page_margin',
    label: '页边距',
    unit: 'cm',
    step: 0.5,
    decimals: 1,
    hint: '同时映射到 @page { margin }',
  },
  {
    kind: 'bool',
    field: 'answer_line',
    label: '答题横线',
    onLabel: '留空位',
    offLabel: '不留',
    hint: '是否留答题横线 / 书写空位',
  },
  {
    kind: 'bool',
    field: 'header_bar',
    label: '卷头栏',
    onLabel: '显示',
    offLabel: '隐藏',
    hint: '班级 / 姓名 / 得分信息栏',
  },
];

export interface LayoutPanelProps {
  layout: LayoutIn;
  onChange: (patch: Partial<LayoutIn>) => void;
  onReset: () => void;
  /** 「持久化排版」的状态提示：保存中 / 已保存 / 保存失败（失败不影响预览）。 */
  persistState: 'idle' | 'saving' | 'saved' | 'error';
  persistError: string | null;
  /** 后端不可用时也允许继续调参 —— 明确告诉老师改的只是本地预览。 */
  localOnly: boolean;
}

const PERSIST_TEXT: Record<LayoutPanelProps['persistState'], string> = {
  idle: '排版改动会实时生效；松开控件后自动保存到后端',
  saving: '正在保存排版到后端…',
  saved: '排版已保存到后端',
  error: '排版保存失败（预览仍然实时生效）',
};

export function LayoutPanel({
  layout,
  onChange,
  onReset,
  persistState,
  persistError,
  localOnly,
}: LayoutPanelProps) {
  return (
    <aside className="layout-panel" aria-label="排版面板">
      <div className="layout-panel-head">
        <h2>排版面板</h2>
        <button type="button" className="btn btn-ghost" data-testid="layout-reset" onClick={onReset}>
          恢复默认
        </button>
      </div>
      <p className="layout-panel-note">
        六项参数直接驱动预览区的 CSS 变量，不往返服务器；拖动或拨动开关时右侧版式立刻变化。
      </p>

      <div className="layout-controls">
        {LAYOUT_CONTROLS.map((control) => {
          const cssVar = CSS_VAR_NAMES[control.field];
          if (control.kind === 'bool') {
            const checked = layout[control.field];
            return (
              <div className="layout-control" key={control.field} data-control={control.field}>
                <div className="layout-control-head">
                  <label htmlFor={`layout-${control.field}`}>{control.label}</label>
                  <span className="layout-control-value">{checked ? control.onLabel : control.offLabel}</span>
                </div>
                <label className="switch">
                  <input
                    id={`layout-${control.field}`}
                    data-testid={`layout-${control.field}`}
                    type="checkbox"
                    checked={checked}
                    onChange={(event) => onChange({ [control.field]: event.target.checked })}
                  />
                  <span className="switch-track" aria-hidden="true" />
                </label>
                <p className="layout-control-hint">
                  <code>{cssVar}</code> = {checked ? '1' : '0'} · {control.hint}
                </p>
              </div>
            );
          }

          const [min, max] = LAYOUT_BOUNDS[control.field];
          const value = layout[control.field];
          return (
            <div className="layout-control" key={control.field} data-control={control.field}>
              <div className="layout-control-head">
                <label htmlFor={`layout-${control.field}`}>{control.label}</label>
                <span className="layout-control-value">
                  {control.decimals === 0 ? value.toFixed(0) : value.toFixed(control.decimals)} {control.unit}
                </span>
              </div>
              <input
                id={`layout-${control.field}`}
                data-testid={`layout-${control.field}`}
                type="range"
                min={min}
                max={max}
                step={control.step}
                value={value}
                onChange={(event) =>
                  onChange({ [control.field]: Number(event.target.value) })
                }
              />
              <div className="layout-control-range">
                <span>{min}</span>
                <span>…</span>
                <span>{max}</span>
              </div>
              <p className="layout-control-hint">
                <code>{cssVar}</code> · {control.hint}
              </p>
            </div>
          );
        })}
      </div>

      <div className="layout-panel-status" data-persist-state={persistState}>
        {localOnly ? '后端未连接：排版只在本地预览生效，无法打印后端渲染的卷面' : PERSIST_TEXT[persistState]}
        {persistError ? <span className="layout-panel-error"> {persistError}</span> : null}
      </div>
    </aside>
  );
}
