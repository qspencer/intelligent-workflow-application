import { Component } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { RoleSwitcherComponent } from './components/role-switcher/role-switcher.component';

@Component({
  selector: 'wp-root',
  standalone: true,
  imports: [RouterLink, RouterLinkActive, RouterOutlet, RoleSwitcherComponent],
  template: `
    <header>
      <h1>Workflow Platform</h1>
      <nav>
        <a routerLink="/instances" routerLinkActive="active">Instances</a>
        <a routerLink="/workflows" routerLinkActive="active">Workflows</a>
        <a routerLink="/cost" routerLinkActive="active">Cost</a>
      </nav>
      <wp-role-switcher />
    </header>
    <main>
      <router-outlet />
    </main>
  `,
  styles: [
    `
      header {
        background: var(--panel);
        border-bottom: 1px solid var(--border);
        padding: 12px 24px;
        display: flex;
        align-items: baseline;
        gap: 24px;
      }
      h1 {
        font-size: 16px;
        margin: 0;
        font-weight: 600;
      }
      nav {
        display: flex;
        gap: 16px;
      }
      nav a {
        color: var(--muted);
        font-weight: 500;
      }
      nav a.active {
        color: var(--text);
      }
      main {
        padding: 24px;
        max-width: 1200px;
        margin: 0 auto;
      }
    `,
  ],
})
export class AppComponent {}
