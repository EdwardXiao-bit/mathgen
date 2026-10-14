/** 后端可用性探测：让「后端未启动」在界面上是一句人话，而不是白屏或哑转圈。 */

import { useQuery } from '@tanstack/react-query';

import { ApiError, teacherApi } from '../shared/apiClient';
import { queryKeys } from '../shared/queryKeys';

export type BackendStatus = 'checking' | 'online' | 'offline';

export interface BackendHealth {
  status: BackendStatus;
  message: string | null;
}

export function useBackendHealth(): BackendHealth {
  const query = useQuery({
    // 健康探测只关心「后端在不在」，用最小请求（limit=1）；与卷库分页查询分开缓存键
    queryKey: queryKeys.health,
    queryFn: () => teacherApi.listPapers({ limit: 1, offset: 0 }),
    retry: false,
    refetchInterval: 15_000,
  });

  if (query.isLoading) {
    return { status: 'checking', message: null };
  }
  if (query.isError) {
    const error = query.error;
    if (error instanceof ApiError && error.isBackendUnreachable) {
      return {
        status: 'offline',
        message: error.isOffline
          ? error.message
          : `代理返回 HTTP ${error.status}，说明 127.0.0.1:8000 上没有服务在听。请先启动后端：uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir backend`,
      };
    }
    return {
      status: 'online',
      message:
        error instanceof ApiError
          ? `后端已响应，但 /api/papers 返回错误：${error.code} ${error.message}`
          : String(error),
    };
  }
  return { status: 'online', message: null };
}
