/**
 * 出题配置表单 —— 六维（FR-T1 / §4.3）：
 *   ① 运算类型  ② 数值范围 R1–R7  ③ 特殊约束开关  ④ 题型  ⑤ 题量  ⑥ 答案版形式
 *
 * 选项来源：`shared/api.ts`（运算类型/题型 = D17，档位 = D16，约束 = D16 尾部说明）。
 * 约束规则的前端提示来自 `validateConfig()`；**权威判定仍在后端**（会返回 CONFIG_INVALID）。
 *
 * 第六维「答案版形式」在 M2 是**只读**的：`PaperConfigIn` 里没有对应字段，
 * `paper.answer_layout` 留到 M3，所以这里只展示 D20 默认值 + 「M3 起生效」说明，
 * 不做成可点控件，也不在本地存任何「假装生效」的状态。
 */

import { useState } from 'react';
import {
  CONFIG_BOUNDS,
  DEFAULT_ANSWER_LAYOUT,
  DEFAULT_CONFIG,
  OP_OPTIONS,
  QUESTION_TYPE_OPTIONS,
  RANGE_OPTIONS,
  validateConfig,
  type AnswerLayoutIn,
  type OpName,
  type PaperConfigIn,
  type QuestionTypeName,
  type RangeName,
} from '../shared/api';
import { AnswerLayoutEditor } from './AnswerLayoutEditor';
import { Checkbox, NumberInput } from '../ui';

export interface ConfigDraft {
  config: PaperConfigIn;
  seedText: string;
  title: string;
  /** 第六维「答案版形式」——随创建一起落库（M3） */
  answerLayout: AnswerLayoutIn;
}

export const DEFAULT_DRAFT: ConfigDraft = {
  config: DEFAULT_CONFIG,
  seedText: '',
  title: '',
  answerLayout: DEFAULT_ANSWER_LAYOUT,
};

const CONSTRAINT_TOGGLES: ReadonlyArray<{
  field: 'require_carry' | 'require_borrow' | 'exact_division' | 'allow_remainder' | 'parentheses';
  label: string;
  hint: string;
}> = [
  { field: 'require_carry', label: '必须进位', hint: '加法必须产生进位' },
  { field: 'require_borrow', label: '必须退位', hint: '减法必须产生退位' },
  { field: 'exact_division', label: '除法必须整除', hint: '与「允许有余数」互斥' },
  { field: 'allow_remainder', label: '允许有余数', hint: '与「必须整除」互斥' },
  { field: 'parentheses', label: '含括号', hint: '仅脱式题型有意义；填空题不允许' },
];

const COUNT_PRESETS = [10, 20, 30, 50] as const;

/** 生成一个 32 位正整数种子，便于老师「复现同一套题」（D19 / FR-E8）。 */
export function randomSeed(): number {
  const buffer = new Uint32Array(1);
  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    crypto.getRandomValues(buffer);
  } else {
    buffer[0] = Math.floor(Math.random() * 0xffffffff);
  }
  return buffer[0] % 1_000_000_000;
}

function toggle<T>(list: T[], value: T): T[] {
  return list.includes(value) ? list.filter((item) => item !== value) : [...list, value];
}

export interface ConfigFormProps {
  draft: ConfigDraft;
  onChange: (draft: ConfigDraft) => void;
  onSubmit: () => void;
  submitting: boolean;
  submitError: string | null;
}

