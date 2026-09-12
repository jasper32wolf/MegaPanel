import { Navigate, Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import { BlocksPage } from "./pages/BlocksPage";
import { DashboardPage } from "./pages/DashboardPage";
import { DomainsPage } from "./pages/DomainsPage";
import { LeadsPage } from "./pages/LeadsPage";
import { LoginPage } from "./pages/LoginPage";
import { OnboardingPage } from "./pages/OnboardingPage";
import { OpsPage } from "./pages/OpsPage";
import { PanelToolsPage } from "./pages/PanelToolsPage";
import { SitesPage } from "./pages/SitesPage";
import { useAuth } from "./lib/auth";

export default function App() {
  const { token } = useAuth();

  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/*"
        element={
          token ? (
            <Shell>
              <Routes>
                <Route path="/" element={<DashboardPage />} />
                <Route path="/sites" element={<SitesPage />} />
                <Route path="/domains" element={<DomainsPage />} />
                <Route path="/blocks" element={<BlocksPage />} />
                <Route path="/onboarding" element={<OnboardingPage />} />
                <Route path="/leads" element={<LeadsPage />} />
                <Route path="/ops" element={<OpsPage />} />
                <Route path="/tools" element={<PanelToolsPage />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </Shell>
          ) : (
            <Navigate to="/login" replace />
          )
        }
      />
    </Routes>
  );
}
