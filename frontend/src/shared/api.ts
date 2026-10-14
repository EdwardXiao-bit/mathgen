/**
 * 老师端 REST 出入参类型 —— 与后端冻结契约 **逐字段对应**。
 *
 * 唯一来源：`backend/app/contracts/api.py`（Pydantic 模型）。
 * **JSON 字段一律 snake_case**，前端不做驼峰映射——映射层是多余的错误来源。
 * 路径常量来自 `ROUTES`，同样不在这里另写字符串字面量。
 *
 * 改动纪律：后端 `api.py` 一动，本文件必须同步；本文件不做「顺手优化」。
 */

// ---------------------------------------------------------------------------
// 路径常量：镜像 backend/app/contracts/api.py 的 ROUTES
// ---------------------------------------------------------------------------

/** 与后端 `ROUTES` 一一对应；调用方只允许引用这里，禁止手写路径字符串。 */
export const ROUTES = {
  create_paper: '/api/papers',
  list_papers: '/api/papers',
  get_paper: '/api/papers/{paper_id}',
  delete_item: '/api/papers/{paper_id}/items/{seq}',
  regenerate_item: '/api/papers/{paper_id}/items/{seq}/regenerate',
  update_layout: '/api/papers/{paper_id}/layout',
  publish_paper: '/api/papers/{paper_id}/publish',
  // D53 新收录：已发布卷只读，改题走「另存为可编辑副本」（契约 §6 状态机）
  copy_paper: '/api/papers/{paper_id}/copy',
  sheet: '/api/papers/{paper_id}/sheet',
  export: '/api/papers/{paper_id}/export',
  qr: '/api/papers/{paper_id}/qr',
} as const;

export type RouteName = keyof typeof ROUTES;

/** 把 `{paper_id}` / `{seq}` 占位符替换成实际值（其余查询串由调用方拼）。 */
export function route(name: RouteName, params: Record<string, string | number> = {}): string {
  return ROUTES[name].replace(/\{(\w+)\}/g, (_match, key: string) => {
    const value = params[key];
    if (value === undefined) {
      throw new Error(`路由 ${name} 缺少参数 ${key}`);
    }
    return encodeURIComponent(String(value));
  });
}

// ---------------------------------------------------------------------------
// 冻结枚举（api.py 的 Literal 别名）
// ---------------------------------------------------------------------------

export type PaperStatus = 'draft' | 'published';
export type QuestionTypeName = 'oral' | 'stepwise' | 'fill';
export type RangeName = 'R1' | 'R2' | 'R3' | 'R4' | 'R5' | 'R6' | 'R7';
export type OpName = '+' | '-' | '×' | '÷';
/** 卷面模式：对应 `backend/app/contracts/sheet.py` 的 `SheetMode`（`GET /sheet?mode=`）。 */
export type SheetMode = 'question' | 'answer';

// ---------------------------------------------------------------------------
// 模型（api.py 的 Pydantic 类）；字段名/可空性与后端逐条对齐
// ---------------------------------------------------------------------------

/** 对应 `PaperConfigIn`。 */
export interface PaperConfigIn {
  ops: OpName[];
  ranges: RangeName[];
  question_type: QuestionTypeName;
  max_value: number | null;
  require_carry: boolean;
  require_borrow: boolean;
  exact_division: boolean;
  allow_remainder: boolean;
  parentheses: boolean;
  count: number;
}

/** 对应 `LayoutIn`；六个字段与 `app/contracts/layout.py` 的 LayoutParams 同名。 */
export interface LayoutIn {
  cols_per_row: number;
  row_gap: number;
  font_size: number;
  page_margin: number;
  answer_line: boolean;
  header_bar: boolean;
}

/** 对应 `PaperItemOut`（老师端**包含** answer / steps，学生端必须剔除）。 */
export interface PaperItemOut {
  seq: number;
  display: string;
  expr: string;
  answer: string;
  difficulty: number;
  tags: string[];
  steps: string[];
  origin: string;
}

/** 对应 `PaperDetail`。 */
export interface PaperDetail {
  id: number;
  code: string | null;
  title: string;
  status: PaperStatus;
  seed: number;
  config: PaperConfigIn;
  layout: LayoutIn;
  created_at: string;
  published_at: string | null;
  items: PaperItemOut[];
}

/** 对应 `PaperSummary`。 */
export interface PaperSummary {
  id: number;
  code: string | null;
  title: string;
  status: PaperStatus;
  item_count: number;
  created_at: string;
  published_at: string | null;
}

/** 对应 `PaperListOut`。 */
export interface PaperListOut {
  total: number;
  papers: PaperSummary[];
}

/** 对应 `PublishResult`。 */
export interface PublishResult {
  id: number;
  code: string;
  qr_url: string;
  student_url: string;
}

/** 对应 `ErrorBody`。 */
export interface ErrorBody {
  code: string;
  message: string;
}

/** 对应 `ErrorResponse`；错误响应统一 `{"error": {...}}`。 */
export interface ErrorResponse {
  error: ErrorBody;
}

