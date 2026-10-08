import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import { useAuth } from "./lib/auth";

const AIProvidersPage = lazy(() =>
  import("./pages/AIProvidersPage").then(({ AIProvidersPage: Page }) => ({
    default: Page,
  })),
);
const AIWorkspacePage = lazy(() =>
  import("./pages/AIWorkspacePage").then(({ AIWorkspacePage: Page }) => ({
    default: Page,
  })),
);
const AuditPage = lazy(() =>
  import("./pages/AuditPage").then(({ AuditPage: Page }) => ({ default: Page })),
);
const AlertsPage = lazy(() =>
  import("./pages/AlertsPage").then(({ AlertsPage: Page }) => ({ default: Page })),
);
const BlocksPage = lazy(() =>
  import("./pages/BlocksPage").then(({ BlocksPage: Page }) => ({ default: Page })),
);
const BulkPage = lazy(() =>
  import("./pages/BulkPage").then(({ BulkPage: Page }) => ({ default: Page })),
);
const CompetitorsPage = lazy(() =>
  import("./pages/CompetitorsPage").then(({ CompetitorsPage: Page }) => ({
    default: Page,
  })),
);
const DashboardPage = lazy(() =>
  import("./pages/DashboardPage").then(({ DashboardPage: Page }) => ({
    default: Page,
  })),
);
const DomainsPage = lazy(() =>
  import("./pages/DomainsPage").then(({ DomainsPage: Page }) => ({
    default: Page,
  })),
);
const GeoPage = lazy(() =>
  import("./pages/GeoPage").then(({ GeoPage: Page }) => ({ default: Page })),
);
const HelpPage = lazy(() =>
  import("./pages/HelpPage").then(({ HelpPage: Page }) => ({ default: Page })),
);
const KeywordsPage = lazy(() =>
  import("./pages/KeywordsPage").then(({ KeywordsPage: Page }) => ({
    default: Page,
  })),
);
const LeadsPage = lazy(() =>
  import("./pages/LeadsPage").then(({ LeadsPage: Page }) => ({ default: Page })),
);
const LoginPage = lazy(() =>
  import("./pages/LoginPage").then(({ LoginPage: Page }) => ({ default: Page })),
);
const MediaPage = lazy(() =>
  import("./pages/MediaPage").then(({ MediaPage: Page }) => ({ default: Page })),
);
const OpsPage = lazy(() =>
  import("./pages/OpsPage").then(({ OpsPage: Page }) => ({ default: Page })),
);
const ProjectActivityPage = lazy(() =>
  import("./pages/project/activity").then(({ ProjectActivityPage: Page }) => ({
    default: Page,
  })),
);
const ProjectCityProjectsPage = lazy(() =>
  import("./pages/project/city-projects").then(
    ({ ProjectCityProjectsPage: Page }) => ({ default: Page }),
  ),
);
const ProjectDesignPage = lazy(() =>
  import("./pages/project/design").then(({ ProjectDesignPage: Page }) => ({
    default: Page,
  })),
);
const ProjectFactsPage = lazy(() =>
  import("./pages/project/facts").then(({ ProjectFactsPage: Page }) => ({
    default: Page,
  })),
);
const ProjectOverviewPage = lazy(() =>
  import("./pages/project/overview").then(({ ProjectOverviewPage: Page }) => ({
    default: Page,
  })),
);
const ProjectPagesPage = lazy(() =>
  import("./pages/project/pages").then(({ ProjectPagesPage: Page }) => ({
    default: Page,
  })),
);
const ProjectReleasesPage = lazy(() =>
  import("./pages/project/releases").then(({ ProjectReleasesPage: Page }) => ({
    default: Page,
  })),
);
const ProjectRoutingPage = lazy(() =>
  import("./pages/project/routing").then(({ ProjectRoutingPage: Page }) => ({
    default: Page,
  })),
);
const ProjectSiteStructurePage = lazy(() =>
  import("./pages/project/site-structure").then(
    ({ ProjectSiteStructurePage: Page }) => ({ default: Page }),
  ),
);
const SemanticCoveragePage = lazy(() =>
  import("./pages/project/semantic-coverage").then(
    ({ SemanticCoveragePage: Page }) => ({ default: Page }),
  ),
);
const ProjectWorkspacePage = lazy(() =>
  import("./pages/ProjectWorkspacePage").then(({ ProjectWorkspacePage: Page }) => ({
    default: Page,
  })),
);
const ProjectsPage = lazy(() =>
  import("./pages/ProjectsPage").then(({ ProjectsPage: Page }) => ({
    default: Page,
  })),
);
const PromptsPage = lazy(() =>
  import("./pages/PromptsPage").then(({ PromptsPage: Page }) => ({
    default: Page,
  })),
);
const SettingsPage = lazy(() =>
  import("./pages/SettingsPage").then(({ SettingsPage: Page }) => ({
    default: Page,
  })),
);
const SessionHistoryPage = lazy(() =>
  import("./pages/SessionHistoryPage").then(({ SessionHistoryPage: Page }) => ({
    default: Page,
  })),
);
const SitesPage = lazy(() =>
  import("./pages/SitesPage").then(({ SitesPage: Page }) => ({ default: Page })),
);
const SystemOperationsPage = lazy(() =>
  import("./pages/SystemOperationsPage").then(
    ({ SystemOperationsPage: Page }) => ({ default: Page }),
  ),
);

