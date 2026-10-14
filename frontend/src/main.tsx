import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';

import { App } from './App';
import { ErrorBoundary } from './teacher/ErrorBoundary';
import './styles.css';
// 注意：没有卷面样式文件 —— 卷面由后端 `app/render/` 渲染（D56 / C3），
// 前端只保留控件与容器样式，避免出现第二套排版实现。

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 内网单机部署：不重试、不轮询，失败就明确报错给老师看
      retry: false,
      refetchOnWindowFocus: false,
      staleTime: 5_000,
    },
    mutations: {
      retry: false,
    },
  },
});

const container = document.getElementById('root');
if (!container) {
  throw new Error('找不到 #root 挂载点（index.html 被改动过？）');
}

createRoot(container).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
);
