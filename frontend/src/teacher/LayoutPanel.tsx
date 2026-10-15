/**
 * 排版面板 —— 六个控件（FR-T4 / D30）。
 *
 * **实时生效**：控件 `onChange` 直接调 `onChange(patch)`，把新值写进页面级状态，
 * 预览区随之立刻改变 CSS 变量。持久化（`PATCH /api/papers/{id}/layout`）是**另一条路**，
 * 由页面用 debounce 异步做，面板不等待它的结果。
 *
 * 控件的 id / 标签 / CSS 变量名与 `shared/api.ts` 的 `CSS_VAR_NAMES`、`LAYOUT_BOUNDS` 绑定，
 * 保证「界面控件 ↔ 契约字段 ↔ CSS 变量」三者不会各自漂移。
 *
 * M3.5：控件改用组件层（`Slider` / `Switch`），状态（disabled / focus-visible / dragging）
 * 与其它页面统一 —— 面板不再自己写一套 `<input type="range">` 样式。
 * 面板状态（保存中 / 已保存 / 失败）也改走组件层语义，见底部的 `PanelStatusLine`。
 */

import {
  CSS_DISPLAY_VALUES,
  CSS_VAR_NAMES,
  LAYOUT_BOUNDS,
  type LayoutBoolField,
  type LayoutIn,
  type LayoutNumberField,
} from '../shared/api';
import { Slider, Switch } from '../ui';

interface NumberControl {
  kind: 'number';
  field: LayoutNumberField;
  label: string;
  unit: string;
  step: number;
  decimals: number;
  hint: string;
  /**
   * 一项参数在屏幕与打印上表现不同时的提醒。
   *
   * 目前 `--page-margin` 会在 `@page { margin }`（打印 / PDF）和
   * `@media screen` 的纸面 box（预览）两处生效，所以这里说明它一定影响打印 / PDF；
   * 如果哪一天渲染器去掉了屏幕纸面，老师会觉得「拖了没反应」，这条说明就不该删。
   */
  screenNote?: string;
}

interface BoolControl {
  kind: 'bool';
  field: LayoutBoolField;
  label: string;
  onLabel: string;
  offLabel: string;
  hint: string;
  /** 该布尔项在 CSS 里的实际取值（D59：display 关键字），只用于小字展示与核对。 */
  cssOn: string;
  cssOff: string;
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
    hint: '每行排几道题',
  },
  {
    kind: 'number',
    field: 'row_gap',
    label: '行间距',
    unit: '倍',
    step: 0.1,
    decimals: 1,
    hint: '题与题之间的疏密',
  },
  {
    kind: 'number',
    field: 'font_size',
    label: '字号',
    unit: 'pt',
    step: 1,
    decimals: 0,
    hint: '题目文字大小',
  },
  {
    kind: 'number',
    field: 'page_margin',
    label: '页边距',
    unit: 'cm',
    step: 0.5,
    decimals: 1,
    hint: '纸张四周留白：预览的纸面与打印 / PDF 同时生效',
    screenNote: '打印 / PDF 一定生效',
  },
  {
    kind: 'bool',
    field: 'answer_line',
    label: '答题横线',
    onLabel: '保留',
    offLabel: '不留',
    hint: '题目下方是否留书写空位',
    cssOn: CSS_DISPLAY_VALUES.answer_line.on,
    cssOff: CSS_DISPLAY_VALUES.answer_line.off,
  },
  {
    kind: 'bool',
    field: 'header_bar',
    label: '卷头栏',
    onLabel: '显示',
    offLabel: '隐藏',
    hint: '班级 / 姓名 / 得分信息栏',
    cssOn: CSS_DISPLAY_VALUES.header_bar.on,
    cssOff: CSS_DISPLAY_VALUES.header_bar.off,
  },
];

export interface LayoutPanelProps {
  layout: LayoutIn;
  onChange: (patch: Partial<LayoutIn>) => void;
  onReset: () => void;
  /**
   * 「持久化排版」的状态：
   *   saving      正在 PATCH
   *   refreshing  已保存，正在重新加载卷面（D59 兜底：让预览等于后端当前渲染）
   *   refreshed   保存并刷新完成 —— 此时才敢说「看到的就是最新的」
   *   error       保存失败（预览仍然实时生效，只是没落库）
   */
  persistState: 'idle' | 'saving' | 'refreshing' | 'refreshed' | 'error';
  persistError: string | null;
  /** 后端不可用时也允许继续调参 —— 明确告诉老师改的只是本地预览。 */
  localOnly: boolean;
}

const PERSIST_TEXT: Record<LayoutPanelProps['persistState'], string> = {
  idle: '拖动或拨动开关时预览立刻变化；松开后自动保存到后端',
  saving: '正在保存排版到后端…',
  refreshing: '已保存，正在按后端渲染结果刷新预览…',
  refreshed: '已保存，预览已刷新（当前显示 = 后端渲染结果）',
  error: '排版保存失败（预览仍然实时生效，但没有落库）',
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
        保存成功后会自动重新加载卷面，确保预览与后端渲染结果一致。
      </p>

      <div className="layout-controls">
        {LAYOUT_CONTROLS.map((control) => {
          const cssVar = CSS_VAR_NAMES[control.field];
          if (control.kind === 'bool') {
            const checked = layout[control.field];
            return (
              <div className="layout-control" key={control.field}>
                {/* 人话文案（「保留 / 不留」「显示 / 隐藏」）由 Switch 的 onLabel/offLabel 承载；
                    CSS 关键字（block/none）只出现在下面的调试小字里，不怼给老师看（D59）。 */}
                <Switch
                  control={control.field}
                  checked={checked}
                  onLabel={control.onLabel}
                  offLabel={control.offLabel}
                  label={control.label}
                  ariaLabel={control.label}
                  testId={`layout-${control.field}`}
                  onChange={(next) => onChange({ [control.field]: next })}
                />
                <p className="layout-control-hint">
                  {control.hint} · <code className="var-chip">{cssVar}</code>
                  <code className="var-chip">
                    {checked ? control.cssOn : control.cssOff}
                  </code>
                </p>
              </div>
            );
          }

          const [min, max] = LAYOUT_BOUNDS[control.field];
          const value = layout[control.field];
          // §4：Slider 必须显示当前数值与量纲（`4 题/行`、`2.0 cm`）
          const valueText = `${control.decimals === 0 ? value.toFixed(0) : value.toFixed(control.decimals)} ${control.unit}`;
          return (
            <div className="layout-control" key={control.field}>
              <Slider
                control={control.field}
                testId={`layout-${control.field}`}
                label={control.label}
                min={min}
                max={max}
                step={control.step}
                value={value}
                valueText={valueText}
                scaleLabels={[String(min), String(max)]}
                onChange={(next) => onChange({ [control.field]: next })}
              />
              <p className="layout-control-hint">
                {control.hint}
                {control.screenNote ? (
                  <span className="screen-note" data-testid={`screen-note-${control.field}`}>
                    {control.screenNote}
                  </span>
                ) : null}{' '}
                <code className="var-chip">{cssVar}</code>
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
