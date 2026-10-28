/** 老师端路由表 —— 与 `docs/04-接口契约.md` §7 的前端路由表一一对应。 */

import { Link, Navigate, Route, Routes } from 'react-router-dom';

import { TeacherShell } from './teacher/TeacherShell';
import { ConfigPage } from './teacher/pages/ConfigPage';
import { PaperPage } from './teacher/pages/PaperPage';
import { LibraryPage } from './teacher/pages/LibraryPage';
import { PublishPage } from './teacher/pages/PublishPage';
import { AttemptsPage } from './teacher/pages/AttemptsPage';
import { AttemptDetailPage } from './teacher/pages/AttemptDetailPage';

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/teacher" replace />} />
      <Route path="/teacher" element={<TeacherShell />}>
        <Route index element={<ConfigPage />} />
        <Route path="paper/:id" element={<PaperPage />} />
        <Route path="paper/:id/publish" element={<PublishPage />} />
        <Route path="library" element={<LibraryPage />} />
        {/* 成绩：一套卷的成绩（docs/04 §7 的 `/teacher/paper/:id/attempts`）
            与教师端「全部成绩」共用同一个组件，见 AttemptsPage 顶部注释。
            `attempts` 与 `attempts/:attemptId` 都由 react-router 按段打分自动排序，
            写在哪个顺序都对；这里按「先列后明」写只是为了读起来顺。 */}
        <Route path="paper/:paperId/attempts" element={<AttemptsPage />} />
        <Route path="attempts" element={<AttemptsPage />} />
        <Route path="attempts/:attemptId" element={<AttemptDetailPage />} />
      </Route>
      <Route
        path="*"
        element={
          <div className="fatal-error">
            <h1>没有这个页面</h1>
            <p>地址可能打错了，也可能这一页只在某个流程中间出现。</p>
            <p>
              <Link className="btn" to="/teacher">
                回到出题配置
              </Link>
            </p>
          </div>
        }
      />
    </Routes>
  );
}
