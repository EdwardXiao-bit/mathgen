/** TanStack Query 的 key 工厂：集中管理，避免各处手写字符串数组不匹配。 */

import type { AttemptListQuery, PaperConfigIn, PaperListQuery } from './api';

export const queryKeys = {
  /** 卷相关的**前缀**：写操作后用它一次性失效「卷库列表 + 健康探测 + 各卷详情」 */
  papersRoot: ['papers'] as const,
  /** 后端健康探测（与卷库分页查询分开缓存键，避免互相覆盖） */
  health: ['papers', 'health'] as const,
  /**
   * 卷库列表：带上分页/搜索/筛选，任何一项变化都算不同查询。
   *
   * **`archived` 必须进缓存键**（D78）：不进的话「在用 / 已归档」两档会共用同一个
   * 缓存条目，切档时 TanStack Query 直接把上一档的数据当成本档的数据渲染 ——
   * 界面会声称「已归档」却列出在用卷，且**不会报错**（D59 不许谎报状态）。
   * 缺省写成 `'exclude'`，与后端默认值对齐。
   */
  papers: (query: PaperListQuery) =>
    [
      'papers',
      'list',
      query.limit,
      query.offset,
      query.q ?? '',
      query.status ?? 'all',
      query.archived ?? 'exclude',
    ] as const,
  paper: (paperId: number) => ['paper', paperId] as const,
  paperByCode: (code: string) => ['paper', 'by-code', code] as const,
  practice: (config: PaperConfigIn, seed: number | null) =>
    ['practice-preview', seed, config] as const,
  /** 成绩（M5）的**前缀**：与卷库分开，删卷/归档不会误伤成绩缓存 */
  attemptsRoot: ['attempts'] as const,
  /**
   * 成绩列表：分页与**两项筛选**（`paper_id` / `q`）都必须进缓存键。
   *
   * 与卷库列表同一个理由（D59）：不进键，切筛选时 TanStack Query 会把上一档的数据
   * 当成本档渲染 —— 界面会声称「只看《…》的成绩」却列着别套卷的行，且**不会报错**。
   * 缺省写成 `0` / `''`（不是 `undefined`）是为了让「没筛选」只有一个稳定的键形态。
   */
  attempts: (query: AttemptListQuery) =>
    [
      'attempts',
      'list',
      query.limit,
      query.offset,
      query.paper_id ?? 0,
      query.q ?? '',
    ] as const,
  attempt: (attemptId: number) => ['attempt', attemptId] as const,
};
