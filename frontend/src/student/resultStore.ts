/**
 * 结果页的数据来源。
 *
 * 契约里**没有**「学生按 attempt_id 取自己的判分结果」这个接口（`submit_attempt`
 * 是老师端查明细用的），所以判分结果只能在交卷那一刻由前端带着走到结果页。
 * 用 `sessionStorage` 而不是仅用路由 state：学生交完卷刷新一下页面就白屏是不可接受的。
 * 用 `sessionStorage` 而不是 `localStorage`：一次作答的结果不该在下次打开时还留着。
 */
import type { AnswerEntry, AttemptResult, StudentQuestion } from './api';

export interface StoredResult {
  /** 卷子标题或「自主练习」 */
  title: string;
  source: 'paper' | 'practice';
  /** 做卷子时的卷号，用于「再做一遍」；自主练习为 null */
  paperCode: string | null;
  result: AttemptResult;
  questions: StudentQuestion[];
  answers: AnswerEntry[];
}

const STORAGE_KEY = 'mathgen.student.lastResult';

/**
 * 把任意来源（sessionStorage 的 JSON、路由 state）的值收成一个 `StoredResult`。
 *
 * 存不下的时候（微信内置浏览器、隐私模式）结果页还能从路由 state 拿到，
 * 所以这条归一化要能被两处复用，而不是各写一遍。
 */
export function asStoredResult(value: unknown): StoredResult | null {
  if (typeof value !== 'object' || value === null) return null;
  const parsed = value as Partial<StoredResult>;
  if (!parsed.result || !Array.isArray(parsed.result.items)) return null;
  // D80：得分只能来自后端。升级前交的卷存在会话里的结果没有 `score`，
  // 这里**不补算** —— 补算就等于把旧口径又留了一份，两条路会各自漂移。
  // 返回 null 让结果页走「这里暂时没有结果」的说明，而不是渲染出 `undefined`。
  if (typeof parsed.result.score !== 'number') return null;
  return {
    title: typeof parsed.title === 'string' ? parsed.title : '练习结果',
    source: parsed.source === 'paper' ? 'paper' : 'practice',
    paperCode: typeof parsed.paperCode === 'string' ? parsed.paperCode : null,
    result: parsed.result,
    questions: Array.isArray(parsed.questions) ? parsed.questions : [],
    answers: Array.isArray(parsed.answers) ? parsed.answers : [],
  };
}

export function saveResult(value: StoredResult): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  } catch {
    // 存不下不影响本次跳转：结果页还能从路由 state 拿到（见 ResultPage）
  }
}

export function readResult(): StoredResult | null {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    return asStoredResult(JSON.parse(raw));
  } catch {
    return null;
  }
}
