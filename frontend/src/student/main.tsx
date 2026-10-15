/**
 * 学生端入口（M4）。
 *
 * 两点和老师端不同的地方：
 * 1. **basename 必须是 `/student`**（契约 §7）：后端按前缀把 `/student/**` 都交给
 *    `student.html`，深链接 `/student/paper/2601-0001` 能不能匹配到路由全靠它。
 * 2. **样式先 import 老师端的设计令牌**，再 import 学生端自己的 `student.css`：
 *    判据 A1 对整个 `frontend/src` 生效，颜色只能来自 `var(--mg-*)`；
 *    学生端没有复用老师端的 `src/styles.css`——那是三栏工作台外壳，手机上没法用（D-5）。
 */
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

import '../design/tokens.css';
import './student.css';
import { App } from './App';

const container = document.getElementById('root');
if (!container) {
  throw new Error('找不到 #root 挂载点（student.html 被改动过？）');
}

createRoot(container).render(
  <StrictMode>
    <BrowserRouter basename="/student">
      <App />
    </BrowserRouter>
  </StrictMode>,
);