export function ConfigForm({
  draft,
  onChange,
  onSubmit,
  submitting,
  submitError,
}: ConfigFormProps) {
  const [showHints, setShowHints] = useState(true);
  const { config } = draft;

  const problems = validateConfig(config);
  const seedValue = draft.seedText.trim() === '' ? null : Number(draft.seedText);
  const seedInvalid =
    draft.seedText.trim() !== '' && (!Number.isInteger(seedValue) || (seedValue ?? -1) < 0);
  if (seedInvalid) {
    problems.push('种子必须是非负整数（留空表示由后端随机）');
  }

  const patchConfig = (patch: Partial<PaperConfigIn>) =>
    onChange({ ...draft, config: { ...config, ...patch } });

  return (
    <form
      className="config-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (problems.length === 0 && !submitting) onSubmit();
      }}
    >
      <section className="dim-card" data-dimension="ops">
        <h2>
          <span className="dim-index">①</span> 运算类型
        </h2>
        <div className="chip-row">
          {OP_OPTIONS.map((option) => (
            // M3.5：改用组件层的 Checkbox（原先是手写 <label class="chip"> + <input>）。
            // class 仍是 `chip` / `chip-on`，`data-testid="op-*"` 不变 ——
            // 门禁④按 `[data-dimension="ops"] .chip` 与 `.chip-on` 断言选项与选中态，
            // 含义不变（仍是「可多选的胶囊选项」），只是状态样式改由组件层统一提供。
            <Checkbox
              key={option.value}
              testId={`op-${option.value}`}
              checked={config.ops.includes(option.value)}
              onChange={() => patchConfig({ ops: toggle<OpName>(config.ops, option.value) })}
            >
              {option.label}
            </Checkbox>
          ))}
        </div>
        {showHints ? <p className="dim-hint">至少选一种；脱式题建议选两种以上。</p> : null}
      </section>

      <section className="dim-card" data-dimension="ranges">
        <h2>
          <span className="dim-index">②</span> 数值范围（可多选）
        </h2>
        <div className="range-list">
          {RANGE_OPTIONS.map((option) => (
            <label
              key={option.value}
              className={`range-item ${config.ranges.includes(option.value) ? 'range-on' : ''}`}
            >
              <input
                type="checkbox"
                data-testid={`range-${option.value}`}
                checked={config.ranges.includes(option.value)}
                onChange={() =>
                  patchConfig({ ranges: toggle<RangeName>(config.ranges, option.value) })
                }
              />
              <span className="range-label">{option.label}</span>
              {option.hint ? <span className="range-grade">{option.hint}</span> : null}
            </label>
          ))}
        </div>
        {config.ranges.includes('R7') ? (
          <div className="r7-input">
            <label htmlFor="max-value">R7 最大不超过 N（必填，≥ {CONFIG_BOUNDS.max_value_min}）</label>
            <input
              id="max-value"
              data-testid="max-value"
              type="number"
              min={CONFIG_BOUNDS.max_value_min}
              value={config.max_value ?? ''}
              onChange={(event) =>
                patchConfig({
                  max_value: event.target.value === '' ? null : Number(event.target.value),
                })
              }
            />
          </div>
        ) : null}
        {showHints ? <p className="dim-hint">可多选：只要题目符合你勾的任意一个档位，就会被选进来。</p> : null}
      </section>

      <section className="dim-card" data-dimension="constraints">
        <h2>
          <span className="dim-index">③</span> 特殊约束开关
        </h2>
        <div className="switch-grid">
          {CONSTRAINT_TOGGLES.map((item) => (
            <label className="switch-row" key={item.field}>
              <input
                type="checkbox"
                data-testid={`constraint-${item.field}`}
                checked={config[item.field]}
                onChange={(event) => patchConfig({ [item.field]: event.target.checked })}
              />
              <span className="switch-row-label">{item.label}</span>
              <span className="switch-row-hint">{item.hint}</span>
            </label>
          ))}
        </div>
      </section>

      <section className="dim-card" data-dimension="question_type">
        <h2>
          <span className="dim-index">④</span> 题型
        </h2>
        <div className="chip-row">
          {QUESTION_TYPE_OPTIONS.map((option) => (
            <label
              key={option.value}
              className={`chip ${config.question_type === option.value ? 'chip-on' : ''}`}
            >
              <input
                type="radio"
                name="question-type"
                data-testid={`question-type-${option.value}`}
                checked={config.question_type === option.value}
                onChange={() =>
                  patchConfig({ question_type: option.value as QuestionTypeName })
                }
              />
              {option.label}
            </label>
          ))}
        </div>
      </section>

      <section className="dim-card" data-dimension="count">
        <h2>
          <span className="dim-index">⑤</span> 题量
        </h2>
        <div className="count-row">
          {/* M3.5：改用组件层的 NumberInput —— 它自带 min/max 校验与 **invalid 文字原因**
              （§4：`invalid` 必须同时给出文字原因，不能只变红）。
              `data-testid="count"` 与 min/max 属性都保留：门禁④按它们断言
              「默认 20、范围 1..100 与契约一致」。 */}
          <NumberInput
            id="count"
            testId="count"
            unit="题"
            value={config.count}
            min={CONFIG_BOUNDS.count[0]}
            max={CONFIG_BOUNDS.count[1]}
            onValueChange={(next) => patchConfig({ count: next === '' ? 0 : next })}
          />
          <div className="chip-row">
            {COUNT_PRESETS.map((preset) => (
              <button
                type="button"
                key={preset}
                className={`chip ${config.count === preset ? 'chip-on' : ''}`}
                onClick={() => patchConfig({ count: preset })}
              >
                {preset} 题
              </button>
            ))}
          </div>
        </div>
        {showHints ? (
          <p className="dim-hint">
            取值范围 {CONFIG_BOUNDS.count[0]}..{CONFIG_BOUNDS.count[1]}（契约 PaperConfigIn.count）。
          </p>
        ) : null}
      </section>

      <section className="dim-card" data-dimension="answer_form">
        <h2>
          <span className="dim-index">⑥</span> 答案版形式
        </h2>
        <AnswerLayoutEditor
          value={draft.answerLayout}
          onChange={(next) => onChange({ ...draft, answerLayout: next })}
          idPrefix="config-answer-layout"
        />
        <p className="dim-hint" data-testid="answer-layout-note">
          四项开关会跟着「生成」一起存进这套卷子；生成后还可以在卷页继续调整。
        </p>
      </section>

      <section className="dim-card" data-dimension="meta">
        <h2>
          <span className="dim-index">＋</span> 卷名与随机种子
        </h2>
        <div className="meta-grid">
          <label className="meta-field">
            <span>卷名（可留空，后端给默认名）</span>
            <input
              type="text"
              data-testid="title"
              value={draft.title}
              placeholder="例：三位数乘两位数 随堂练"
              onChange={(event) => onChange({ ...draft, title: event.target.value })}
            />
          </label>
          <label className="meta-field">
            <span>种子（填同一个种子、同一套配置，就得到同一套题）</span>
            <span className="seed-row">
              <input
                type="text"
                inputMode="numeric"
                data-testid="seed"
                value={draft.seedText}
                placeholder="留空 → 后端随机"
                onChange={(event) => onChange({ ...draft, seedText: event.target.value })}
              />
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => onChange({ ...draft, seedText: String(randomSeed()) })}
              >
                随机一个
              </button>
            </span>
          </label>
        </div>
      </section>

      <div className="submit-bar">
        <label className="hint-toggle">
          <input
            type="checkbox"
            checked={showHints}
            onChange={(event) => setShowHints(event.target.checked)}
          />
          显示填写提示
        </label>
        <button type="submit" className="btn btn-primary" disabled={submitting || problems.length > 0}>
          {submitting ? '正在生成…' : `生成 ${config.count} 道题`}
        </button>
      </div>

      {problems.length > 0 ? (
        <ul className="problem-list" data-testid="config-problems">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      ) : (
        <p className="dim-ok" data-testid="config-ok">
          配置看起来合法，可以生成。
        </p>
      )}

      {submitError ? (
        <p className="form-error" role="alert" data-testid="submit-error">
          {submitError}
        </p>
      ) : null}
    </form>
  );
}