function PageLoading() {
  return (
    <p className="app-loading" aria-live="polite">
      Загрузка страницы…
    </p>
  );
}

export default function App() {
  const { authenticated, loading } = useAuth();

  if (loading) return <p className="app-loading" aria-live="polite">Проверка сессии…</p>;

  return (
    <Routes>
      <Route
        path="/login"
        element={
          <Suspense fallback={<PageLoading />}>
            <LoginPage />
          </Suspense>
        }
      />
      <Route
        path="/*"
        element={
          authenticated ? (
            <Shell>
              <Suspense fallback={<PageLoading />}>
                <Routes>
                  <Route path="/" element={<DashboardPage />} />
                  <Route path="/projects" element={<ProjectsPage />} />
                  {/* Keep the root as the compatibility composed workflow; section links are additive deep links. */}
                  <Route path="/projects/:projectId" element={<ProjectWorkspacePage />} />
                  <Route path="/projects/:projectId/overview" element={<ProjectOverviewPage />} />
                  <Route path="/projects/:projectId/design" element={<ProjectDesignPage />} />
                  <Route path="/projects/:projectId/facts" element={<ProjectFactsPage />} />
                  <Route path="/projects/:projectId/pages" element={<ProjectPagesPage />} />
                  <Route path="/projects/:projectId/releases" element={<ProjectReleasesPage />} />
                  <Route path="/projects/:projectId/routing" element={<ProjectRoutingPage />} />
                  <Route path="/projects/:projectId/site-structure" element={<ProjectSiteStructurePage />} />
                  <Route path="/projects/:projectId/semantic-coverage" element={<SemanticCoveragePage />} />
                  <Route path="/projects/:projectId/city-projects" element={<ProjectCityProjectsPage />} />
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
                  <Route path="/alerts" element={<AlertsPage />} />
                  <Route path="/settings" element={<SettingsPage />} />
                  <Route path="/settings/sessions" element={<SessionHistoryPage />} />
                  <Route path="/audit" element={<AuditPage />} />
                  <Route path="/system" element={<SystemOperationsPage />} />
                  <Route path="/ai" element={<AIWorkspacePage />} />
                  <Route path="/ai/providers" element={<AIProvidersPage />} />
                  <Route path="/ai/prompts" element={<PromptsPage />} />
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
              </Suspense>
            </Shell>
          ) : (
            <Navigate to="/login" replace />
          )
        }
      />
    </Routes>
  );
}
