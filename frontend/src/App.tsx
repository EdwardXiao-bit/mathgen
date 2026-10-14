/** 老师端路由表 —— 与 `docs/04-接口契约.md` §7 的前端路由表一一对应。 */

import { Navigate, Route, Routes } from 'react-router-dom';

import { TeacherShell } from './teacher/TeacherShell';
import { ConfigPage } from './teacher/pages/ConfigPage';
import { PaperPage } from './teacher/pages/PaperPage';
import { LibraryPage } from './teacher/pages/LibraryPage';
import { PublishPage } from './teacher/pages/PublishPage';

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/teacher" replace />} />
      <Route path="/teacher" element={<TeacherShell />}>
        <Route index element={<ConfigPage />} />
        <Route path="paper/:id" element={<PaperPage />} />
        <Route path="paper/:id/publish" element={<PublishPage />} />
        <Route path="library" element={<LibraryPage />} />
      </Route>
      <Route
        path="*"
        element={
          <div className="fatal-error">
            <h1>没有这个页面</h1>
            <p>老师端目前只有：/teacher、/teacher/paper/:id、/teacher/paper/:id/publish、/teacher/library。</p>
          </div>
        }
      />
    </Routes>
  );
}
