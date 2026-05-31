// "Advanced / Developer" mode toggle.
//
// The friendly surfaces (Automations home, Templates, the canvas) are the
// default. The developer console (Instances / Workflows / Cost) is hidden
// behind this toggle so a non-technical user isn't dropped into a list of
// UUIDs. Persisted in localStorage, same pattern as the role switcher.

const KEY = 'wp.advanced';

export function advancedEnabled(): boolean {
  try {
    return localStorage.getItem(KEY) === '1';
  } catch {
    return false;
  }
}

export function setAdvanced(on: boolean): void {
  try {
    if (on) localStorage.setItem(KEY, '1');
    else localStorage.removeItem(KEY);
  } catch {
    // Non-fatal: storage may be unavailable (private mode / SSR).
  }
}
