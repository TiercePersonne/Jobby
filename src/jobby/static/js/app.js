import {
  fetchApplications,
  fetchConfig,
  fetchOpportunities,
  setApiToastNotifier,
} from './api.js';
import {
  VALID_VIEWS,
  getActiveKanbanCount,
  getState,
  resolveView,
  setActiveModal,
  setActiveView,
  setApplications,
  setConfig,
  setLoadingFeed,
  setOpportunities,
  subscribe,
} from './state.js';
import { initFeedView } from './views/feed.js';
import { initKanbanView } from './views/kanban.js';
import { initProfileView } from './views/profile.js';
import { initSettingsView } from './views/settings.js';

let lastFocusedElement = null;

/**
 * Displays a sober Warm Porcelain toast message.
 */
export function showToast(message, variant = 'info') {
  const container = document.getElementById('toast-container');
  if (!container || !message) return;

  const toast = document.createElement('div');
  const variantClass =
    variant === 'error'
      ? 'jp-toast jp-toast-error'
      : variant === 'success'
        ? 'jp-toast jp-toast-success'
        : 'jp-toast';
  toast.className = variantClass;

  const textSpan = document.createElement('span');
  textSpan.textContent = String(message);

  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'jp-label shrink-0 cursor-pointer bg-transparent border-0 p-0 text-current';
  closeBtn.setAttribute('aria-label', 'Fermer la notification');
  closeBtn.textContent = 'Fermer';
  closeBtn.addEventListener('click', () => {
    toast.remove();
  });

  toast.appendChild(textSpan);
  toast.appendChild(closeBtn);
  container.appendChild(toast);

  window.setTimeout(() => {
    if (toast.isConnected) {
      toast.remove();
    }
  }, 5000);
}

function registerServiceWorker() {
  if (!('serviceWorker' in navigator)) return;
  const swMeta = document.querySelector('meta[name="service-worker"]');
  const swUrl = swMeta?.getAttribute('content') || '/sw.js';
  const register = () => {
    navigator.serviceWorker.register(swUrl).catch((err) => {
      console.warn('[PWA] Service Worker registration failed:', err);
    });
  };
  if (document.readyState === 'complete') {
    register();
  } else {
    window.addEventListener('load', register, { once: true });
  }
}

