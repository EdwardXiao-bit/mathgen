/** TanStack Query 的 key 工厂：集中管理，避免各处手写字符串数组不匹配。 */

import type { PaperConfigIn, PaperListQuery } from './api';

export const queryKeys = {
  /** 卷相关的**前缀**：写操作后用它一次性失效「卷库列表 + 健康探测 + 各卷详情」 */
  papersRoot: ['papers'] as const,
  /** 后端健康探测（与卷库分页查询分开缓存键，避免互相覆盖） */
  health: ['papers', 'health'] as const,
  /** 卷库列表：带上分页/搜索/筛选，任何一项变化都算不同查询 */
  papers: (query: PaperListQuery) =>
    ['papers', 'list', query.limit, query.offset, query.q ?? '', query.status ?? 'all'] as const,
  paper: (paperId: number) => ['paper', paperId] as const,
  paperByCode: (code: string) => ['paper', 'by-code', code] as const,
  practice: (config: PaperConfigIn, seed: number | null) =>
    ['practice-preview', seed, config] as const,
};
