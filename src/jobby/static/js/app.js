import {
  fetchApplications,
  fetchConfig,
  fetchOpportunities,
  setApiToastNotifier,
  triggerSearch,
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
import { showToast } from './utils.js';
import { initFeedView } from './views/feed.js';
import { initKanbanView } from './views/kanban.js';
import { initProfileView } from './views/profile.js';
import { initSettingsView } from './views/settings.js';

export { showToast };

let lastFocusedElement = null;
let huntFormDirty = false;
let isSearchingHunt = false;
let lastIncludePostsBeforeDisable = true;

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

function syncLinkedInPostsToggle() {
  const linkedinCb = document.querySelector('#hunt-form input[data-hunt-source="linkedin"]');
  const includePostsEl = document.getElementById('hunt-include-posts');
  if (!linkedinCb || !includePostsEl) return;

  const linkedinSelected = Boolean(linkedinCb.checked);
  if (!linkedinSelected) {
    if (!includePostsEl.disabled) {
      lastIncludePostsBeforeDisable = Boolean(includePostsEl.checked);
    }
    includePostsEl.disabled = true;
    includePostsEl.checked = false;
  } else {
    const wasDisabled = includePostsEl.disabled;
    includePostsEl.disabled = false;
    if (wasDisabled) {
      includePostsEl.checked = lastIncludePostsBeforeDisable;
    }
  }
}

function syncHuntModalPreview(config, configError) {
  const previewEl = document.getElementById('modal-hunt-preview');
  if (previewEl) {
    if (configError) {
      previewEl.textContent = 'Configuration indisponible pour le moment.';
    } else if (!config) {
      previewEl.textContent = 'Configuration en attente de chargement...';
    } else {
      const roles = Array.isArray(config.target_roles) && config.target_roles.length > 0
        ? config.target_roles.join(', ')
        : 'Aucun rôle cible défini';
      previewEl.textContent = `Rôles configurés : ${roles}`;
    }
  }

  if (!config || configError || huntFormDirty) {
    syncLinkedInPostsToggle();
    return;
  }

  const keywordsEl = document.getElementById('hunt-keywords');
  if (keywordsEl) {
    keywordsEl.value = Array.isArray(config.target_roles) ? config.target_roles.join(', ') : '';
  }

  const locationEl = document.getElementById('hunt-location');
  if (locationEl) {
    locationEl.value =
      Array.isArray(config.preferred_locations) && config.preferred_locations.length > 0
        ? config.preferred_locations[0]
        : '';
  }

  const remoteEl = document.getElementById('hunt-remote-only');
  if (remoteEl) {
    remoteEl.checked = Boolean(config.remote_only);
  }

  const includePostsEl = document.getElementById('hunt-include-posts');
  if (includePostsEl && config.linkedin_include_posts !== undefined) {
    lastIncludePostsBeforeDisable = Boolean(config.linkedin_include_posts);
    includePostsEl.checked = lastIncludePostsBeforeDisable;
  }

  syncLinkedInPostsToggle();
}

async function handleHuntSubmit() {
  if (isSearchingHunt) return;

  const keywordsEl = document.getElementById('hunt-keywords');
  const locationEl = document.getElementById('hunt-location');
  const remoteEl = document.getElementById('hunt-remote-only');
  const includePostsEl = document.getElementById('hunt-include-posts');
  const submitBtn = document.getElementById('btn-submit-hunt');
  const headerBtn = document.getElementById('btn-open-hunt-modal');

  const selectedSources = Array.from(
    document.querySelectorAll('#hunt-form input[data-hunt-source]:checked')
  ).map((input) => input.value);

  if (selectedSources.length === 0) {
    showToast('Sélectionnez au moins une source pour lancer la chasse.', 'error');
    return;
  }

  const keywords = (keywordsEl?.value || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
  const location = (locationEl?.value || '').trim() || null;
  const remoteOnly = Boolean(remoteEl?.checked);
  const includePosts =
    includePostsEl && !includePostsEl.disabled ? Boolean(includePostsEl.checked) : false;

  isSearchingHunt = true;
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = 'Chasse en cours...';
  }
  if (headerBtn) {
    headerBtn.setAttribute('aria-busy', 'true');
  }

  try {
    let searchResult;
    try {
      searchResult = await triggerSearch({
        keywords,
        location,
        remote_only: remoteOnly,
        max_results: 30,
        sources: selectedSources,
        include_posts: includePosts,
        include_offers: true,
      });
    } catch {
      // Error toast emitted by api.js; keep modal open so user can retry or cancel.
      return;
    }

    let refreshFailed = false;
    try {
      const freshOpportunities = await fetchOpportunities({ limit: 100 });
      setOpportunities(freshOpportunities);
    } catch {
      refreshFailed = true;
      const incoming = Array.isArray(searchResult?.opportunities)
        ? searchResult.opportunities
        : [];
      if (incoming.length > 0) {
        const existing = getState().opportunities || [];
        const incomingIds = new Set(incoming.map((opp) => opp?.id).filter(Boolean));
        setOpportunities([
          ...incoming,
          ...existing.filter((opp) => !incomingIds.has(opp?.id)),
        ]);
      }
    }

    if (getState().activeModal === 'modal-hunt') {
      closeActiveModal();
    }

    const totalFound = Number(searchResult?.total_found ?? 0);
    const newCount = Number(searchResult?.new_count ?? 0);
    const foundLabel = `${totalFound} opportunité${totalFound > 1 ? 's' : ''}`;
    const newLabel = `${newCount} nouvelle${newCount > 1 ? 's' : ''}`;
    if (refreshFailed) {
      showToast(
        `Chasse terminée (${foundLabel}, ${newLabel}), mais le rafraîchissement complet du flux a échoué.`,
        'info'
      );
    } else {
      showToast(
        `Chasse terminée : ${foundLabel} trouvée${totalFound > 1 ? 's' : ''}, ${newLabel}.`,
        'success'
      );
    }
  } finally {
    isSearchingHunt = false;
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Lancer la chasse';
    }
    if (headerBtn) {
      headerBtn.removeAttribute('aria-busy');
    }
  }
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

  const huntForm = document.getElementById('hunt-form');
  if (huntForm) {
    huntForm.addEventListener('input', () => {
      huntFormDirty = true;
    });
    huntForm.addEventListener('change', (event) => {
      huntFormDirty = true;
      const target = event.target;
      if (target instanceof HTMLInputElement) {
        if (target.matches('input[data-hunt-source="linkedin"]')) {
          syncLinkedInPostsToggle();
        } else if (target.id === 'hunt-include-posts' && !target.disabled) {
          lastIncludePostsBeforeDisable = Boolean(target.checked);
        }
      }
    });
    huntForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      await handleHuntSubmit();
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
  huntFormDirty = false;
  isSearchingHunt = false;
  lastIncludePostsBeforeDisable = true;

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
