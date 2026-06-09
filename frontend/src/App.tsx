import { Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import Settings from "./pages/Settings";
import Workspace from "./pages/Workspace";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="workspace/:stem" element={<Workspace />} />
        <Route path="settings" element={<Settings />} />
      </Route>
    </Routes>
  );
}
