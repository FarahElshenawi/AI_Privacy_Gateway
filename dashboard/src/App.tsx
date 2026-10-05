import { Routes, Route, Navigate } from "react-router-dom";
import { Sidebar } from "./components/Sidebar";
import { Topbar } from "./components/Topbar";
import { Overview } from "./pages/Overview";
import { Policies } from "./pages/Policies";
import { Audit } from "./pages/Audit";
import { Settings } from "./pages/Settings";

export default function App() {
  return (
    <div className="min-h-screen flex bg-[var(--bg)] text-[var(--ink)]">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">
        <Topbar />
        <main className="flex-1 p-6 lg:p-8 overflow-x-hidden">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/policies" element={<Policies />} />
            <Route path="/audit" element={<Audit />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
