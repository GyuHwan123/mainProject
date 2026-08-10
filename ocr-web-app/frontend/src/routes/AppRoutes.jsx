import { Routes, Route } from 'react-router-dom';

function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<div>Home</div>} />
      <Route path="/ocr" element={<div>OCR</div>} />
      <Route path="/reports" element={<div>Reports</div>} />
      <Route path="/mypage" element={<div>My Page</div>} />
    </Routes>
  );
}

export default AppRoutes;
