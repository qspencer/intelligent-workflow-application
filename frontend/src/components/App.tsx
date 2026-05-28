import { NavLink, Navigate, Route, Routes } from 'react-router-dom';

import { CostDashboard } from './CostDashboard';
import { InstanceDetail } from './InstanceDetail';
import { InstancesList } from './InstancesList';
import { RoleSwitcher } from './RoleSwitcher';
import { WorkflowsList } from './WorkflowsList';
import { WorkflowCanvas } from './canvas/WorkflowCanvas';

function navClass({ isActive }: { isActive: boolean }): string {
  return isActive ? 'active' : '';
}

export function App() {
  return (
    <>
      <header>
        <h1>Workflow Platform</h1>
        <nav>
          <NavLink to="/instances" className={navClass}>
            Instances
          </NavLink>
          <NavLink to="/workflows" className={navClass}>
            Workflows
          </NavLink>
          <NavLink to="/cost" className={navClass}>
            Cost
          </NavLink>
        </nav>
        <RoleSwitcher />
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/instances" replace />} />
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
