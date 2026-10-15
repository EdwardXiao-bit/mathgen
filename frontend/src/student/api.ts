/**
 * 学生端 REST 客户端 —— 形状照 `docs/04-接口契约.md` §5.2（不凭记忆）。
 *
 * 为什么不复用老师端的 `src/shared/apiClient.ts`：
 * 那份客户端的类型（卷库 / 排版 / 导出）与错误文案都是围绕老师工作台写的。
 * 学生端只用四个接口，而且失败提示必须换成「学生拿着手机能执行的下一步」
 * （例如「确认手机和老师的电脑在同一个网络」而不是「连接不上后端」）。
 * 两边各自照着同一份契约写，字段名不会漂移。
 *
 * 路径一律走 `shared/api` 的 `route()`：路径字面量散落在界面代码里，改契约时会漏改
 * （这条判据同时管老师端和学生端，不许有例外）。
 */

import { route } from '../shared/api';

// ---------------------------------------------------------------------------
// 类型：与契约 §5.2 的响应字段一一对应
// ---------------------------------------------------------------------------

/** 学生端取题响应里的题目对象。 */
export interface StudentQuestion {
  seq: number;
  display: string;
  question_type: string;
}

/** 按卷号取卷（`student_paper`）的响应。 */
export interface StudentPaper {
  paper_id: number;
  title: string;
  total: number;
  items: StudentQuestion[];
}

/** 自主练习的出题参数：`GenerateConfig` 的字段**平铺**（契约 §5.1 D55，不包一层）。 */
export interface PracticeConfig {
  ops: string[];
  ranges: string[];
  question_type: string;
  max_value: number | null;
  require_carry: boolean;
  require_borrow: boolean;
  exact_division: boolean;
  allow_remainder: boolean;
  parentheses: boolean;
  count: number;
}

/** 自主练习出题（`student_practice`）的响应：`seed` 必须原样回传给提交接口。 */
export interface PracticePaper {
  seed: number;
  total: number;
  items: StudentQuestion[];
}

/** 判分结果里的一题：`{seq, display, question_type, answer, student_answer, is_correct}`。 */
export interface AttemptResultItem {
  seq: number;
  /** 判分响应也会回题面，结果页优先用它（刷新后仍能显示题目） */
  display: string | null;
  is_correct: boolean;
  /** 正确答案，仅用于结果页对照展示 */
  answer: string | null;
  student_answer: string | null;
}

/** 交卷判分（`submit_attempt`）的响应。 */
export interface AttemptResult {
  attempt_id: number | null;
  total: number;
  correct: number;
  /**
   * 得分（0..100 整数），**唯一来源是后端**（D80）。
   *
   * M5 前结果页自己用 `Math.round(correct / total * 100)` 算，与契约的
   * `score_of(total, correct)` 是两套口径：`(40, 23)` 精确值 57.5，旧口径给 57、
   * 契约给 58。两套算法并存就会出现「同一份成绩两个分数」而且**不会自己报错**，
   * 所以这里只接受后端给的值，缺了就报错（见 `submitAttempt`）。
   */
  score: number;
  items: AttemptResultItem[];
}

/** 一条作答：未作答给 `null`（后端按答错计，契约 §5.2）。 */
export interface AnswerEntry {
  seq: number;
  answer: string | null;
}

/** 提交作答的两种模式：`paper_id` 与「`config` + `seed`」**恰好给一组**（契约 §5.2）。 */
export type AttemptTarget =
  | { kind: 'paper'; paperId: number }
  | { kind: 'practice'; config: PracticeConfig; seed: number };

export interface SubmitAttemptInput {
  studentId: number;
  attemptUuid: string;
  answers: AnswerEntry[];
  durationMs: number;
  target: AttemptTarget;
}

// ---------------------------------------------------------------------------
// 错误：只带契约 §9 的错误码与后端原文；给用户看的文案由 describeError 决定
// ---------------------------------------------------------------------------

export class StudentApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = 'StudentApiError';
    this.code = code;
    this.status = status;
  }
}

const TIMEOUT_MS = 20_000;

function readErrorBody(value: unknown): { code: string; message: string } | null {
  if (typeof value !== 'object' || value === null) return null;
  const box = (value as { error?: unknown }).error;
  if (typeof box !== 'object' || box === null) return null;
  const { code, message } = box as { code?: unknown; message?: unknown };
  if (typeof code !== 'string' || typeof message !== 'string') return null;
  return { code, message };
}

