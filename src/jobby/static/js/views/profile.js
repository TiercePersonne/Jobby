import { getState, subscribe } from '../state.js';

export function renderProfileView() {
  // Vue 3 reste volontairement en coquille d'attente Warm Porcelain (Story 1.1)
  // et isolée du flux actif (AD-2).
}

export function initProfileView() {
  subscribe((snapshot) => {
    if (snapshot.activeView === 'profile') {
      renderProfileView(snapshot);
    }
  });
  renderProfileView(getState());
}
