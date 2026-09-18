import { Navigate, Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import { BulkPage } from "./pages/BulkPage";
import { BlocksPage } from "./pages/BlocksPage";
import { DashboardPage } from "./pages/DashboardPage";
import { DomainsPage } from "./pages/DomainsPage";
import { GeoPage } from "./pages/GeoPage";
import { KeywordsPage } from "./pages/KeywordsPage";
import { LeadsPage } from "./pages/LeadsPage";
import { LoginPage } from "./pages/LoginPage";
import { MediaPage } from "./pages/MediaPage";
import { OnboardingPage } from "./pages/OnboardingPage";
import { OpsPage } from "./pages/OpsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { SitesPage } from "./pages/SitesPage";
import { useAuth } from "./lib/auth";

export default function App() {
  const { authenticated, loading } = useAuth();

  if (loading) return null;

  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/*"
        element={
          authenticated ? (
            <Shell>
              <Routes>
                <Route path="/" element={<DashboardPage />} />
                <Route path="/sites" element={<SitesPage />} />
                <Route path="/keywords" element={<KeywordsPage />} />
                <Route path="/geo" element={<GeoPage />} />
                <Route path="/domains" element={<DomainsPage />} />
                <Route path="/blocks" element={<BlocksPage />} />
                <Route path="/media" element={<MediaPage />} />
                <Route path="/bulk" element={<BulkPage />} />
                <Route path="/onboarding" element={<OnboardingPage />} />
                <Route path="/leads" element={<LeadsPage />} />
                <Route path="/ops" element={<OpsPage />} />
                <Route path="/settings" element={<SettingsPage />} />
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
