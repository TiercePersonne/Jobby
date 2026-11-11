import { getState, subscribe } from '../state.js';

export function renderSettingsView(snapshot = getState()) {
  const summaryEl = document.getElementById('settings-shell-summary');
  if (!summaryEl) return;

  if (snapshot.configError) {
    summaryEl.textContent = 'Configuration indisponible pour le moment.';
    return;
  }

  const cfg = snapshot.config;
  if (!cfg) {
    summaryEl.textContent = 'Configuration en attente de chargement...';
    return;
  }

  const rolesCount = Array.isArray(cfg.target_roles) ? cfg.target_roles.length : 0;
  const provider = cfg.ai_provider || 'auto';
  summaryEl.textContent = `${rolesCount} rôle${rolesCount > 1 ? 's' : ''} configuré${rolesCount > 1 ? 's' : ''} • Fournisseur IA : ${provider}`;
}

export function initSettingsView() {
  let prevState = getState();
  subscribe((snapshot) => {
    if (
      snapshot.config !== prevState.config ||
      snapshot.configError !== prevState.configError
    ) {
      prevState = snapshot;
      renderSettingsView(snapshot);
    }
  });
  renderSettingsView(getState());
}