/**
 * 发一次请求并解析 JSON。
 *
 * 超时用 `AbortController`：智能手机在教室角落里信号差时，宁可 20 秒后给出
 * 「没连上老师电脑」的可读提示，也不要让学生对着转圈等下去。
 */
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), TIMEOUT_MS);
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      signal: controller.signal,
      headers: {
        Accept: 'application/json',
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      },
    });
  } catch {
    // 网络层失败（后端没起 / 不在同一网络 / 超时）：统一成一个可读的错误码
    throw new StudentApiError('NETWORK_UNREACHABLE', '网络请求没有到达老师电脑', 0);
  } finally {
    window.clearTimeout(timer);
  }

  const text = await response.text();
  let parsed: unknown = null;
  if (text.length > 0) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
  }

  if (!response.ok) {
    const body = readErrorBody(parsed);
    throw new StudentApiError(
      body?.code ?? `HTTP_${response.status}`,
      body?.message ?? '老师电脑上的程序没有正常应答',
      response.status,
    );
  }

  return parsed as T;
}

/** 把取题接口的 `items` 归一成 `StudentQuestion[]`；缺题面直接报错而不是渲染空白。 */
function readQuestions(value: unknown): StudentQuestion[] {
  if (!Array.isArray(value)) {
    throw new StudentApiError('BAD_RESPONSE', '取题响应缺少题目列表', 200);
  }
  return value.map((raw, index) => {
    const item = (raw ?? {}) as Record<string, unknown>;
    return {
      seq: typeof item.seq === 'number' ? item.seq : index + 1,
      display: typeof item.display === 'string' ? item.display : '',
      question_type: typeof item.question_type === 'string' ? item.question_type : 'oral',
    };
  });
}

// ---------------------------------------------------------------------------
// 四个接口
// ---------------------------------------------------------------------------

/** 报到（`create_student`）：姓名 + 学号**均必填**（D41），返回学生编号。 */
export async function registerStudent(name: string, studentNo: string): Promise<number> {
  const payload = await request<unknown>(route('create_student'), {
    method: 'POST',
    body: JSON.stringify({ name, student_no: studentNo }),
  });
  const value = (payload ?? {}) as Record<string, unknown>;
  if (typeof value.student_id !== 'number') {
    throw new StudentApiError('BAD_RESPONSE', '报到响应缺少学生编号', 200);
  }
  return value.student_id;
}

/** 按卷号取老师发的那份卷子（`student_paper`，参数名是卷号 `code`）。 */
export async function loadPaper(code: string): Promise<StudentPaper> {
  const payload = await request<unknown>(route('student_paper', { code }));
  const value = (payload ?? {}) as Record<string, unknown>;
  if (typeof value.paper_id !== 'number') {
    throw new StudentApiError('BAD_RESPONSE', '取卷响应缺少卷编号', 200);
  }
  const items = readQuestions(value.items);
  return {
    paper_id: value.paper_id,
    title: typeof value.title === 'string' && value.title.trim() !== '' ? value.title : '练习卷',
    total: typeof value.total === 'number' ? value.total : items.length,
    items,
  };
}

/** 自主练习出题（`student_practice`），拿回 `seed` 与题目。 */
export async function startPractice(config: PracticeConfig): Promise<PracticePaper> {
  const payload = await request<unknown>(route('student_practice'), {
    method: 'POST',
    body: JSON.stringify(config),
  });
  const value = (payload ?? {}) as Record<string, unknown>;
  if (typeof value.seed !== 'number') {
    throw new StudentApiError('BAD_RESPONSE', '练习出题响应缺少题号种子', 200);
  }
  const items = readQuestions(value.items);
  return {
    seed: value.seed,
    total: typeof value.total === 'number' ? value.total : items.length,
    items,
  };
}