// ---------------------------------------------------------------------------
// 六个排版参数的类型化元数据：控件直接由这里生成，界面与契约不会漂移
// ---------------------------------------------------------------------------

export type LayoutNumberField = 'cols_per_row' | 'row_gap' | 'font_size' | 'page_margin';
export type LayoutBoolField = 'answer_line' | 'header_bar';
export type LayoutField = LayoutNumberField | LayoutBoolField;

/**
 * CSS 变量名 —— 与 `app/contracts/layout.py` 的 `CSS_VAR_NAMES` **逐字相同**（冻结）。
 * 预览区必须用这些变量，才能和浏览器打印、PDF 导出共用一套样式（C3 / NFR-5）。
 *
 * **D59 变更**：两个布尔项原来叫 `--answer-line: 1|0` / `--header-bar: 1|0`，
 * 但渲染器样式表里**没有任何规则读取它们**（隐藏逻辑走渲染期烘死的 `data-*` 属性），
 * 于是面板改这两个开关时预览毫无反应。现改为输出 **CSS `display` 关键字**：
 * `--answer-line-display: block|none`、`--header-bar-display: grid|none`，
 * 样式表用 `display: var(--…-display, 默认值)` 直接消费 ——
 * 服务端渲染与面板实时覆盖因此走同一条通路。
 */
export const CSS_VAR_NAMES: Record<LayoutField, string> = {
  cols_per_row: '--cols-per-row',
  row_gap: '--row-gap',
  font_size: '--font-size',
  page_margin: '--page-margin',
  answer_line: '--answer-line-display',
  header_bar: '--header-bar-display',
} as const;

/** 布尔项的 CSS 取值（与后端 `css_variables()` 一致）：**display 关键字**，不是 1/0。 */
export const CSS_DISPLAY_VALUES = {
  answer_line: { on: 'block', off: 'none' },
  header_bar: { on: 'grid', off: 'none' },
} as const;

/** 取值边界 —— 与 `app/contracts/layout.py` 的 `BOUNDS` 相同（越界后端会 400/422）。 */
export const LAYOUT_BOUNDS: Record<LayoutNumberField, readonly [number, number]> = {
  cols_per_row: [2, 6],
  row_gap: [0.8, 2.0],
  font_size: [10, 20],
  page_margin: [1.0, 3.0],
} as const;

export const LAYOUT_DEFAULTS: LayoutIn = {
  cols_per_row: 4,
  row_gap: 1.0,
  font_size: 14,
  page_margin: 2.0,
  answer_line: true,
  header_bar: true,
};

/** 把排版参数转成 CSS 变量 —— 与后端 `css_variables()` 的输出格式**逐字相同**。
 *
 * 数值项带单位（`4` / `1` / `14pt` / `2cm`）；布尔项输出 CSS `display` 关键字（D59）。
 */
export function cssVariables(layout: LayoutIn): Record<string, string> {
  return {
    [CSS_VAR_NAMES.cols_per_row]: String(layout.cols_per_row),
    [CSS_VAR_NAMES.row_gap]: String(layout.row_gap),
    [CSS_VAR_NAMES.font_size]: `${layout.font_size}pt`,
    [CSS_VAR_NAMES.page_margin]: `${layout.page_margin}cm`,
    [CSS_VAR_NAMES.answer_line]: layout.answer_line
      ? CSS_DISPLAY_VALUES.answer_line.on
      : CSS_DISPLAY_VALUES.answer_line.off,
    [CSS_VAR_NAMES.header_bar]: layout.header_bar
      ? CSS_DISPLAY_VALUES.header_bar.on
      : CSS_DISPLAY_VALUES.header_bar.off,
  };
}

/** 前端本地夹紧，避免把越界值发给后端（后端仍会独立校验）。 */
export function clampLayout(layout: LayoutIn): LayoutIn {
  const clamp = (v: number, [lo, hi]: readonly [number, number]) =>
    Math.min(hi, Math.max(lo, v));
  return {
    cols_per_row: Math.round(clamp(layout.cols_per_row, LAYOUT_BOUNDS.cols_per_row)),
    row_gap: Number(clamp(layout.row_gap, LAYOUT_BOUNDS.row_gap).toFixed(1)),
    font_size: Math.round(clamp(layout.font_size, LAYOUT_BOUNDS.font_size)),
    page_margin: Number(clamp(layout.page_margin, LAYOUT_BOUNDS.page_margin).toFixed(1)),
    answer_line: layout.answer_line,
    header_bar: layout.header_bar,
  };
}

// ---------------------------------------------------------------------------
// 出题配置的界面元数据（选项与 D16 档位表 / D17 题型逐条对应）
// ---------------------------------------------------------------------------

export interface OptionMeta<T> {
  value: T;
  label: string;
  hint?: string;
}