function parseHashRoute(rawHash) {
  const cleaned = String(rawHash || '').replace(/^#/, '').trim().toLowerCase();
  if (!cleaned) {
    return { view: 'feed', wasUnknown: false };
  }
  if (VALID_VIEWS.includes(cleaned)) {
    return { view: cleaned, wasUnknown: false };
  }
  return { view: 'feed', wasUnknown: true };
}

function syncViewDom(activeView) {
  document.querySelectorAll('[data-view-panel]').forEach((panel) => {
    const isMatch = panel.getAttribute('data-view-panel') === activeView;
    panel.classList.toggle('hidden', !isMatch);
  });

  document.querySelectorAll('[data-view][id^="nav-"]').forEach((btn) => {
    const isMatch = btn.getAttribute('data-view') === activeView;
    btn.classList.toggle('is-active', isMatch);
    if (isMatch) {
      btn.setAttribute('aria-current', 'page');
    } else {
      btn.removeAttribute('aria-current');
    }
  });
}

function syncKanbanBadge(applications) {
  const badgeEl = document.getElementById('kanban-badge');
  if (!badgeEl) return;
  badgeEl.textContent = String(getActiveKanbanCount(applications));
}

function syncHuntModalPreview(config, configError) {
  const previewEl = document.getElementById('modal-hunt-preview');
  if (!previewEl) return;
  if (configError) {
    previewEl.textContent = 'Configuration indisponible pour le moment.';
    return;
  }
  if (!config) {
    previewEl.textContent = 'Configuration en attente de chargement...';
    return;
  }
  const roles = Array.isArray(config.target_roles) && config.target_roles.length > 0
    ? config.target_roles.join(', ')
    : 'Aucun rôle cible défini';
  previewEl.textContent = `Rôles configurés : ${roles}`;
}

export function navigateTo(requestedView, { syncHash = true, replaceHash = false } = {}) {
  if (getState().activeModal) {
    closeActiveModal();
  }

  const prevView = getState().activeView;
  const nextView = resolveView(requestedView);
  setActiveView(nextView);
  syncViewDom(nextView);

  const targetHash = `#${nextView}`;
  if (syncHash && window.location.hash !== targetHash) {
    if (replaceHash && typeof window.history?.replaceState === 'function') {
      window.history.replaceState(null, '', targetHash);
    } else {
      window.location.hash = targetHash;
    }
  }

  if (prevView !== nextView && typeof window.scrollTo === 'function') {
    window.scrollTo({ top: 0, behavior: 'auto' });
  }
  return nextView;
}

function setBackgroundInert(isInert) {
  ['header', 'main'].forEach((tagName) => {
    const el = document.querySelector(tagName);
    if (!el) return;
    if ('inert' in el) {
      el.inert = Boolean(isInert);
    } else if (isInert) {
      el.setAttribute('inert', '');
    } else {
      el.removeAttribute('inert');
    }
  });
  document.body.style.overflow = isInert ? 'hidden' : '';
}

function getModalFocusableElements(modalEl) {
  return Array.from(
    modalEl.querySelectorAll(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    )
  ).filter((el) => el.offsetParent !== null);
}

export function openModal(modalId) {
  const modalEl = document.getElementById(modalId);
  if (!modalEl) return;

  const currentModalId = getState().activeModal;
  if (currentModalId === modalId) return;

  if (currentModalId) {
    const prevModalEl = document.getElementById(currentModalId);
    if (prevModalEl) {
      prevModalEl.classList.add('hidden');
      prevModalEl.classList.remove('flex');
      prevModalEl.setAttribute('aria-hidden', 'true');
    }
  } else {
    lastFocusedElement = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setBackgroundInert(true);
  }

  modalEl.classList.remove('hidden');
  modalEl.classList.add('flex');
  modalEl.setAttribute('aria-hidden', 'false');
  setActiveModal(modalId);

  const focusables = getModalFocusableElements(modalEl);
  if (focusables.length > 0) {
    focusables[0].focus();
  }
}

export function closeActiveModal() {
  const { activeModal } = getState();
  if (!activeModal) return;

  const modalEl = document.getElementById(activeModal);
  if (modalEl) {
    modalEl.classList.add('hidden');
    modalEl.classList.remove('flex');
    modalEl.setAttribute('aria-hidden', 'true');
  }
  setBackgroundInert(false);
  setActiveModal(null);

  if (lastFocusedElement && lastFocusedElement.isConnected) {
    lastFocusedElement.focus();
  }
  lastFocusedElement = null;
}

function bindNavigationEvents() {
  document.querySelectorAll('[data-view]').forEach((trigger) => {
    trigger.addEventListener('click', (event) => {
      event.preventDefault();
      const targetView = trigger.getAttribute('data-view');
      navigateTo(targetView, { syncHash: true });
    });
  });

  window.addEventListener('hashchange', () => {
    const { view, wasUnknown } = parseHashRoute(window.location.hash);
    navigateTo(view, { syncHash: wasUnknown, replaceHash: wasUnknown });
  });
}

function bindModalAndKeyboardEvents() {
  const openHuntBtn = document.getElementById('btn-open-hunt-modal');
  if (openHuntBtn) {
    openHuntBtn.addEventListener('click', () => {
      openModal('modal-hunt');
    });
  }

  document.querySelectorAll('[data-modal-close]').forEach((closeBtn) => {
    closeBtn.addEventListener('click', () => {
      closeActiveModal();
    });
  });

  document.querySelectorAll('.jp-modal-backdrop').forEach((backdrop) => {
    backdrop.addEventListener('click', (event) => {
      if (event.target === backdrop) {
        closeActiveModal();
      }
    });
  });

  document.addEventListener('keydown', (event) => {
    const { activeModal } = getState();
    if (!activeModal) return;

    if (event.key === 'Escape') {
      event.preventDefault();
      closeActiveModal();
      return;
    }

    if (event.key === 'Tab') {
      const modalEl = document.getElementById(activeModal);
      if (!modalEl) return;
      const focusables = getModalFocusableElements(modalEl);
      if (focusables.length === 0) {
        event.preventDefault();
        return;
      }

      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const activeEl = document.activeElement;

      if (!modalEl.contains(activeEl) || !focusables.includes(activeEl)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
        return;
      }

      if (event.shiftKey && activeEl === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && activeEl === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });
}

async function hydrateInitialData() {
  setLoadingFeed(true, null);

  const [oppsRes, appsRes, configRes] = await Promise.allSettled([
    fetchOpportunities({ limit: 100 }),
    fetchApplications(),
    fetchConfig(),
  ]);

  if (oppsRes.status === 'fulfilled') {
    setOpportunities(oppsRes.value);
  } else {
    setLoadingFeed(
      false,
      oppsRes.reason?.message || 'Impossible de charger les opportunités.'
    );
  }

  if (appsRes.status === 'fulfilled') {
    setApplications(appsRes.value);
  } else {
    setApplications([]);
  }

  if (configRes.status === 'fulfilled') {
    setConfig(configRes.value, null);
  } else {
    setConfig(null, configRes.reason?.message || 'Configuration indisponible.');
  }
}

export async function initApp() {
  setApiToastNotifier((message, variant) => {
    showToast(message, variant);
  });

  registerServiceWorker();

  let prevSnapshot = getState();
  subscribe((snapshot) => {
    if (snapshot.activeView !== prevSnapshot.activeView) {
      syncViewDom(snapshot.activeView);
    }
    if (snapshot.applications !== prevSnapshot.applications) {
      syncKanbanBadge(snapshot.applications);
    }
    if (
      snapshot.config !== prevSnapshot.config ||
      snapshot.configError !== prevSnapshot.configError
    ) {
      syncHuntModalPreview(snapshot.config, snapshot.configError);
    }
    prevSnapshot = snapshot;
  });

  initFeedView();
  initKanbanView();
  initProfileView();
  initSettingsView();

  bindNavigationEvents();
  bindModalAndKeyboardEvents();

  const { view, wasUnknown } = parseHashRoute(window.location.hash);
  navigateTo(view, { syncHash: wasUnknown, replaceHash: wasUnknown });
  syncKanbanBadge(getState().applications);
  syncHuntModalPreview(getState().config, getState().configError);

  await hydrateInitialData();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    initApp();
  });
} else {
  initApp();
}
