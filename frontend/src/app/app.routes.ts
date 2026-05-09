import { Routes } from '@angular/router';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'instances' },
  {
    path: 'workflows',
    loadComponent: () =>
      import('./components/workflows-list/workflows-list.component').then(
        (m) => m.WorkflowsListComponent,
      ),
  },
  {
    path: 'instances',
    loadComponent: () =>
      import('./components/instances-list/instances-list.component').then(
        (m) => m.InstancesListComponent,
      ),
  },
  {
    path: 'instances/:id',
    loadComponent: () =>
      import('./components/instance-detail/instance-detail.component').then(
        (m) => m.InstanceDetailComponent,
      ),
  },
];
