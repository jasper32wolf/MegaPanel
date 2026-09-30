import { Navigate, Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import { AIProvidersPage } from "./pages/AIProvidersPage";
import { AIWorkspacePage } from "./pages/AIWorkspacePage";
import { AuditPage } from "./pages/AuditPage";
import { BlocksPage } from "./pages/BlocksPage";
import { BulkPage } from "./pages/BulkPage";
import { CompetitorsPage } from "./pages/CompetitorsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { DomainsPage } from "./pages/DomainsPage";
import { GeoPage } from "./pages/GeoPage";
import { HelpPage } from "./pages/HelpPage";
import { KeywordsPage } from "./pages/KeywordsPage";
import { LeadsPage } from "./pages/LeadsPage";
import { LoginPage } from "./pages/LoginPage";
import { MediaPage } from "./pages/MediaPage";
import { OpsPage } from "./pages/OpsPage";
import { ProjectActivityPage } from "./pages/project/activity";
import { ProjectFactsPage } from "./pages/project/facts";
import { ProjectOverviewPage } from "./pages/project/overview";
import { ProjectPagesPage } from "./pages/project/pages";
import { ProjectReleasesPage } from "./pages/project/releases";
import { ProjectRoutingPage } from "./pages/project/routing";
import { ProjectWorkspacePage } from "./pages/ProjectWorkspacePage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { PromptsPage } from "./pages/PromptsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { SitesPage } from "./pages/SitesPage";
import { SystemOperationsPage } from "./pages/SystemOperationsPage";
import { useAuth } from "./lib/auth";

export default function App() {
  const { authenticated, loading } = useAuth();

  if (loading) return <p className="app-loading" aria-live="polite">Проверка сессии…</p>;

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
                <Route path="/projects" element={<ProjectsPage />} />
                {/* Keep the root as the compatibility composed workflow; section links are additive deep links. */}
                <Route path="/projects/:projectId" element={<ProjectWorkspacePage />} />
                <Route path="/projects/:projectId/overview" element={<ProjectOverviewPage />} />
                <Route path="/projects/:projectId/facts" element={<ProjectFactsPage />} />
                <Route path="/projects/:projectId/pages" element={<ProjectPagesPage />} />
                <Route path="/projects/:projectId/releases" element={<ProjectReleasesPage />} />
                <Route path="/projects/:projectId/routing" element={<ProjectRoutingPage />} />
                {/* Existing activity URL remains unchanged for bookmarks and E2E coverage. */}
                <Route path="/projects/:projectId/activity" element={<ProjectActivityPage />} />
                <Route path="/sites" element={<SitesPage />} />
                <Route path="/keywords" element={<KeywordsPage />} />
                <Route path="/competitors" element={<CompetitorsPage />} />
                <Route path="/geo" element={<GeoPage />} />
                <Route path="/help" element={<HelpPage />} />
                <Route path="/domains" element={<DomainsPage />} />
                <Route path="/blocks" element={<BlocksPage />} />
                <Route path="/media" element={<MediaPage />} />
                <Route path="/bulk" element={<BulkPage />} />
                <Route path="/leads" element={<LeadsPage />} />
                <Route path="/ops" element={<OpsPage />} />
                <Route path="/settings" element={<SettingsPage />} />
                <Route path="/audit" element={<AuditPage />} />
                <Route path="/system" element={<SystemOperationsPage />} />
                <Route path="/ai" element={<AIWorkspacePage />} />
                <Route path="/ai/providers" element={<AIProvidersPage />} />
                <Route path="/ai/prompts" element={<PromptsPage />} />
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
