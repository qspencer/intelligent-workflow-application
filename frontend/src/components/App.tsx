import { useEffect, useState } from 'react';
import { NavLink, Route, Routes } from 'react-router';

import { advancedEnabled, setAdvanced } from '../lib/advanced';
import { setSessionRoles } from '../lib/auth';
import { getMe } from '../lib/me';
import { AutomationsHome } from './AutomationsHome';
import { CostDashboard } from './CostDashboard';
import { ErrorBadge } from './ErrorBadge';
import { InstanceDetail } from './InstanceDetail';
import { InstancesList } from './InstancesList';
import { LoginPage } from './LoginPage';
import { RoleSwitcher } from './RoleSwitcher';
import { UserChip } from './UserChip';
import { CompareRuns } from './CompareRuns';
import { TemplatesGallery } from './TemplatesGallery';
import { UsersAdmin } from './UsersAdmin';
import { WorkflowsList } from './WorkflowsList';
import { WorkflowCanvas } from './canvas/WorkflowCanvas';

function navClass({ isActive }: { isActive: boolean }): string {
  return isActive ? 'active' : '';
}

export function App() {
  const [advanced, setAdvancedState] = useState(advancedEnabled());
  const [needsLogin, setNeedsLogin] = useState(false);
  const [authReady, setAuthReady] = useState(false);

  // Resolve the session before rendering routes so hasRole gates read REAL
  // roles in local/oidc mode (dev mode keeps the RoleSwitcher's localStorage
  // path — sessionRoles stays null). getMe is memoized; on backend-down it
  // resolves null quickly and the shell renders as before.
  useEffect(() => {
    let ignore = false;
    void getMe().then((me) => {
      if (ignore) return;
      setSessionRoles(me && me.auth_mode !== 'dev' ? me.identity.roles : null);
      setAuthReady(true);
    });
    return () => {
      ignore = true;
    };
  }, []);

  // Local-mode session absent/expired: any API 401 flips the whole shell to
  // the login page (the API client dispatches this; docs/AUTH_PLAN.md §6).
  useEffect(() => {
    const onUnauthorized = (): void => setNeedsLogin(true);
    window.addEventListener('wp:unauthorized', onUnauthorized);
    return () => window.removeEventListener('wp:unauthorized', onUnauthorized);
  }, []);

  function toggleAdvanced(): void {
    const next = !advanced;
    setAdvanced(next);
    setAdvancedState(next);
  }

  if (needsLogin) return <LoginPage />;
  if (!authReady) return null;

  return (
    <>
      <header>
        <h1>Workflow Platform</h1>
        <nav>
          <NavLink to="/" end className={navClass}>
            Automations
          </NavLink>
          <NavLink to="/templates" className={navClass}>
            Templates
          </NavLink>
          {advanced && (
            <>
              <NavLink to="/instances" className={navClass}>
                Instances
              </NavLink>
              <NavLink to="/workflows" className={navClass}>
                Workflows
              </NavLink>
              <NavLink to="/cost" className={navClass}>
                Cost
              </NavLink>
              <NavLink to="/users" className={navClass}>
                Users
              </NavLink>
            </>
          )}
        </nav>
        <div className="header-right">
          <ErrorBadge />
          <button
            className="advanced-toggle"
            onClick={toggleAdvanced}
            aria-pressed={advanced}
            title="Show developer tools (instances, workflows, cost)"
          >
            {advanced ? 'Developer: on' : 'Developer: off'}
          </button>
          <UserChip />
          <RoleSwitcher />
        </div>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<AutomationsHome />} />
          <Route path="/templates" element={<TemplatesGallery />} />
          <Route path="/workflows" element={<WorkflowsList />} />
          <Route path="/canvas/:id" element={<WorkflowCanvas />} />
          <Route path="/instances" element={<InstancesList />} />
          <Route path="/instances/:id" element={<InstanceDetail />} />
          <Route path="/compare/:a/:b" element={<CompareRuns />} />
          <Route path="/cost" element={<CostDashboard />} />
          <Route path="/users" element={<UsersAdmin />} />
        </Routes>
      </main>
    </>
  );
}
