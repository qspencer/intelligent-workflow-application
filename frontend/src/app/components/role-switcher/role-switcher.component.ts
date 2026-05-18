import { CommonModule } from '@angular/common';
import { Component, signal } from '@angular/core';

/**
 * Dev-mode role switcher. Writes `wp.user` + `wp.groups` to localStorage so
 * the auth interceptor picks them up on subsequent API calls. Defaults to
 * whatever's already there (or `dev-user` / `admins` on a fresh install).
 *
 * Reloads the page after a change so all in-flight signals re-fetch with
 * the new identity. Cheap, predictable, no signal-wiring required.
 */
@Component({
  selector: 'wp-role-switcher',
  standalone: true,
  imports: [CommonModule],
  template: `
    <label>
      <span class="label">Acting as</span>
      <select [value]="currentGroups()" (change)="onChange($event)">
        <option value="admins">Admin</option>
        <option value="designers">Workflow Designer</option>
        <option value="operators">Operator</option>
        <option value="viewers">Viewer</option>
        <option value="auditors">Auditor</option>
      </select>
    </label>
  `,
  styles: [
    `
      :host {
        margin-left: auto;
      }
      label {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 13px;
      }
      .label {
        color: var(--muted);
      }
      select {
        padding: 4px 8px;
        font-size: 13px;
        border: 1px solid var(--border);
        background: var(--panel);
        border-radius: 4px;
        cursor: pointer;
      }
    `,
  ],
})
export class RoleSwitcherComponent {
  readonly currentGroups = signal<string>(this.readGroups());

  private readGroups(): string {
    const value = localStorage.getItem('wp.groups');
    const known = ['admins', 'designers', 'operators', 'viewers', 'auditors'];
    if (value && known.includes(value)) return value;
    return 'admins';
  }

  onChange(event: Event): void {
    const target = event.target as HTMLSelectElement;
    const groups = target.value;
    localStorage.setItem('wp.groups', groups);
    // Reuse whatever username was set; default to dev-user.
    if (!localStorage.getItem('wp.user')) {
      localStorage.setItem('wp.user', 'dev-user');
    }
    this.currentGroups.set(groups);
    // Refresh so every component re-fetches with the new identity header.
    window.location.reload();
  }
}
