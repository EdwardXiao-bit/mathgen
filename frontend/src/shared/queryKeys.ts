/** TanStack Query 的 key 工厂：集中管理，避免各处手写字符串数组不匹配。 */

import type { PaperConfigIn } from './api';

export const queryKeys = {
  papers: ['papers'] as const,
  paper: (paperId: number) => ['paper', paperId] as const,
  practice: (config: PaperConfigIn, seed: number | null) =>
    ['practice-preview', seed, config] as const,
};
