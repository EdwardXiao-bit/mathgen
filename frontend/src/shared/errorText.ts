/**
 * 把任意错误对象转成**老师能看懂的一句话**。
 *
 * 为什么抽成共享模块：这条规则（「人话 + 保留错误码」）原先在三个页面里各写了一遍，
 * 于是很容易只改一处、其余两处继续把服务端原文怼给老师。
 * 用户反馈里明确点了「界面里的面向开发者文本必须清掉」（D70），
 * 所以把它收敛成唯一实现，页面只管调用。
 *
 * 口径：
 * - `ApiError` → `错误码：可读消息`（错误码单列在这里，便于排错；
 *   界面上的其它位置不得再散落错误码）。
 * - 其它 `Error` → 直接用 `message`。
 * - 兜底 `String(error)`，但绝不返回 `undefined` / `[object Object]` 这类对老师无意义的文本。
 */

import { ApiError } from './apiClient';

export function readError(error: unknown): string {
  if (error instanceof ApiError) return `${error.code}：${error.message}`;
  if (error instanceof Error) return error.message;
  const text = String(error);
  return text === '[object Object]' || text === 'undefined' ? '发生了未知错误' : text;
}