export const OP_OPTIONS: ReadonlyArray<OptionMeta<OpName>> = [
  { value: '+', label: '加法 +' },
  { value: '-', label: '减法 −' },
  { value: '×', label: '乘法 ×' },
  { value: '÷', label: '除法 ÷' },
];

/** 数值范围档位 —— 与 `docs/00-决策记录.md` D16《数值范围档位表》逐条对应。 */
export const RANGE_OPTIONS: ReadonlyArray<OptionMeta<RangeName>> = [
  { value: 'R1', label: 'R1 · 20 以内加减法', hint: '一年级' },
  { value: 'R2', label: 'R2 · 100 以内加减法（含进位 / 退位）', hint: '一~二年级' },
  { value: 'R3', label: 'R3 · 表内乘除法（九九表范围内）', hint: '二~三年级' },
  { value: 'R4', label: 'R4 · 两位数 × 一位数、两位数 ÷ 一位数', hint: '三~四年级' },
  { value: 'R5', label: 'R5 · 三位数 × 两位数、三位数 ÷ 两位数', hint: '四年级以上' },
  { value: 'R6', label: 'R6 · 四位数及以上多位数加减', hint: '四年级以上' },
  { value: 'R7', label: 'R7 · 老师自定义上限（最大不超过 N）', hint: '分层教学用' },
];

/** 题型 —— 与 D17「口算横式 / 脱式（递等式）/ 填空求未知数」对应。 */
export const QUESTION_TYPE_OPTIONS: ReadonlyArray<OptionMeta<QuestionTypeName>> = [
  { value: 'oral', label: '口算横式' },
  { value: 'stepwise', label: '脱式（递等式）' },
  { value: 'fill', label: '填空求未知数' },
];

/** 答案版形式 —— FR-T6「题目版与答案版分别打印」的用户可见开关。 */
export type AnswerForm = 'separate_page' | 'same_page' | 'none';

export const ANSWER_FORM_OPTIONS: ReadonlyArray<OptionMeta<AnswerForm>> = [
  { value: 'separate_page', label: '答案版单独打印（默认，D20）' },
  { value: 'same_page', label: '答案版与题目版同屏对照' },
  { value: 'none', label: '只要题目版' },
];

export const CONFIG_BOUNDS = {
  count: [1, 100] as const,
  max_value_min: 10,
} as const;

const OP_VALUES = OP_OPTIONS.map((o) => o.value);
const RANGE_VALUES = RANGE_OPTIONS.map((o) => o.value);
const QUESTION_TYPE_VALUES = QUESTION_TYPE_OPTIONS.map((o) => o.value);

export function isOpName(v: string): v is OpName {
  return (OP_VALUES as string[]).includes(v);
}
export function isRangeName(v: string): v is RangeName {
  return (RANGE_VALUES as string[]).includes(v);
}
export function isQuestionTypeName(v: string): v is QuestionTypeName {
  return (QUESTION_TYPE_VALUES as string[]).includes(v);
}

/**
 * 校验出题参数是否满足契约 §2.1 的约束规则（**前端只做提示，不做权威判定**；
 * 后端仍会独立校验并返回 `CONFIG_INVALID`）。
 * 返回人类可读的问题列表，空数组表示前端看不出问题。
 */
export function validateConfig(config: PaperConfigIn): string[] {
  const problems: string[] = [];
  if (config.ops.length === 0) {
    problems.push('运算类型至少选一种');
  }
  if (config.ranges.length === 0) {
    problems.push('数值范围至少选一个档位');
  }
  if (config.exact_division && config.allow_remainder) {
    problems.push('「除法必须整除」与「允许有余数」互斥，只能选一个');
  }
  if (config.ranges.includes('R7')) {
    if (config.max_value === null || Number.isNaN(config.max_value)) {
      problems.push('选了 R7 就必须填「最大不超过 N」');
    } else if (config.max_value < CONFIG_BOUNDS.max_value_min) {
      problems.push(`R7 的自定义上限必须 ≥ ${CONFIG_BOUNDS.max_value_min}`);
    }
  }
  if (config.count < CONFIG_BOUNDS.count[0] || config.count > CONFIG_BOUNDS.count[1]) {
    problems.push(`题量必须在 ${CONFIG_BOUNDS.count[0]}..${CONFIG_BOUNDS.count[1]} 之间`);
  }
  if (config.question_type === 'stepwise') {
    if (config.ops.length < 2 && !config.parentheses) {
      problems.push('脱式（递等式）至少需要两种运算，或勾选「含括号」');
    }
  }
  if (config.question_type === 'fill' && config.parentheses) {
    problems.push('填空题不允许「含括号」（V1 简化）');
  }
  return problems;
}

/** 六维表单的初始值：最贴近「场景 1 随堂练」的默认。 */
export const DEFAULT_CONFIG: PaperConfigIn = {
  ops: ['×'],
  ranges: ['R5'],
  question_type: 'oral',
  max_value: null,
  require_carry: true,
  require_borrow: false,
  exact_division: false,
  allow_remainder: false,
  parentheses: false,
  count: 20,
};