/** 交卷判分（`submit_attempt`）。`attempt_uuid` 幂等（D76）：重复提交返回首次结果。 */
export async function submitAttempt(input: SubmitAttemptInput): Promise<AttemptResult> {
  const body: Record<string, unknown> = {
    student_id: input.studentId,
    attempt_uuid: input.attemptUuid,
    answers: input.answers,
    duration_ms: input.durationMs,
  };
  if (input.target.kind === 'paper') {
    body.paper_id = input.target.paperId;
  } else {
    body.config = input.target.config;
    body.seed = input.target.seed;
  }

  const payload = await request<unknown>(route('submit_attempt'), {
    method: 'POST',
    body: JSON.stringify(body),
  });
  const value = (payload ?? {}) as Record<string, unknown>;
  if (!Array.isArray(value.items)) {
    throw new StudentApiError('BAD_RESPONSE', '判分响应缺少题目结果', 200);
  }
  const items: AttemptResultItem[] = value.items.map((raw, index) => {
    const item = (raw ?? {}) as Record<string, unknown>;
    const answer =
      typeof item.answer === 'string'
        ? item.answer
        : typeof item.correct_answer === 'string'
          ? item.correct_answer
          : null;
    return {
      seq: typeof item.seq === 'number' ? item.seq : index + 1,
      display: typeof item.display === 'string' ? item.display : null,
      is_correct: item.is_correct === true,
      answer,
      student_answer: typeof item.student_answer === 'string' ? item.student_answer : null,
    };
  });
  return {
    attempt_id: typeof value.attempt_id === 'number' ? value.attempt_id : null,
    total: typeof value.total === 'number' ? value.total : items.length,
    correct: typeof value.correct === 'number' ? value.correct : items.filter((i) => i.is_correct).length,
    // D80：得分**不在这里算**。旧实现在结果页用 `correct / total` 复算了一遍，
    // 那是一套独立的口径；缺字段时宁可报错，也不要悄悄换回旧算法。
    score: readScore(value.score),
    items,
  };
}

function readScore(raw: unknown): number {
  if (typeof raw !== 'number' || !Number.isFinite(raw)) {
    throw new StudentApiError(
      'BAD_RESPONSE',
      '判分响应缺少得分（score）：得分只能由后端给出（D80），前端不会自己算一遍',
      200,
    );
  }
  return raw;
}

/** 每次进入答题生成一个新的 UUID；**同一次提交重试必须复用同一个**（D76 幂等）。 */
export function newAttemptUuid(): string {
  const webCrypto = globalThis.crypto;
  if (webCrypto && typeof webCrypto.randomUUID === 'function') return webCrypto.randomUUID();
  return `attempt-${Date.now()}-${Math.floor(Math.random() * 1_000_000_000)}`;
}

// ---------------------------------------------------------------------------
// 错误 → 人话 + 下一步
// ---------------------------------------------------------------------------

export interface FriendlyError {
  title: string;
  hint: string;
}

const FALLBACK: FriendlyError = {
  title: '这一步没做成',
  hint: '请重试；如果一直不行，把手机上的提示告诉老师。',
};

const BY_CODE: Record<string, FriendlyError> = {
  NETWORK_UNREACHABLE: {
    title: '没连上老师电脑',
    hint: '请确认手机和老师电脑连的是同一个网络，然后重试。',
  },
  BAD_RESPONSE: {
    title: '题目没有完整拿到',
    hint: '请退回首页重新进入；如果还是不行，告诉老师。',
  },
  CODE_NOT_FOUND: {
    title: '没找到这个卷号',
    hint: '请核对卷号有没有输错，或直接扫老师卷子上的二维码。',
  },
  PAPER_NOT_FOUND: {
    title: '这份卷子找不到了',
    hint: '请找老师确认，或者重新扫一遍卷子上的二维码。',
  },
  PAPER_NOT_PUBLISHED: {
    title: '这份卷子还没发',
    hint: '请找老师确认发卷之后再扫一次。',
  },
  VALIDATION_ERROR: {
    title: '填的信息不完整',
    hint: '姓名和学号都要填，学号请照老师给的写。',
  },
  INSUFFICIENT_QUESTIONS: {
    title: '题量太大了',
    hint: '这套练习出不了这么多题，把题量调小一点再试。',
  },
  CONFIG_INVALID: {
    title: '练习设置没有被接受',
    hint: '把题量调小一点再试。',
  },
};

/** 把任何错误变成「发生了什么 + 怎么办」两句话，界面上从不再出现技术原文。 */
export function describeError(error: unknown): FriendlyError {
  if (error instanceof StudentApiError) {
    const known = BY_CODE[error.code];
    if (known) return known;
    if (error.status >= 500) {
      return { title: '老师电脑上的程序出错了', hint: '请告诉老师，然后稍后重试。' };
    }
  }
  return FALLBACK;
}
