import { Navigate, Route, Routes } from 'react-router-dom';

import { EntryPage } from './pages/EntryPage';
import { PaperPage } from './pages/PaperPage';
import { PracticePage } from './pages/PracticePage';
import { ResultPage } from './pages/ResultPage';

/**
 * 学生端路由表。
 *
 * 路径与老师端**不重叠**（老师端在 `/teacher/**`），所以两边各自的路由互不影响；
 * basename 由 `main.tsx` 交给 BrowserRouter（`/student`），这里只写相对路径。
 *
 * 兜底跳回首页：学生手里的链接可能是旧二维码、也可能手输错了路径，
 * 与其显示一个空白页，不如让他回到能重新开始的地方。
 */
export function App() {
  return (
    <Routes>
      <Route path="/" element={<EntryPage />} />
      <Route path="/practice" element={<PracticePage />} />
      <Route path="/paper/:code" element={<PaperPage />} />
      <Route path="/result" element={<ResultPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
