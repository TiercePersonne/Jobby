/**
 * Centralized reactive store for Jobby.
 * Views read and mutate shared state here instead of importing each other.
 */

import { FEED_SOURCES } from './utils.js';

export const VALID_VIEWS = Object.freeze(['feed', 'kanban', 'profile', 'settings']);

const STATUS_ALIASES = Object.freeze(
  Object.assign(Object.create(null), {
    favorite: 'kept',
    saved: 'kept',
  })
);

const INACTIVE_KANBAN_STATUSES = new Set(['rejected', 'archived']);
const VALID_APPLICATION_STAGES = new Set(['saved', 'applied', 'interview', 'offer', 'rejected']);
const VALID_FEED_TYPES = new Set(['all', 'offer', 'post']);
const VALID_FEED_SORTS = new Set(['date', 'score', 'region']);
const VALID_FEED_MODES = new Set(['full', 'hybrid', 'onsite']);
const VALID_FEED_REGIONS = new Set(['france', 'europe', 'usa']);
const VALID_FEED_SOURCES = new Set(FEED_SOURCES.map((s) => s.key));

export function createDefaultFeedFilters() {
  return {
    onlyNew: false,
    sources: [],
    modes: [],
    regions: [],
    type: 'all',
    sortBy: 'date',
    hideAlternance: true,
  };
}

function toggleArrayItem(list, item) {
  const idx = list.indexOf(item);
  if (idx >= 0) {
    return list.filter((entry) => entry !== item);
  }
  return [...list, item];
}

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
    feedFilters: createDefaultFeedFilters(),
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
        is_seen: Boolean(opp?.is_seen),
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

function updateFeedFilters(nextPartial) {
  state = {
    ...state,
    feedFilters: {
      ...state.feedFilters,
      ...nextPartial,
    },
  };
  emit();
  return state.feedFilters;
}

export function setOnlyNew(onlyNew) {
  const next = Boolean(onlyNew);
  if (state.feedFilters.onlyNew === next) return state.feedFilters;
  return updateFeedFilters({ onlyNew: next });
}

export function toggleFeedSource(sourceKey) {
  const key = String(sourceKey || '').trim().toLowerCase();
  if (!VALID_FEED_SOURCES.has(key)) return state.feedFilters;
  return updateFeedFilters({ sources: toggleArrayItem(state.feedFilters.sources, key) });
}

export function toggleFeedMode(modeKey) {
  const key = String(modeKey || '').trim().toLowerCase();
  if (key === 'all') {
    if (state.feedFilters.modes.length === 0) return state.feedFilters;
    return updateFeedFilters({ modes: [] });
  }
  if (!VALID_FEED_MODES.has(key)) return state.feedFilters;
  return updateFeedFilters({ modes: toggleArrayItem(state.feedFilters.modes, key) });
}

export function toggleFeedRegion(regionKey) {
  const key = String(regionKey || '').trim().toLowerCase();
  if (!VALID_FEED_REGIONS.has(key)) return state.feedFilters;
  return updateFeedFilters({ regions: toggleArrayItem(state.feedFilters.regions, key) });
}

export function setFeedType(type) {
  const key = String(type || 'all').trim().toLowerCase();
  const nextType = VALID_FEED_TYPES.has(key) ? key : 'all';
  if (state.feedFilters.type === nextType) return state.feedFilters;
  return updateFeedFilters({ type: nextType });
}

export function setFeedSort(sortBy) {
  const key = String(sortBy || 'date').trim().toLowerCase();
  const nextSort = VALID_FEED_SORTS.has(key) ? key : 'date';
  if (state.feedFilters.sortBy === nextSort) return state.feedFilters;
  return updateFeedFilters({ sortBy: nextSort });
}

export function setHideAlternance(hideAlternance) {
  const next = Boolean(hideAlternance);
  if (state.feedFilters.hideAlternance === next) return state.feedFilters;
  return updateFeedFilters({ hideAlternance: next });
}

export function resetFeedFilters() {
  state = {
    ...state,
    feedFilters: createDefaultFeedFilters(),
  };
  emit();
  return state.feedFilters;
}

/**
 * Updates an opportunity's seen flag in the store and emits to subscribers.
 */
export function syncOpportunitySeen(oppId, isSeen = true) {
  const nextSeen = Boolean(isSeen);
  let matched = false;
  let changed = false;
  const nextOpportunities = state.opportunities.map((opp) => {
    if (opp.id !== oppId) return opp;
    matched = true;
    if (opp.is_seen === nextSeen) return opp;
    changed = true;
    return { ...opp, is_seen: nextSeen };
  });

  if (!matched) return false;
  if (!changed) return true;
  state = {
    ...state,
    opportunities: nextOpportunities,
  };
  emit();
  return true;
}

/**
 * Optimistically marks all opportunities in the store as seen.
 */
export function markAllOpportunitiesSeenInState() {
  let changed = false;
  const nextOpportunities = state.opportunities.map((opp) => {
    if (opp.is_seen) return opp;
    changed = true;
    return { ...opp, is_seen: true };
  });
  if (!changed) return;
  state = {
    ...state,
    opportunities: nextOpportunities,
  };
  emit();
}

/**
 * Restores opportunity is_seen flags from a Map or plain object snapshot on rollback.
 */
export function restoreOpportunitiesSeen(seenSnapshot) {
  if (!seenSnapshot) return;
  const getSaved = (id) =>
    seenSnapshot instanceof Map
      ? seenSnapshot.get(id)
      : Object.hasOwn(seenSnapshot, id)
        ? seenSnapshot[id]
        : undefined;

  let changed = false;
  const nextOpportunities = state.opportunities.map((opp) => {
    const restored = getSaved(opp.id);
    if (restored === undefined) return opp;
    const flag = Boolean(restored);
    if (opp.is_seen === flag) return opp;
    changed = true;
    return { ...opp, is_seen: flag };
  });
  if (!changed) return;
  state = {
    ...state,
    opportunities: nextOpportunities,
  };
  emit();
}
