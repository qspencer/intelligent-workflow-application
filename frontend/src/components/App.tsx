import { useState } from 'react';
import { NavLink, Route, Routes } from 'react-router-dom';

import { advancedEnabled, setAdvanced } from '../lib/advanced';
import { AutomationsHome } from './AutomationsHome';
import { CostDashboard } from './CostDashboard';
import { ErrorBadge } from './ErrorBadge';
import { InstanceDetail } from './InstanceDetail';
import { InstancesList } from './InstancesList';
import { RoleSwitcher } from './RoleSwitcher';
import { TemplatesGallery } from './TemplatesGallery';
import { WorkflowsList } from './WorkflowsList';
import { WorkflowCanvas } from './canvas/WorkflowCanvas';

function navClass({ isActive }: { isActive: boolean }): string {
  return isActive ? 'active' : '';
}

export function App() {
  const [advanced, setAdvancedState] = useState(advancedEnabled());

  function toggleAdvanced(): void {
    const next = !advanced;
    setAdvanced(next);
    setAdvancedState(next);
  }

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
          <Route path="/cost" element={<CostDashboard />} />
        </Routes>
      </main>
    </>
  );
}
