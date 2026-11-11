/**
 * Centralized reactive store for Jobby.
 * Views read and mutate shared state here instead of importing each other.
 */

export const VALID_VIEWS = Object.freeze(['feed', 'kanban', 'profile', 'settings']);

const STATUS_ALIASES = Object.freeze(
  Object.assign(Object.create(null), {
    favorite: 'kept',
    saved: 'kept',
  })
);

const INACTIVE_KANBAN_STATUSES = new Set(['rejected', 'archived']);
const VALID_APPLICATION_STAGES = new Set(['saved', 'applied', 'interview', 'offer', 'rejected']);

/**
 * Normalizes legacy SQLite opportunity statuses ('favorite', 'saved') to 'kept'.
 * All other statuses are returned lowercased and trimmed intact.
 */
export function normalizeOpportunityStatus(status) {
  if (!status) return 'new';
  const key = String(status).trim().toLowerCase();
  if (!key) return 'new';
  return Object.hasOwn(STATUS_ALIASES, key) ? STATUS_ALIASES[key] : key;
}

/**
 * Resolves a requested view name against the 4 allowed routes, falling back to 'feed'.
 */
export function resolveView(view) {
  const normalized = String(view || '').trim().toLowerCase();
  return VALID_VIEWS.includes(normalized) ? normalized : 'feed';
}

function createInitialState() {
  return {
    activeView: 'feed',
    opportunities: [],
    applications: [],
    config: null,
    configError: null,
    isLoadingFeed: false,
    feedError: null,
    activeModal: null,
  };
}

let state = createInitialState();
const listeners = new Set();

function emit() {
  const snapshot = getState();
  listeners.forEach((listener) => {
    try {
      listener(snapshot);
    } catch (err) {
      console.error('[state] Subscriber error:', err);
    }
  });
}

export function getState() {
  return state;
}

export function subscribe(listener) {
  if (typeof listener !== 'function') {
    return () => {};
  }
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function resetState() {
  state = createInitialState();
  emit();
}

export function setActiveView(view) {
  const nextView = resolveView(view);
  if (state.activeView === nextView) return state.activeView;
  state = { ...state, activeView: nextView };
  emit();
  return nextView;
}

export function setLoadingFeed(isLoadingFeed, feedError = null) {
  state = { ...state, isLoadingFeed: Boolean(isLoadingFeed), feedError };
  emit();
}

export function setOpportunities(list) {
  const normalized = Array.isArray(list)
    ? list.map((opp) => ({
        ...opp,
        status: normalizeOpportunityStatus(opp?.status),
      }))
    : [];
  state = {
    ...state,
    opportunities: normalized,
    isLoadingFeed: false,
    feedError: null,
  };
  emit();
}

export function setApplications(list) {
  const safeList = Array.isArray(list) ? list.map((app) => ({ ...app })) : [];
  state = { ...state, applications: safeList };
  emit();
}

export function setConfig(config, error = null) {
  state = {
    ...state,
    config: config || null,
    configError: error || null,
  };
  emit();
}

export function setActiveModal(modalId) {
  const nextModal = modalId || null;
  if (state.activeModal === nextModal) return;
  state = { ...state, activeModal: nextModal };
  emit();
}

/**
 * Counts active Kanban applications, excluding archived/rejected entries.
 */
export function getActiveKanbanCount(applications = state.applications) {
  if (!Array.isArray(applications)) return 0;
  return applications.filter((app) => {
    const st = String(app?.status || 'saved').toLowerCase();
    return !INACTIVE_KANBAN_STATUSES.has(st);
  }).length;
}

/**
 * Updates an opportunity status and keeps the Kanban applications list synchronized.
 */
export function syncOpportunityStatus(oppId, rawStatus) {
  const canonicalStatus = normalizeOpportunityStatus(rawStatus);
  let matchedOpp = null;

  const nextOpportunities = state.opportunities.map((opp) => {
    if (opp.id !== oppId) return opp;
    matchedOpp = { ...opp, status: canonicalStatus };
    return matchedOpp;
  });

  let nextApplications = state.applications.slice();
  const existingIdx = nextApplications.findIndex((app) => app.opportunity_id === oppId);

  if (canonicalStatus === 'kept') {
    if (existingIdx >= 0) {
      nextApplications[existingIdx] = { ...nextApplications[existingIdx], status: 'saved' };
    } else {
      nextApplications.push({
        id: `app_optimistic_${oppId}`,
        opportunity_id: oppId,
        status: 'saved',
        opportunity: matchedOpp || undefined,
      });
    }
  } else if (canonicalStatus === 'archived' || canonicalStatus === 'rejected') {
    if (existingIdx >= 0) {
      nextApplications[existingIdx] = { ...nextApplications[existingIdx], status: 'rejected' };
    } else {
      nextApplications.push({
        id: `app_optimistic_${oppId}`,
        opportunity_id: oppId,
        status: 'rejected',
        opportunity: matchedOpp || undefined,
      });
    }
  } else if (canonicalStatus === 'applied' || canonicalStatus === 'interview') {
    if (existingIdx >= 0) {
      nextApplications[existingIdx] = { ...nextApplications[existingIdx], status: canonicalStatus };
    } else {
      nextApplications.push({
        id: `app_optimistic_${oppId}`,
        opportunity_id: oppId,
        status: canonicalStatus,
        opportunity: matchedOpp || undefined,
      });
    }
  } else if (canonicalStatus === 'new') {
    nextApplications = nextApplications.filter((app) => app.opportunity_id !== oppId);
  }

  state = {
    ...state,
    opportunities: nextOpportunities,
    applications: nextApplications,
  };
  emit();
  return canonicalStatus;
}

/**
 * Updates a Kanban application's stage and mirrors the change onto state.opportunities.
 */
export function updateApplicationStage(appId, newStage) {
  const normalizedStage = String(newStage || '').trim().toLowerCase();
  if (!VALID_APPLICATION_STAGES.has(normalizedStage)) {
    return null;
  }

  let targetOppId = null;
  const nextApplications = state.applications.map((app) => {
    if (app.id !== appId) return app;
    targetOppId = app.opportunity_id;
    return { ...app, status: normalizedStage };
  });

  let nextOpportunities = state.opportunities;
  if (targetOppId) {
    let oppStatus = null;
    if (normalizedStage === 'saved') oppStatus = 'kept';
    else if (normalizedStage === 'rejected') oppStatus = 'archived';
    else if (normalizedStage === 'applied' || normalizedStage === 'interview') oppStatus = normalizedStage;

    if (oppStatus) {
      nextOpportunities = state.opportunities.map((opp) =>
        opp.id === targetOppId ? { ...opp, status: oppStatus } : opp
      );
    }
  }

  state = {
    ...state,
    applications: nextApplications,
    opportunities: nextOpportunities,
  };
  emit();
  return normalizedStage;
}
