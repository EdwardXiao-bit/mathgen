/**
 * 第六维「答案版形式」的四个开关（M3 起正式接入）。
 *
 * 抽成独立组件的原因：它在**两个地方**用同一份语义 ——
 *   ① `/teacher` 出题配置页：勾好后随创建一起落库（见 `teacherApi.createPaper`）
 *   ② `/teacher/paper/:id` 卷页：对已存在的卷直接 `PATCH .../answer-layout`
 * 两处若各写一遍，字段名/文案/默认值会漂移（这是本项目已发生过的失效模式）。
 *
 * **只读态不是「禁用就算」**：已发布的卷后端会返回 409 `PAPER_NOT_EDITABLE`，
 * 界面必须把「为什么不能改」讲清楚，并且**不允许显示成已保存**（D59 的教训）。
 */

import { ANSWER_LAYOUT_FIELDS, type AnswerLayoutIn } from '../shared/api';

export interface AnswerLayoutEditorProps {
  value: AnswerLayoutIn;
  onChange: (next: AnswerLayoutIn) => void;
  /** 只读（已发布卷）：控件禁用 + 说明原因 */
  readOnly?: boolean;
  /** 只读原因（例如「已发布卷是只读快照」） */
  readOnlyReason?: string;
  /** 是否显示每个开关的说明小字 */
  showHints?: boolean;
  /** 表单前缀：同一页可能渲染多个实例，用来保证 id 唯一 */
  idPrefix?: string;
}

export function AnswerLayoutEditor({
  value,
  onChange,
  readOnly = false,
  readOnlyReason,
  showHints = true,
  idPrefix = 'answer-layout',
}: AnswerLayoutEditorProps) {
  return (
    <div
      className="answer-layout-editor"
      data-testid="answer-layout-editor"
      data-readonly={readOnly ? '1' : '0'}
    >
      <div className="switch-grid">
        {ANSWER_LAYOUT_FIELDS.map((item) => {
          const checked = value[item.field];
          const id = `${idPrefix}-${item.field}`;
          return (
            <label className="switch-row" key={item.field} htmlFor={id}>
              <input
                id={id}
                data-testid={`answer-layout-${item.field}`}
                type="checkbox"
                checked={checked}
                disabled={readOnly}
                onChange={(event) => onChange({ ...value, [item.field]: event.target.checked })}
              />
              <span className="switch-row-label">{item.label}</span>
              <span className="switch-row-hint">
                {checked ? item.onLabel : item.offLabel}
                {showHints ? ` · ${item.hint}` : ''}
              </span>
            </label>
          );
        })}
      </div>

      {readOnly ? (
        <p className="dim-hint" data-testid="answer-layout-readonly-reason">
          {readOnlyReason ??
            '已发布的卷是只读快照：答案版形式不能再改（后端会拒绝 PAPER_NOT_EDITABLE）。需要调整请先「另存为可编辑副本」。'}
        </p>
      ) : null}
    </div>
  );
}
