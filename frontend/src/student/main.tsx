/**
 * 学生端入口（M4）。
 *
 * **这是 Lead 铺的脚手架，fe-student 请直接改写本文件**——它存在的唯一目的是让
 * `student.html` 的构建链路先通（`vite.config.ts` 的多入口 + 后端 `student.html` 落盘），
 * 这样「入口装配」这个跨角色的缝**先于实现**被验证过一次，而不是等到 M4 收尾才发现接不上。
 *
 * 三点交接提醒：
 * 1. **basename**：路由是 `/student/*`（契约 §7），`BrowserRouter` 要带 `basename="/student"`，
 *    否则深链接 `/student/paper/2601-0001` 会被当成 `/` 而匹配不到路由。
 * 2. **样式**：老师端的 `src/styles.css` 是**工作台三栏外壳**，不是给学生端用的。
 *    学生端自己建 `src/student/student.css`，但**必须**先 `import '../design/tokens.css'`
 *    并且只引用 `var(--mg-*)`——判据 A1 会对整个 `frontend/src` 生效（除 `tokens.css` 外颜色字面量必须为 0）。
 * 3. **卷面不可自绘**：题目正文由后端 `app/render/` 渲染（D56 / C3）。
 *    学生端**不要**自己用 React 拼题面排版，`display` 字段拿到的就是可展示文本。
 */
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

import '../design/tokens.css';

const container = document.getElementById('root');
if (!container) {
  throw new Error('找不到 #root 挂载点（student.html 被改动过？）');
}

createRoot(container).render(
  <StrictMode>
    <BrowserRouter basename="/student">
      <main className="mg-student" />
    </BrowserRouter>
  </StrictMode>,
);
