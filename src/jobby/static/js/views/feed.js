import {
  fetchCoverageAudit,
  fetchOpportunities,
  markAllOpportunitiesSeen,
  markOpportunitySeen,
  rankOpportunitiesByBrief,
  rescueFilteredOpportunity,
  saveProfileBrief,
  updateOpportunityStatus,
} from '../api.js';
import {
  createDefaultFeedFilters,
  getState,
  markAllOpportunitiesSeenInState,
  resetFeedFilters,
  restoreOpportunitiesSeen,
  setFeedSort,
  setFeedType,
  setHideAlternance,
  setIdealJobBrief,
  setLoadingFeed,
  setOnlyNew,
  setOpportunities,
  setProfile,
  subscribe,
  syncOpportunitySeen,
  syncOpportunityStatus,
  toggleFeedMode,
  toggleFeedRegion,
  toggleFeedSource,
} from '../state.js';
import {
  FEED_SOURCES,
  ICONS,
  detectClientRemotePolicy,
  detectOpportunityRegion,
  escapeHtml,
  formatRelativeDate,
  formatSourceLabel,
  isAlternanceOrStage,
  sanitizeUrl,
  showToast,
} from '../utils.js';

const REMOTE_LABELS = Object.freeze(
  Object.assign(Object.create(null), {
    full: '100% Remote',
    hybrid: 'Hybride',
    onsite: 'Sur site',
  })
);

const REGION_LABELS = Object.freeze(
  Object.assign(Object.create(null), {
    france: 'FR',
    europe: 'EU',
    usa: 'US',
  })
);

const REGION_SORT_RANK = Object.freeze(
  Object.assign(Object.create(null), {
    france: 0,
    europe: 1,
    usa: 2,
    other: 3,
  })
);

const STATUS_CHIPS = Object.freeze(
  Object.assign(Object.create(null), {
    applied: '<span class="jp-pill">Candidaté</span>',
    interview: '<span class="jp-pill">Entretien</span>',
  })
);

const BRIEF_EMPTY_INVITE = 'Aucun brief actif — décrivez votre poste idéal pour classer le flux.';
const BRIEF_TOGGLE_EXPAND_LABEL = 'Déplier & affiner';
const BRIEF_TOGGLE_COLLAPSE_LABEL = 'Replier';
const BRIEF_RUN_DEFAULT_LABEL = 'Re-calculer le classement Gemini';
const BRIEF_RUN_BUSY_LABEL = 'Analyse en cours...';

const REMOTE_PREF_LABELS = Object.freeze(
  Object.assign(Object.create(null), {
    full: '100% Remote',
    hybrid: 'Hybride',
    partial: 'Hybride',
    onsite: 'Sur site',
    'on-site': 'Sur site',
    on_site: 'Sur site',
  })
);

const REMOTE_PREF_SYNONYMS = Object.freeze(
  Object.assign(Object.create(null), {
    '100% Remote': [
      '100% remote',
      'full remote',
      'full-remote',
      '100% télétravail',
      '100% teletravail',
      'télétravail total',
      'teletravail total',
      'totalement à distance',
      'totalement a distance',
      'remote only',
    ],
    Hybride: ['hybride', 'hybrid', 'télétravail partiel', 'teletravail partiel'],
    'Sur site': ['sur site', 'sur-site', 'présentiel', 'presentiel', 'on-site', 'onsite'],
  })
);

let isMarkingAllSeen = false;
let briefExpanded = false;
let briefDirty = false;
let isRankingBrief = false;
let inFlightBriefSave = null;
let inFlightSubmittedBrief = null;
let lastKnownSavedBrief = '';
let pendingBriefActionButton = null;
const openAccordionIds = new Set();
const transientOnlyNewOpenIds = new Set();
const seenPendingIds = new Set();
const statusPendingRequests = new Map();
let pendingFocusTarget = null;

function resolveOpportunityDate(opp) {
  for (const candidate of [opp?.published_at, opp?.discovered_at]) {
    if (!candidate) continue;
    const ms = Date.parse(candidate);
    if (Number.isFinite(ms)) {
      return { raw: candidate, ms };
    }
  }
  return { raw: null, ms: 0 };
}

function getOpportunityTimestamp(opp) {
  return resolveOpportunityDate(opp).ms;
}

function getNumericScore(opp) {
  if (opp?.score === null || opp?.score === undefined || opp?.score === '') {
    return -Infinity;
  }
  const num = Number(opp.score);
  return Number.isFinite(num) ? num : -Infinity;
}

function getRegionRank(opp) {
  const reg = detectOpportunityRegion(opp);
  return Object.hasOwn(REGION_SORT_RANK, reg.primary) ? REGION_SORT_RANK[reg.primary] : 3;
}

export function sortOpportunities(items, sortBy = 'date') {
  const sorted = items.slice();
  if (sortBy === 'score') {
    sorted.sort((a, b) => {
      const sa = getNumericScore(a);
      const sb = getNumericScore(b);
      if (sa !== sb) return sb - sa;
      return getOpportunityTimestamp(b) - getOpportunityTimestamp(a);
    });
    return sorted;
  }

  if (sortBy === 'region') {
    sorted.sort((a, b) => {
      const rankDiff = getRegionRank(a) - getRegionRank(b);
      if (rankDiff !== 0) return rankDiff;
      return getOpportunityTimestamp(b) - getOpportunityTimestamp(a);
    });
    return sorted;
  }

  sorted.sort((a, b) => getOpportunityTimestamp(b) - getOpportunityTimestamp(a));
  return sorted;
}

export function computeFeedViewModel(snapshot = getState()) {
  const filters = snapshot.feedFilters || createDefaultFeedFilters();
  const activeOpportunities = (snapshot.opportunities || []).filter(
    (opp) => opp.status !== 'archived' && opp.status !== 'rejected'
  );

  // Counters N and Sources follow the current "Masquer alternances" toggle
  const counterBase = activeOpportunities.filter(
    (opp) => !(filters.hideAlternance && isAlternanceOrStage(opp))
  );

  let newCount = 0;
  const sourceCounts = Object.create(null);
  FEED_SOURCES.forEach(({ key }) => {
    sourceCounts[key] = 0;
  });

  counterBase.forEach((opp) => {
    if (!opp.is_seen) {
      newCount += 1;
    }
    const sourceKey = String(opp.source || '').trim().toLowerCase();
    if (Object.hasOwn(sourceCounts, sourceKey)) {
      sourceCounts[sourceKey] += 1;
    }
  });

  const filtered = activeOpportunities.filter((opp) => {
    if (filters.hideAlternance && isAlternanceOrStage(opp)) {
      return false;
    }
    if (
      filters.onlyNew &&
      Boolean(opp.is_seen) &&
      !(transientOnlyNewOpenIds.has(opp.id) && openAccordionIds.has(opp.id))
    ) {
      return false;
    }
    if (filters.sources.length > 0) {
      const sourceKey = String(opp.source || '').trim().toLowerCase();
      if (!filters.sources.includes(sourceKey)) {
        return false;
      }
    }
    if (filters.modes.length > 0) {
      const modeKey = detectClientRemotePolicy(opp);
      if (!filters.modes.includes(modeKey)) {
        return false;
      }
    }
    if (filters.regions.length > 0) {
      const region = detectOpportunityRegion(opp);
      const matchesRegion =
        (filters.regions.includes('france') && region.isFrance) ||
        (filters.regions.includes('europe') && region.isEurope) ||
        (filters.regions.includes('usa') && region.isUSA);
      if (!matchesRegion) {
        return false;
      }
    }
    const oppType = String(opp.opportunity_type || 'offer').trim().toLowerCase();
    if (filters.type === 'offer' && oppType === 'post') {
      return false;
    }
    if (filters.type === 'post' && oppType !== 'post') {
      return false;
    }
    return true;
  });

  return {
    filters,
    activeOpportunities,
    newCount,
    sourceCounts,
    filteredOpportunities: sortOpportunities(filtered, filters.sortBy),
  };
}

function renderScoreBadge(opp) {
  const rawScore = opp?.score;
  if (rawScore === null || rawScore === undefined || rawScore === '') return '';
  const numScore = Number(rawScore);
  if (!Number.isFinite(numScore) || numScore <= 0) return '';
  const rounded = Math.round(numScore);
  if (rounded <= 0) return '';

  const tierClass =
    rounded >= 85
      ? 'jp-score-badge jp-score-high'
      : rounded >= 60
        ? 'jp-score-badge jp-score-mid'
        : 'jp-score-badge jp-score-low';
  return `<span class="${tierClass}" data-score-badge>${rounded}% Match</span>`;
}

function resolveSynthesis(opp) {
  const aiMatch = opp?.raw_metadata?.ai_match;
  const matchVerdict =
    aiMatch && typeof aiMatch.verdict === 'string' ? aiMatch.verdict.trim() : '';
  if (matchVerdict) {
    const isRealGemini =
      (aiMatch.provider_used === 'gemini' || aiMatch.provider === 'gemini') &&
      aiMatch.used_fallback !== true;
    return {
      label: isRealGemini ? 'Gemini' : 'Synthèse',
      text: matchVerdict,
    };
  }

  const summary = typeof opp?.ai_summary === 'string' ? opp.ai_summary.trim() : '';
  if (summary) {
    const isRealGemini =
      opp?.raw_metadata?.ai_provider === 'gemini' &&
      opp?.raw_metadata?.used_fallback !== true;
    return {
      label: isRealGemini ? 'Gemini' : 'Synthèse',
      text: summary,
    };
  }
  return null;
}

function renderOpportunityRow(opp) {
  const oppId = opp.id || '';
  const escapedId = escapeHtml(oppId);
  const rawTitle = opp.title || 'Opportunité sans titre';
  const rawCompany = opp.company || 'Entreprise non précisée';
  const title = escapeHtml(rawTitle);
  const company = escapeHtml(rawCompany);
  const sourceLabel = escapeHtml(formatSourceLabel(opp));
  const locationText = opp.location ? escapeHtml(opp.location) : '';
  const safeUrl = sanitizeUrl(opp.url);
  const relDate = escapeHtml(formatRelativeDate(resolveOpportunityDate(opp).raw));

  const remoteKey = detectClientRemotePolicy(opp);
  const remoteLabel = Object.hasOwn(REMOTE_LABELS, remoteKey) ? REMOTE_LABELS[remoteKey] : '';
  const region = detectOpportunityRegion(opp);
  const regionLabel = Object.hasOwn(REGION_LABELS, region.primary)
    ? REGION_LABELS[region.primary]
    : '';

  const metaParts = [sourceLabel];
  if (remoteLabel) metaParts.push(escapeHtml(remoteLabel));
  if (regionLabel) metaParts.push(escapeHtml(regionLabel));
  if (relDate) metaParts.push(relDate);

  const newBadgeHtml = opp.is_seen
    ? ''
    : `<span class="jp-new-badge" data-new-badge><span class="jp-dot-sage" aria-hidden="true"></span><span>Nouveau</span></span>`;

  const statusKey = String(opp.status || 'new').trim().toLowerCase();
  const statusChip = Object.hasOwn(STATUS_CHIPS, statusKey) ? STATUS_CHIPS[statusKey] : '';
  const scoreBadgeHtml = renderScoreBadge(opp);
  const rightMetaHtml = [statusChip, scoreBadgeHtml].filter(Boolean).join('');

  const isPost = opp.opportunity_type === 'post';
  const authorParts = [];
  if (opp.company || !isPost || !opp.author_name) {
    authorParts.push(`<span class="font-medium text-[#1C1917]">${company}</span>`);
  }
  if (isPost && opp.author_name) {
    const authorDisplay = opp.author_title
      ? `${opp.author_name} — ${opp.author_title}`
      : opp.author_name;
    authorParts.push(`<span data-opp-author>Par ${escapeHtml(authorDisplay)}</span>`);
  }
  const displayLoc = locationText || (remoteLabel ? remoteLabel : 'France / Non précisée');
  authorParts.push(`<span class="inline-flex items-center gap-1 font-medium text-[#44403C]" data-opp-location>📍 ${escapeHtml(displayLoc)}</span>`);

  const synthesis = resolveSynthesis(opp);
  const synthesisHtml = synthesis
    ? `<div class="jp-ai-callout" data-ai-callout><strong>${escapeHtml(synthesis.label)} :</strong> ${escapeHtml(synthesis.text)}</div>`
    : '';

  const isOpen = openAccordionIds.has(oppId);
  const rawDescription = typeof opp.description === 'string' ? opp.description.trim() : '';
  const salaryHtml = opp.salary_raw
    ? `<p class="jp-label mb-2">Rémunération : ${escapeHtml(opp.salary_raw)}</p>`
    : '';
  const descriptionContent = rawDescription
    ? escapeHtml(rawDescription)
    : 'Aucune description détaillée fournie.';

  const isKept = statusKey === 'kept';
  const isProgressed = statusKey === 'applied' || statusKey === 'interview';
  const isStatusPending = statusPendingRequests.has(oppId);

  let keepContent;
  let keepAriaLabel;
  if (isProgressed) {
    const progressedLabel = statusKey === 'interview' ? 'Entretien' : 'Candidaté';
    keepContent = `${ICONS.check('w-3.5 h-3.5')}<span>${progressedLabel}</span>`;
    keepAriaLabel = escapeHtml(`${progressedLabel} — déjà suivi dans le Kanban : ${rawTitle}`);
  } else if (isKept) {
    keepContent = `${ICONS.check('w-3.5 h-3.5')}<span>Gardé</span>`;
    keepAriaLabel = escapeHtml(`Retirer des opportunités gardées : ${rawTitle}`);
  } else {
    keepContent = '<span>Je garde</span>';
    keepAriaLabel = escapeHtml(`Garder l'opportunité : ${rawTitle}`);
  }

  const keepDisabledAttr = isProgressed || isStatusPending ? ' disabled' : '';
  const archiveDisabledAttr = isStatusPending ? ' disabled' : '';
  const busyAttr = isStatusPending ? ' aria-busy="true"' : '';
  const archiveAriaLabel = escapeHtml(`Archiver l'opportunité : ${rawTitle}`);

  const ctaText = isPost ? 'Voir le post &rarr;' : "Voir l'offre &rarr;";
  const ariaAction = isPost ? 'Voir le post' : "Voir l'offre";
  const ctaAriaLabel = escapeHtml(`${ariaAction} : ${rawTitle} — ${rawCompany}`);

  const linkHtml = safeUrl
    ? `<a href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener noreferrer" data-action="open-link" class="jp-btn-primary shrink-0" aria-label="${ctaAriaLabel}">${ctaText}</a>`
    : '<span class="jp-label">Lien indisponible</span>';

  return `
    <article class="jp-card flex flex-col gap-3" data-opp-id="${escapedId}" data-is-seen="${opp.is_seen ? 'true' : 'false'}">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div class="jp-label flex flex-wrap items-center gap-2 min-w-0">
          ${newBadgeHtml}
          <span>${metaParts.join(' &bull; ')}</span>
        </div>
        ${rightMetaHtml ? `<div class="flex items-center gap-2 shrink-0">${rightMetaHtml}</div>` : ''}
      </div>

      <div class="min-w-0">
        <h2 class="jp-card-title break-words">${title}</h2>
        <p class="jp-body mt-0.5 break-words">
          ${authorParts.join(' &bull; ')}
        </p>
      </div>

      ${synthesisHtml}

      <details class="jp-accordion" data-opp-accordion="${escapedId}"${isOpen ? ' open data-restored-open="1"' : ''}>
        <summary>Voir le détail de l'annonce</summary>
        <div class="jp-accordion-body">${salaryHtml}${descriptionContent}</div>
      </details>

      <div class="flex flex-wrap items-center justify-between gap-2 pt-3 border-t border-[#E7E5E4]">
        <div class="flex flex-wrap items-center gap-2">
          <button type="button" data-action="toggle-keep" class="jp-btn-secondary jp-btn-keep${isKept ? ' is-kept' : ''}" aria-pressed="${isKept ? 'true' : 'false'}" aria-label="${keepAriaLabel}"${keepDisabledAttr}${busyAttr}>${keepContent}</button>
          <button type="button" data-action="archive" class="jp-btn-ghost" aria-label="${archiveAriaLabel}"${archiveDisabledAttr}${busyAttr}>Archiver</button>
          <button type="button" data-action="letter" data-modal-open="modal-letter" class="jp-btn-ghost" aria-label="Générer une lettre IA : ${rawTitle}" title="Générer une lettre de motivation">Lettre IA</button>
        </div>
        ${linkHtml}
      </div>
    </article>
  `;
}

function syncFilterBarDom(filters, newCount, sourceCounts) {
  const onlyNewBtn = document.getElementById('filter-only-new');
  const newCountEl = document.getElementById('filter-new-count');
  if (newCountEl) {
    newCountEl.textContent = String(newCount);
  }
  if (onlyNewBtn) {
    onlyNewBtn.classList.toggle('is-active', Boolean(filters.onlyNew));
    onlyNewBtn.setAttribute('aria-pressed', filters.onlyNew ? 'true' : 'false');
  }

  const markAllBtn = document.getElementById('btn-mark-all-seen');
  if (markAllBtn) {
    markAllBtn.disabled = isMarkingAllSeen || newCount === 0;
  }

  const sourcesBtn = document.getElementById('btn-sources-popover');
  const sourcesActiveCountEl = document.getElementById('sources-active-count');
  if (sourcesBtn) {
    sourcesBtn.classList.toggle('is-active', filters.sources.length > 0);
  }
  if (sourcesActiveCountEl) {
    if (filters.sources.length > 0) {
      sourcesActiveCountEl.textContent = `(${filters.sources.length})`;
      sourcesActiveCountEl.classList.remove('hidden');
    } else {
      sourcesActiveCountEl.textContent = '';
      sourcesActiveCountEl.classList.add('hidden');
    }
  }
  if (isSourcesPopoverOpen()) {
    positionSourcesPopover();
  }

  document.querySelectorAll('[data-filter-source]').forEach((checkbox) => {
    const key = String(checkbox.getAttribute('data-filter-source') || '').toLowerCase();
    checkbox.checked = filters.sources.includes(key);
  });

  document.querySelectorAll('[data-source-count]').forEach((countBadge) => {
    const key = String(countBadge.getAttribute('data-source-count') || '').toLowerCase();
    countBadge.textContent = String(Object.hasOwn(sourceCounts, key) ? sourceCounts[key] : 0);
  });

  document.querySelectorAll('[data-filter-mode]').forEach((btn) => {
    const mode = String(btn.getAttribute('data-filter-mode') || '').toLowerCase();
    const isActive = mode === 'all' ? filters.modes.length === 0 : filters.modes.includes(mode);
    btn.classList.toggle('is-active', isActive);
    btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
  });

  document.querySelectorAll('[data-filter-region]').forEach((btn) => {
    const region = String(btn.getAttribute('data-filter-region') || '').toLowerCase();
    const isActive = filters.regions.includes(region);
    btn.classList.toggle('is-active', isActive);
    btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
  });

  document.querySelectorAll('[data-filter-type]').forEach((btn) => {
    const type = String(btn.getAttribute('data-filter-type') || '').toLowerCase();
    const isActive = filters.type === type;
    btn.classList.toggle('is-active', isActive);
    btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
  });

  const sortSelect = document.getElementById('filter-sort');
  if (sortSelect && sortSelect.value !== filters.sortBy) {
    sortSelect.value = filters.sortBy;
  }

  const hideAltCheckbox = document.getElementById('filter-hide-alternance');
  if (hideAltCheckbox) {
    hideAltCheckbox.checked = Boolean(filters.hideAlternance);
  }
}

function captureInlineFeedFocus(listEl) {
  const activeEl = document.activeElement;
  if (!(activeEl instanceof HTMLElement) || !listEl.contains(activeEl)) {
    return null;
  }
  const oppId = activeEl.closest('[data-opp-id]')?.getAttribute('data-opp-id') || null;
  if (!oppId) return null;

  if (activeEl.tagName === 'SUMMARY') {
    return { type: 'summary', oppId };
  }
  const actionEl = activeEl.closest('[data-action]');
  const action = actionEl?.getAttribute('data-action');
  if (action) {
    return { type: 'action', oppId, action };
  }
  return null;
}

function restoreFeedFocus(listEl, inlineFocus) {
  if (inlineFocus?.type === 'summary') {
    const matchingCard = Array.from(listEl.children).find(
      (el) => el.getAttribute('data-opp-id') === inlineFocus.oppId
    );
    const summaryEl = matchingCard?.querySelector('details.jp-accordion > summary');
    if (summaryEl instanceof HTMLElement) {
      summaryEl.focus({ preventScroll: true });
    }
    return;
  }

  let target = null;
  if (pendingFocusTarget && !statusPendingRequests.has(pendingFocusTarget.oppId)) {
    target = pendingFocusTarget;
    pendingFocusTarget = null;
  } else if (inlineFocus?.type === 'action' && !statusPendingRequests.has(inlineFocus.oppId)) {
    target = inlineFocus;
  }

  if (!target) return;

  const matchingCard = Array.from(listEl.children).find(
    (el) => el.getAttribute('data-opp-id') === target.oppId
  );
  const actionBtn = matchingCard?.querySelector(`[data-action="${target.action}"]`);
  if (actionBtn instanceof HTMLElement && !actionBtn.hasAttribute('disabled')) {
    actionBtn.focus({ preventScroll: true });
    return;
  }

  document.getElementById('filter-only-new')?.focus({ preventScroll: true });
}

export function renderFeedView(snapshot = getState()) {
  const listEl = document.getElementById('feed-list');
  const countEl = document.getElementById('feed-count');
  const model = computeFeedViewModel(snapshot);

  syncBriefBoxDom(snapshot);
  syncFilterBarDom(model.filters, model.newCount, model.sourceCounts);

  if (!listEl) return;

  const totalActive = model.activeOpportunities.length;
  const visibleCount = model.filteredOpportunities.length;

  if (countEl) {
    if (snapshot.isLoadingFeed && totalActive === 0) {
      countEl.textContent = 'Chargement...';
    } else {
      const baseLabel = `${visibleCount} opportunité${visibleCount > 1 ? 's' : ''}`;
      countEl.textContent =
        totalActive > 0 && visibleCount !== totalActive
          ? `${baseLabel} sur ${totalActive}`
          : baseLabel;
    }
  }

  const inlineFocus = captureInlineFeedFocus(listEl);

  if (snapshot.isLoadingFeed && totalActive === 0) {
    listEl.innerHTML = `
      <div class="jp-card jp-body text-center py-8" role="status">
        Chargement des opportunités...
      </div>
    `;
    return;
  }

  if (snapshot.feedError && totalActive === 0) {
    listEl.innerHTML = `
      <div class="jp-card flex flex-col items-center gap-3 py-8 text-center" role="status">
        <p class="jp-body text-[#991B1B]">${escapeHtml(snapshot.feedError)}</p>
        <button type="button" id="btn-retry-feed" class="jp-btn-secondary">Réessayer</button>
      </div>
    `;
    restoreFeedFocus(listEl, inlineFocus);
    return;
  }

  if (totalActive === 0) {
    listEl.innerHTML = `
      <div class="jp-card jp-body text-center py-8" role="status">
        Aucune opportunité disponible pour le moment.
      </div>
    `;
    restoreFeedFocus(listEl, inlineFocus);
    return;
  }

  if (visibleCount === 0) {
    listEl.innerHTML = `
      <div class="jp-card flex flex-col items-center gap-3 py-8 text-center" role="status">
        <p class="jp-body">Aucune opportunité ne correspond à ces critères.</p>
        <button type="button" id="btn-reset-filters" class="jp-btn-secondary">Réinitialiser les filtres</button>
      </div>
    `;
    restoreFeedFocus(listEl, inlineFocus);
    return;
  }

  listEl.innerHTML = model.filteredOpportunities.map(renderOpportunityRow).join('');
  restoreFeedFocus(listEl, inlineFocus);
}

async function reloadFeed() {
  setLoadingFeed(true, null);
  try {
    const opportunities = await fetchOpportunities({ limit: 100 });
    setOpportunities(opportunities);
  } catch (err) {
    setLoadingFeed(false, err?.message || 'Impossible de charger les opportunités.');
  }
}

function syncMarkAllSeenDisabled(newCount = computeFeedViewModel(getState()).newCount) {
  const markAllBtn = document.getElementById('btn-mark-all-seen');
  if (markAllBtn) {
    markAllBtn.disabled = isMarkingAllSeen || newCount === 0;
  }
}

async function handleMarkAllSeen() {
  if (isMarkingAllSeen) return;

  const opportunities = getState().opportunities || [];
  const prevSeenMap = new Map(opportunities.map((opp) => [opp.id, Boolean(opp.is_seen)]));

  isMarkingAllSeen = true;
  transientOnlyNewOpenIds.clear();
  syncMarkAllSeenDisabled(0);
  markAllOpportunitiesSeenInState();

  try {
    await markAllOpportunitiesSeen();
  } catch {
    restoreOpportunitiesSeen(prevSeenMap);
  } finally {
    isMarkingAllSeen = false;
    syncMarkAllSeenDisabled();
  }
}

function isSourcesPopoverOpen() {
  const popover = document.getElementById('popover-sources');
  if (!popover || popover.classList.contains('hidden')) return false;
  return !popover.closest('[data-view-panel].hidden');
}

function positionSourcesPopover() {
  const popover = document.getElementById('popover-sources');
  if (!popover || popover.classList.contains('hidden')) return;

  popover.style.left = '0px';
  popover.style.right = 'auto';

  const rect = popover.getBoundingClientRect();
  const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
  const margin = 12;
  const maxRight = viewportWidth - margin;

  if (rect.right > maxRight) {
    const shift = Math.min(rect.left - margin, rect.right - maxRight);
    popover.style.left = `${-Math.max(0, shift)}px`;
  } else if (rect.left < margin) {
    popover.style.left = `${margin - rect.left}px`;
  }
}

function setSourcesPopoverOpen(open, { restoreFocus = false } = {}) {
  const popover = document.getElementById('popover-sources');
  const triggerBtn = document.getElementById('btn-sources-popover');
  if (!popover || !triggerBtn) return;

  popover.classList.toggle('hidden', !open);
  triggerBtn.setAttribute('aria-expanded', open ? 'true' : 'false');

  if (open) {
    positionSourcesPopover();
  } else if (restoreFocus) {
    triggerBtn.focus();
  }
}

function bindFilterBarEvents() {
  const filterBar = document.getElementById('feed-filter-bar');
  if (!filterBar) return;

  const onlyNewBtn = document.getElementById('filter-only-new');
  if (onlyNewBtn) {
    onlyNewBtn.addEventListener('click', () => {
      setOnlyNew(!getState().feedFilters.onlyNew);
    });
  }

  const markAllBtn = document.getElementById('btn-mark-all-seen');
  if (markAllBtn) {
    markAllBtn.addEventListener('click', () => {
      handleMarkAllSeen();
    });
  }

  const sourcesBtn = document.getElementById('btn-sources-popover');
  if (sourcesBtn) {
    sourcesBtn.addEventListener('click', () => {
      setSourcesPopoverOpen(!isSourcesPopoverOpen());
    });
  }

  filterBar.addEventListener('change', (event) => {
    const sourceCheckbox = event.target.closest('[data-filter-source]');
    if (sourceCheckbox) {
      toggleFeedSource(sourceCheckbox.getAttribute('data-filter-source'));
      return;
    }

    if (event.target.id === 'filter-sort') {
      setFeedSort(event.target.value);
      return;
    }

    if (event.target.id === 'filter-hide-alternance') {
      setHideAlternance(event.target.checked);
    }
  });

  filterBar.addEventListener('click', (event) => {
    const modeBtn = event.target.closest('[data-filter-mode]');
    if (modeBtn) {
      toggleFeedMode(modeBtn.getAttribute('data-filter-mode'));
      return;
    }

    const regionBtn = event.target.closest('[data-filter-region]');
    if (regionBtn) {
      toggleFeedRegion(regionBtn.getAttribute('data-filter-region'));
      return;
    }

    const typeBtn = event.target.closest('[data-filter-type]');
    if (typeBtn) {
      setFeedType(typeBtn.getAttribute('data-filter-type'));
    }
  });

  document.addEventListener('click', (event) => {
    if (!isSourcesPopoverOpen()) return;
    const wrapper = document.getElementById('filter-sources-wrapper');
    if (wrapper && !wrapper.contains(event.target)) {
      setSourcesPopoverOpen(false);
    }
  });

  window.addEventListener('resize', () => {
    if (isSourcesPopoverOpen()) {
      positionSourcesPopover();
    }
  });
}

function getBriefElements() {
  return {
    box: document.getElementById('brief-box'),
    toggleBtn: document.getElementById('brief-toggle-btn'),
    toggleText: document.getElementById('brief-toggle-text'),
    dot: document.getElementById('brief-dot'),
    previewText: document.getElementById('brief-preview-text'),
    drawer: document.getElementById('brief-drawer'),
    input: document.getElementById('brief-input'),
    btnPrefill: document.getElementById('btn-prefill'),
    btnRunGemini: document.getElementById('btn-run-gemini'),
  };
}

function isBriefDrawerOpen() {
  const drawer = document.getElementById('brief-drawer');
  if (!drawer || drawer.classList.contains('hidden')) return false;
  return !drawer.closest('[data-view-panel].hidden');
}

function getTrimmedProfileBrief(snapshot) {
  const raw = snapshot?.profile?.ideal_job_brief;
  return typeof raw === 'string' ? raw.trim() : '';
}

function formatBriefPreview(briefText) {
  if (!briefText) return BRIEF_EMPTY_INVITE;
  const collapsed = briefText.replace(/\s+/g, ' ').trim();
  return collapsed ? `« ${collapsed} »` : BRIEF_EMPTY_INVITE;
}

function syncBriefActionButtons() {
  const { input, btnPrefill, btnRunGemini } = getBriefElements();
  if (input) {
    input.readOnly = isRankingBrief;
  }
  if (btnPrefill) {
    btnPrefill.disabled = isRankingBrief;
  }
  if (btnRunGemini) {
    btnRunGemini.disabled = isRankingBrief;
    btnRunGemini.textContent = isRankingBrief ? BRIEF_RUN_BUSY_LABEL : BRIEF_RUN_DEFAULT_LABEL;
    if (isRankingBrief) {
      btnRunGemini.setAttribute('aria-busy', 'true');
    } else {
      btnRunGemini.removeAttribute('aria-busy');
    }
  }
}

function applyBriefDrawerVisibility(open) {
  const { toggleBtn, toggleText, drawer } = getBriefElements();
  if (!toggleBtn || !drawer) return false;
  drawer.classList.toggle('hidden', !open);
  toggleBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
  if (toggleText) {
    toggleText.textContent = open ? BRIEF_TOGGLE_COLLAPSE_LABEL : BRIEF_TOGGLE_EXPAND_LABEL;
  }
  return true;
}

function syncBriefBoxDom(snapshot = getState()) {
  const { dot, previewText, input } = getBriefElements();
  if (!applyBriefDrawerVisibility(briefExpanded)) return;

  const hasProfileProp = Boolean(snapshot && Object.hasOwn(snapshot, 'profile'));
  const activeBrief = hasProfileProp ? getTrimmedProfileBrief(snapshot) : lastKnownSavedBrief;
  if (hasProfileProp) {
    lastKnownSavedBrief = activeBrief;
    if (input && input.value.trim() === activeBrief) {
      briefDirty = false;
    }
  }

  if (dot) {
    dot.classList.toggle('is-active', Boolean(activeBrief));
  }
  if (previewText) {
    previewText.textContent = formatBriefPreview(activeBrief);
  }
  if (
    input &&
    !briefDirty &&
    !inFlightBriefSave &&
    !isRankingBrief &&
    input.value.trim() !== activeBrief
  ) {
    input.value = activeBrief;
  }

  syncBriefActionButtons();
}

function isBriefModified() {
  const input = document.getElementById('brief-input');
  if (!input) return false;
  return briefDirty || input.value.trim() !== lastKnownSavedBrief;
}

async function saveBriefIfDirty() {
  while (inFlightBriefSave) {
    const pendingVal = inFlightSubmittedBrief;
    const prevRes = await inFlightBriefSave;
    const currentVal = document.getElementById('brief-input')?.value.trim() ?? '';
    if (currentVal === pendingVal && prevRes !== null) {
      return null;
    }
  }

  const input = document.getElementById('brief-input');
  if (!input || !isBriefModified()) {
    briefDirty = false;
    return null;
  }

  const submittedTrimmed = input.value.trim();
  inFlightSubmittedBrief = submittedTrimmed;
  const reqPromise = (async () => {
    try {
      const res = await saveProfileBrief(submittedTrimmed);
      const savedText = typeof res?.ideal_job_brief === 'string' ? res.ideal_job_brief.trim() : '';
      lastKnownSavedBrief = savedText;
      if (input.value.trim() === submittedTrimmed) {
        briefDirty = false;
      }
      const nextBrief = res?.ideal_job_brief ?? (submittedTrimmed || null);
      if (
        res?.profile &&
        typeof res.profile === 'object' &&
        !Array.isArray(res.profile) &&
        !getState().profile?.user_id
      ) {
        setProfile({ ...res.profile, ideal_job_brief: nextBrief }, null);
      } else {
        setIdealJobBrief(nextBrief);
      }
      syncBriefBoxDom(getState());
      return res;
    } catch {
      briefDirty = input.value.trim() !== lastKnownSavedBrief;
      return null;
    } finally {
      if (inFlightBriefSave === reqPromise) {
        inFlightBriefSave = null;
        inFlightSubmittedBrief = null;
      }
    }
  })();

  inFlightBriefSave = reqPromise;
  return reqPromise;
}

function setBriefDrawerOpen(open, { restoreFocus = false, skipAutoSave = false } = {}) {
  const { toggleBtn, drawer, input } = getBriefElements();
  if (!toggleBtn || !drawer) return;

  const nextOpen = Boolean(open);
  if (briefExpanded === nextOpen) return;

  const shouldAutoSave = !nextOpen && !skipAutoSave && isBriefModified();
  briefExpanded = nextOpen;

  if (!nextOpen && (restoreFocus || drawer.contains(document.activeElement))) {
    toggleBtn.focus();
  }

  applyBriefDrawerVisibility(nextOpen);

  if (nextOpen) {
    if (input && !briefDirty && !inFlightBriefSave && !isRankingBrief) {
      const activeBrief = getTrimmedProfileBrief(getState());
      if (input.value.trim() !== activeBrief) {
        input.value = activeBrief;
      }
    }
    input?.focus();
  } else if (shouldAutoSave) {
    saveBriefIfDirty();
  }
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function foldDiacritics(value) {
  return String(value || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '');
}

function containsBriefTerm(normalizedExisting, term) {
  const foldedHaystack = foldDiacritics(normalizedExisting);
  const needle = foldDiacritics(term).replace(/\s+/g, ' ').trim();
  if (!needle) return true;
  const pattern = new RegExp(
    `(^|[^\\p{L}\\p{N}_+#])${escapeRegExp(needle)}(?=$|[^\\p{L}\\p{N}_+#])`,
    'iu'
  );
  return pattern.test(foldedHaystack);
}

function formatRemotePreferenceLabel(rawRemote) {
  const cleaned = String(rawRemote || '').trim();
  if (!cleaned) return '';
  const key = cleaned.toLowerCase();
  if (key === 'unknown' || key === 'none') return '';
  return Object.hasOwn(REMOTE_PREF_LABELS, key) ? REMOTE_PREF_LABELS[key] : cleaned;
}

function isRemotePreferencePresent(normalizedExisting, remoteLabel) {
  if (!remoteLabel) return true;
  if (containsBriefTerm(normalizedExisting, remoteLabel)) return true;
  const synonyms = REMOTE_PREF_SYNONYMS[remoteLabel];
  if (!Array.isArray(synonyms)) return false;
  return synonyms.some((syn) => containsBriefTerm(normalizedExisting, syn));
}

function uniqueCleanList(items) {
  if (!Array.isArray(items)) return [];
  const seen = new Set();
  const out = [];
  items.forEach((raw) => {
    const cleaned = String(raw ?? '').replace(/\s+/g, ' ').trim();
    if (!cleaned) return;
    const key = cleaned.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    out.push(cleaned);
  });
  return out;
}

/**
 * Builds or appends profile roles, skills, remote preference and bio into the Brief IA without duplication or overwriting.
 */
export function buildNonDestructiveBriefWithProfile(existingText, profile) {
  if (!profile || typeof profile !== 'object') {
    return { status: 'empty_profile', text: existingText || '' };
  }

  const roles = uniqueCleanList(profile.target_roles);
  const skills = uniqueCleanList(profile.skills);
  const remoteLabel = formatRemotePreferenceLabel(profile.remote_preference);
  const bio = typeof profile.bio === 'string' ? profile.bio.replace(/\s+/g, ' ').trim() : '';

  if (roles.length === 0 && skills.length === 0 && !remoteLabel && !bio) {
    return { status: 'empty_profile', text: existingText || '' };
  }

  const currentRaw = typeof existingText === 'string' ? existingText : '';
  const currentTrimmed = currentRaw.trim();
  const normalizedExisting = currentTrimmed.replace(/\s+/g, ' ');

  const missingRoles = currentTrimmed
    ? roles.filter((role) => !containsBriefTerm(normalizedExisting, role))
    : roles;
  const missingSkills = currentTrimmed
    ? skills.filter((skill) => !containsBriefTerm(normalizedExisting, skill))
    : skills;
  const includeRemote = Boolean(
    remoteLabel && (!currentTrimmed || !isRemotePreferencePresent(normalizedExisting, remoteLabel))
  );
  const foldedExistingLower = foldDiacritics(normalizedExisting).toLowerCase();
  const bioCore = bio.replace(/[.!?;,]+$/g, '').trim();
  const foldedBioLower = foldDiacritics(bioCore || bio).toLowerCase();
  const includeBio = Boolean(
    bio &&
      (!currentTrimmed ||
        (!containsBriefTerm(normalizedExisting, bioCore || bio) &&
          !(foldedBioLower.length >= 12 && foldedExistingLower.includes(foldedBioLower))))
  );

  const additions = [];
  if (missingRoles.length > 0) {
    additions.push(`Rôles visés : ${missingRoles.join(', ')}.`);
  }
  if (missingSkills.length > 0) {
    additions.push(`Compétences : ${missingSkills.join(', ')}.`);
  }
  if (includeRemote) {
    additions.push(`Mode souhaité : ${remoteLabel}.`);
  }
  if (includeBio) {
    const bioSentence = /[.!?]$/.test(bio) ? bio : `${bio}.`;
    additions.push(`Parcours : ${bioSentence}`);
  }

  if (additions.length === 0) {
    return { status: 'already_present', text: currentRaw };
  }

  const nextText = currentTrimmed
    ? `${currentRaw.trimEnd()}\n${additions.join('\n')}`
    : additions.join('\n');
  return { status: currentTrimmed ? 'appended' : 'filled', text: nextText };
}

function handlePrefillFromProfile() {
  if (isRankingBrief) return;
  const { input } = getBriefElements();
  if (!input) return;

  const { profile, profileError } = getState();
  if (!profile && !profileError) {
    showToast('Profil en cours de chargement, veuillez patienter.', 'info');
    return;
  }
  const res = buildNonDestructiveBriefWithProfile(input.value, profile);
  if (res.status === 'empty_profile') {
    showToast(
      profileError
        ? 'Profil indisponible pour le pré-remplissage.'
        : 'Aucune information de profil disponible à importer.',
      'info'
    );
    return;
  }
  if (res.status === 'already_present') {
    showToast('Les éléments du profil sont déjà présents dans le brief.', 'info');
    return;
  }

  input.value = res.text;
  briefDirty = input.value.trim() !== lastKnownSavedBrief;
  input.focus();
}

/**
 * Merges /api/ai/rank-by-brief results into cached opportunities if full feed refresh fails.
 */
export function mergeRankingResultsIntoOpportunities(opportunities, rankResult) {
  const currentList = Array.isArray(opportunities) ? opportunities : [];
  const results = Array.isArray(rankResult?.results) ? rankResult.results : [];
  if (results.length === 0) return currentList;

  const byId = new Map();
  results.forEach((entry) => {
    if (entry && typeof entry.id === 'string' && entry.id) {
      byId.set(entry.id, entry);
    }
  });

  return currentList.map((opp) => {
    const ranked = byId.get(opp?.id);
    if (!ranked) return opp;

    const numericScore = Number(ranked.score);
    const hasValidScore =
      (typeof ranked.score === 'number' ||
        (typeof ranked.score === 'string' && ranked.score.trim() !== '')) &&
      Number.isFinite(numericScore);
    const nextScore = hasValidScore ? numericScore : opp.score;
    const verdict = typeof ranked.verdict === 'string' ? ranked.verdict.trim() : '';
    const existingMeta =
      opp.raw_metadata && typeof opp.raw_metadata === 'object' && !Array.isArray(opp.raw_metadata)
        ? opp.raw_metadata
        : {};

    return {
      ...opp,
      score: nextScore,
      score_reasons: verdict ? [verdict] : opp.score_reasons,
      raw_metadata: {
        ...existingMeta,
        ai_match: {
          ...ranked,
          provider_used: ranked.provider_used ?? rankResult?.provider_used,
          used_fallback: ranked.used_fallback ?? rankResult?.used_fallback,
          fallback_reason: ranked.fallback_reason ?? rankResult?.fallback_reason ?? null,
        },
      },
    };
  });
}

function parseFiniteCount(raw) {
  if (typeof raw === 'number') {
    return Number.isFinite(raw) ? raw : null;
  }
  if (typeof raw === 'string' && raw.trim() !== '') {
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function buildRankingToast(rankResult, refreshFailed) {
  const resultsList = Array.isArray(rankResult?.results) ? rankResult.results : [];
  const totalEvaluatedNum = parseFiniteCount(rankResult?.total_evaluated);
  const totalEvaluated = totalEvaluatedNum !== null ? totalEvaluatedNum : resultsList.length;
  const updatedCountNum = parseFiniteCount(rankResult?.updated_count);
  const count = updatedCountNum !== null ? updatedCountNum : totalEvaluated;
  const countLabel = `${count} opportunité${count > 1 ? 's' : ''} classée${count > 1 ? 's' : ''}`;
  const refreshSuffix = refreshFailed
    ? ' (rafraîchissement complet indisponible, vue locale mise à jour)'
    : '';

  if (totalEvaluated === 0 && resultsList.length === 0) {
    const emptyMsg =
      typeof rankResult?.message === 'string' && rankResult.message.trim()
        ? rankResult.message.trim().replace(/\.$/, '')
        : 'Aucune opportunité active à classer';
    return { message: `${emptyMsg}.`, variant: 'info' };
  }

  const providerUsed = String(rankResult?.provider_used || '').trim().toLowerCase();
  const hasFallbackItem = resultsList.some((item) => {
    const itemProvider = String(item?.provider_used || '').trim().toLowerCase();
    return item?.used_fallback === true || (itemProvider !== '' && itemProvider !== 'gemini');
  });
  const hasGeminiItem = resultsList.some((item) => {
    const itemProvider = String(item?.provider_used || '').trim().toLowerCase();
    return itemProvider === 'gemini' && item?.used_fallback !== true;
  });
  const usedFallback = Boolean(rankResult?.used_fallback) || hasFallbackItem;
  const topReason =
    typeof rankResult?.fallback_reason === 'string' ? rankResult.fallback_reason.trim() : '';
  const firstItemReason =
    resultsList
      .map((item) => (typeof item?.fallback_reason === 'string' ? item.fallback_reason.trim() : ''))
      .find(Boolean) || '';
  const rawReason = topReason || firstItemReason;
  const reasonText = rawReason.length > 140 ? `${rawReason.slice(0, 137)}...` : rawReason;

  if (providerUsed === 'gemini' && !usedFallback) {
    return {
      message: `Analyse Gemini terminée : ${countLabel}${refreshSuffix}.`,
      variant: refreshFailed ? 'info' : 'success',
    };
  }

  if (providerUsed === 'gemini' && usedFallback && hasGeminiItem) {
    const reasonPart = reasonText ? ` — ${reasonText}` : '';
    return {
      message: `Classement partiel Gemini avec repli heuristique local (${countLabel})${reasonPart}${refreshSuffix}.`,
      variant: 'info',
    };
  }

  if (usedFallback) {
    const reasonPart = reasonText ? ` — ${reasonText}` : '';
    return {
      message: `Repli heuristique local appliqué (${countLabel})${reasonPart}${refreshSuffix}.`,
      variant: 'info',
    };
  }

  const engineLabel = providerUsed && providerUsed !== 'heuristic' ? providerUsed : 'heuristique local';
  return {
    message: `Classement ${engineLabel} terminé : ${countLabel}${refreshSuffix}.`,
    variant: 'info',
  };
}

async function handleRankByBrief() {
  if (isRankingBrief) return;
  const { input } = getBriefElements();
  const briefText = (input?.value || '').trim();

  if (!briefText) {
    showToast('Veuillez saisir un brief avant de lancer le classement.', 'error');
    input?.focus();
    return;
  }

  isRankingBrief = true;
  syncBriefActionButtons();

  try {
    if (inFlightBriefSave) {
      await inFlightBriefSave.catch(() => null);
    }

    let rankResult;
    try {
      rankResult = await rankOpportunitiesByBrief(briefText, 100);
    } catch {
      // Error toast emitted by api.js; keep drawer open so user can edit and retry.
      return;
    }

    lastKnownSavedBrief = briefText;
    if (input && input.value.trim() === briefText) {
      briefDirty = false;
    }
    setIdealJobBrief(briefText);

    let nextOpportunities;
    let refreshFailed = false;
    try {
      nextOpportunities = await fetchOpportunities({ limit: 100 });
    } catch {
      refreshFailed = true;
      nextOpportunities = mergeRankingResultsIntoOpportunities(
        getState().opportunities,
        rankResult
      );
    }

    setBriefDrawerOpen(false, { skipAutoSave: true, restoreFocus: true });
    setFeedSort('score');
    setOpportunities(nextOpportunities);

    const { message, variant } = buildRankingToast(rankResult, refreshFailed);
    showToast(message, variant);
  } finally {
    isRankingBrief = false;
    syncBriefActionButtons();
  }
}

function bindBriefDrawerEvents() {
  const { toggleBtn, input, btnPrefill, btnRunGemini } = getBriefElements();
  const briefActionButtons = [toggleBtn, btnPrefill, btnRunGemini].filter(Boolean);

  briefActionButtons.forEach((btn) => {
    btn.addEventListener('pointerdown', () => {
      pendingBriefActionButton = btn;
    });
  });

  const settlePendingActionPointer = (event) => {
    const activeBtn = pendingBriefActionButton;
    if (!activeBtn) return;
    const isCancelled =
      event.type === 'pointercancel' ||
      !(event.target instanceof Node && activeBtn.contains(event.target));
    pendingBriefActionButton = null;
    if (isCancelled && isBriefDrawerOpen() && document.activeElement !== input) {
      saveBriefIfDirty();
    }
  };
  document.addEventListener('pointerup', settlePendingActionPointer);
  document.addEventListener('pointercancel', settlePendingActionPointer);

  if (toggleBtn) {
    toggleBtn.addEventListener('click', () => {
      setBriefDrawerOpen(!isBriefDrawerOpen());
    });
  }

  if (input) {
    input.addEventListener('input', () => {
      briefDirty = input.value.trim() !== lastKnownSavedBrief;
    });

    input.addEventListener('blur', (event) => {
      const nextTarget = event.relatedTarget;
      if (pendingBriefActionButton || briefActionButtons.includes(nextTarget)) {
        return;
      }
      saveBriefIfDirty();
    });
  }

  if (btnPrefill) {
    btnPrefill.addEventListener('click', () => {
      handlePrefillFromProfile();
    });
  }

  if (btnRunGemini) {
    btnRunGemini.addEventListener('click', () => {
      handleRankByBrief();
    });
  }
}

function bindFeedEscapeKey() {
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    if (getState().activeModal !== null) return;

    if (isSourcesPopoverOpen()) {
      event.preventDefault();
      setSourcesPopoverOpen(false, { restoreFocus: true });
      return;
    }

    if (isBriefDrawerOpen()) {
      event.preventDefault();
      setBriefDrawerOpen(false, { restoreFocus: true });
    }
  });
}

async function markSeenExplicitly(oppId) {
  if (!oppId || seenPendingIds.has(oppId)) return;
  const opp = getState().opportunities.find((o) => o.id === oppId);
  if (!opp || opp.is_seen) return;

  seenPendingIds.add(oppId);
  syncOpportunitySeen(oppId, true);
  try {
    await markOpportunitySeen(oppId, true);
  } catch {
    const currentOpp = getState().opportunities.find((o) => o.id === oppId);
    if (currentOpp && currentOpp.status === 'new') {
      syncOpportunitySeen(oppId, false);
    }
  } finally {
    seenPendingIds.delete(oppId);
  }
}

function recordActionFocusIfActive(oppId, action) {
  const activeEl = document.activeElement;
  if (!(activeEl instanceof HTMLElement)) return;
  const activeCard = activeEl.closest('[data-opp-id]');
  if (activeCard?.getAttribute('data-opp-id') !== oppId) return;
  if (activeEl.closest('[data-action]')?.getAttribute('data-action') === action) {
    pendingFocusTarget = { oppId, action };
  }
}

async function handleToggleKeep(oppId) {
  if (!oppId || statusPendingRequests.has(oppId)) return;
  const opp = getState().opportunities.find((o) => o.id === oppId);
  if (!opp) return;

  const prevStatus = opp.status || 'new';
  if (prevStatus === 'applied' || prevStatus === 'interview') return;

  const prevSeen = Boolean(opp.is_seen);
  const existingApp = getState().applications.find((a) => a.opportunity_id === oppId);
  const prevApp = existingApp ? { ...existingApp } : null;
  const nextStatus = prevStatus === 'kept' ? 'new' : 'kept';

  recordActionFocusIfActive(oppId, 'toggle-keep');

  let resolvePromise;
  const reqPromise = new Promise((resolve) => {
    resolvePromise = resolve;
  });
  statusPendingRequests.set(oppId, reqPromise);
  syncOpportunityStatus(oppId, nextStatus);

  try {
    const res = await updateOpportunityStatus(oppId, nextStatus);
    if (res && typeof res.is_seen === 'boolean') {
      syncOpportunitySeen(oppId, res.is_seen);
    }
    if (nextStatus === 'new' && !res?.is_seen) {
      try {
        await markOpportunitySeen(oppId, true);
        syncOpportunitySeen(oppId, true);
      } catch {
        // Error toast emitted by api.js; keep existing is_seen state unchanged.
      }
    }
  } catch {
    syncOpportunityStatus(oppId, prevStatus, {
      isSeen: prevSeen,
      restoreApplication: prevApp,
    });
  } finally {
    statusPendingRequests.delete(oppId);
    renderFeedView(getState());
    resolvePromise();
  }
}

async function handleUndoArchive(oppId, prevStatus, prevSeen, prevApp, inFlightArchive) {
  if (inFlightArchive) {
    const archiveSucceeded = await inFlightArchive;
    if (!archiveSucceeded) return;
  }

  const targetStatus =
    prevStatus === 'archived' || prevStatus === 'rejected' ? 'new' : prevStatus;
  const restoreAppArg =
    prevApp !== null ? prevApp : targetStatus === 'new' ? null : undefined;

  let resolveUndo;
  const undoPromise = new Promise((resolve) => {
    resolveUndo = resolve;
  });
  statusPendingRequests.set(oppId, undoPromise);

  syncOpportunityStatus(oppId, targetStatus, {
    isSeen: prevSeen,
    restoreApplication: restoreAppArg,
  });

  try {
    const res = await updateOpportunityStatus(oppId, targetStatus);
    if (!prevSeen) {
      await markOpportunitySeen(oppId, false);
      syncOpportunitySeen(oppId, false);
    } else if (res && typeof res.is_seen === 'boolean') {
      syncOpportunitySeen(oppId, res.is_seen);
    }
  } catch {
    syncOpportunityStatus(oppId, 'archived', {
      isSeen: true,
      restoreApplication: prevApp ? { ...prevApp, status: 'rejected' } : undefined,
    });
  } finally {
    statusPendingRequests.delete(oppId);
    renderFeedView(getState());
    resolveUndo();
  }
}

async function handleArchiveOpportunity(oppId) {
  if (!oppId || statusPendingRequests.has(oppId)) return;
  const opp = getState().opportunities.find((o) => o.id === oppId);
  if (!opp) return;

  const prevStatus = opp.status || 'new';
  const prevSeen = Boolean(opp.is_seen);
  const existingApp = getState().applications.find((a) => a.opportunity_id === oppId);
  const prevApp = existingApp ? { ...existingApp } : null;
  const wasAccordionOpen = openAccordionIds.has(oppId);
  const wasTransientOnlyNew = transientOnlyNewOpenIds.has(oppId);

  recordActionFocusIfActive(oppId, 'archive');
  openAccordionIds.delete(oppId);
  transientOnlyNewOpenIds.delete(oppId);

  let resolveReq;
  const reqTracker = new Promise((resolve) => {
    resolveReq = resolve;
  });
  statusPendingRequests.set(oppId, reqTracker);
  syncOpportunityStatus(oppId, 'archived');

  const archiveToast = showToast('Opportunité archivée.', 'info', {
    actionLabel: 'Annuler',
    onAction: () => handleUndoArchive(oppId, prevStatus, prevSeen, prevApp, reqTracker),
  });

  let ok = false;
  try {
    await updateOpportunityStatus(oppId, 'archived');
    ok = true;
  } catch {
    if (archiveToast && archiveToast.isConnected) {
      archiveToast.remove();
    }
    if (wasAccordionOpen) openAccordionIds.add(oppId);
    if (wasTransientOnlyNew) transientOnlyNewOpenIds.add(oppId);
    syncOpportunityStatus(oppId, prevStatus, {
      isSeen: prevSeen,
      restoreApplication: prevApp,
    });
  } finally {
    statusPendingRequests.delete(oppId);
    renderFeedView(getState());
    resolveReq(ok);
  }
}

export function initFeedView() {
  openAccordionIds.clear();
  transientOnlyNewOpenIds.clear();
  seenPendingIds.clear();
  statusPendingRequests.clear();
  pendingFocusTarget = null;
  briefExpanded = false;
  briefDirty = false;
  isRankingBrief = false;
  inFlightBriefSave = null;
  inFlightSubmittedBrief = null;
  lastKnownSavedBrief = '';
  pendingBriefActionButton = null;

  const listEl = document.getElementById('feed-list');
  if (listEl) {
    listEl.addEventListener(
      'toggle',
      (event) => {
        const details = event.target;
        if (!(details instanceof HTMLDetailsElement) || !details.matches('.jp-accordion')) {
          return;
        }
        const card = details.closest('[data-opp-id]');
        const oppId = card?.getAttribute('data-opp-id');
        if (!oppId) return;

        if (details.open) {
          if (details.hasAttribute('data-restored-open')) {
            details.removeAttribute('data-restored-open');
            return;
          }
          openAccordionIds.add(oppId);
          if (getState().feedFilters?.onlyNew) {
            transientOnlyNewOpenIds.add(oppId);
          }
          markSeenExplicitly(oppId);
        } else {
          details.removeAttribute('data-restored-open');
          openAccordionIds.delete(oppId);
          if (transientOnlyNewOpenIds.delete(oppId)) {
            renderFeedView(getState());
          }
        }
      },
      true
    );

    listEl.addEventListener('auxclick', (event) => {
      if (event.button !== 1) return;
      const linkEl = event.target.closest('[data-action="open-link"]');
      if (!linkEl) return;
      const oppId = linkEl.closest('[data-opp-id]')?.getAttribute('data-opp-id');
      if (oppId) {
        markSeenExplicitly(oppId);
      }
    });

    listEl.addEventListener('click', (event) => {
      const retryBtn = event.target.closest('#btn-retry-feed');
      if (retryBtn) {
        reloadFeed();
        return;
      }

      const resetBtn = event.target.closest('#btn-reset-filters');
      if (resetBtn) {
        transientOnlyNewOpenIds.clear();
        resetFeedFilters();
        document.getElementById('filter-only-new')?.focus();
        return;
      }

      const actionEl = event.target.closest('[data-action]');
      if (!actionEl) return;

      const card = actionEl.closest('[data-opp-id]');
      const oppId = card?.getAttribute('data-opp-id');
      if (!oppId) return;

      const action = actionEl.getAttribute('data-action');
      if (action === 'open-link') {
        markSeenExplicitly(oppId);
      } else if (action === 'toggle-keep') {
        handleToggleKeep(oppId);
      } else if (action === 'archive') {
        handleArchiveOpportunity(oppId);
      }
    });
  }

  bindBriefDrawerEvents();
  bindCoverageAuditEvents();
  bindFilterBarEvents();
  bindFeedEscapeKey();
  refreshCoverageAudit();

  let prev = getState();
  subscribe((snapshot) => {
    if (snapshot.activeView !== prev.activeView && snapshot.activeView !== 'feed') {
      setSourcesPopoverOpen(false);
      setBriefDrawerOpen(false);
      setCoverageDrawerOpen(false);
    }
    if (snapshot.feedFilters !== prev.feedFilters) {
      transientOnlyNewOpenIds.clear();
    }
    const profileChanged =
      snapshot.profile !== prev.profile || snapshot.profileError !== prev.profileError;
    const feedChanged =
      snapshot.opportunities !== prev.opportunities ||
      snapshot.feedFilters !== prev.feedFilters ||
      snapshot.isLoadingFeed !== prev.isLoadingFeed ||
      snapshot.feedError !== prev.feedError;
    prev = snapshot;
    if (feedChanged) {
      renderFeedView(snapshot);
    } else if (profileChanged) {
      syncBriefBoxDom(snapshot);
    }
  });

  renderFeedView(getState());
}

let coverageExpanded = false;
let cachedCoverageAudit = null;

function setCoverageDrawerOpen(open) {
  coverageExpanded = Boolean(open);
  const toggleBtn = document.getElementById('coverage-toggle-btn');
  const drawerEl = document.getElementById('coverage-audit-drawer');
  const toggleTextEl = document.getElementById('coverage-toggle-text');
  if (toggleBtn) {
    toggleBtn.setAttribute('aria-expanded', coverageExpanded ? 'true' : 'false');
  }
  if (drawerEl) {
    drawerEl.classList.toggle('hidden', !coverageExpanded);
  }
  if (toggleTextEl) {
    toggleTextEl.textContent = coverageExpanded ? 'Replier' : 'Voir les écartées & bilan';
  }
}

export async function refreshCoverageAudit(prefetchedData = null) {
  try {
    const data = prefetchedData || (await fetchCoverageAudit(120));
    if (data && typeof data === 'object') {
      cachedCoverageAudit = data;
      renderCoverageAuditDom(data);
    }
  } catch {
    // Non-blocking audit fetch
  }
}

function renderCoverageAuditDom(data) {
  const previewEl = document.getElementById('coverage-preview-text');
  const sourcesGridEl = document.getElementById('coverage-sources-grid');
  const filteredCountEl = document.getElementById('coverage-filtered-count');
  const filteredListEl = document.getElementById('coverage-filtered-list');
  if (!previewEl || !sourcesGridEl || !filteredListEl) return;

  const sources = Array.isArray(data?.sources) ? data.sources : [];
  const filteredOffers = Array.isArray(data?.filtered_offers) ? data.filtered_offers : [];
  const totalFiltered = Number(data?.total_filtered ?? filteredOffers.length);

  const activeSources = sources.filter((s) => Number(s.db_total || 0) > 0 || Number(s.kept_count || 0) > 0);
  const totalDb = sources.reduce((acc, s) => acc + Number(s.db_total || 0), 0);
  const cloudflareSources = sources.filter((s) => s.status === 'cloudflare');

  let summaryText = `${activeSources.length}/${sources.length || 7} plateformes actives • ${totalDb} offres retenues • ${totalFiltered} hors-cible écartée${totalFiltered > 1 ? 's' : ''}`;
  if (cloudflareSources.length > 0) {
    summaryText += ` • ⚠️ ${cloudflareSources.map((s) => formatSourceLabel(s.source)).join(', ')} protégé par Cloudflare sur VPS`;
  }
  previewEl.textContent = summaryText;

  if (filteredCountEl) {
    filteredCountEl.textContent = String(totalFiltered);
  }

  sourcesGridEl.innerHTML = sources
    .map((s) => {
      const label = formatSourceLabel(s.source);
      const dbTotal = Number(s.db_total || 0);
      const filteredCount = Number(s.filtered_count || 0);
      const isCloudflare = s.status === 'cloudflare';
      const badgeClass = isCloudflare
        ? 'bg-[#FEFCE8] text-[#B45309] border-[#FEF08A]'
        : dbTotal > 0
          ? 'bg-[#F0FDF4] text-[#15803D] border-[#BBF7D0]'
          : 'bg-[#F5F5F4] text-[#78716C] border-[#E7E5E4]';
      const badgeText = isCloudflare ? 'Cloudflare VPS' : dbTotal > 0 ? `${dbTotal} actives` : '0 active';

      return `
        <div class="p-2 rounded border border-[#E7E5E4] bg-[#FAF9F6] flex flex-col gap-1 text-xs">
          <div class="flex items-center justify-between gap-1">
            <span class="font-semibold text-[#1C1917] truncate">${escapeHtml(label)}</span>
            <span class="px-1.5 py-0.5 rounded text-xs font-medium border ${badgeClass}">${escapeHtml(badgeText)}</span>
          </div>
          <div class="text-xs text-[#57534E]">
            ${filteredCount > 0 ? `${filteredCount} écartée${filteredCount > 1 ? 's' : ''} par filtre` : '0 écartée'}
          </div>
        </div>
      `;
    })
    .join('');

  if (filteredOffers.length === 0) {
    filteredListEl.innerHTML = `
      <div class="text-xs text-[#78716C] py-3 text-center bg-[#FAF9F6] rounded border border-[#E7E5E4]">
        Aucune annonce écartée enregistrée pour le moment. Lancez une chasse pour voir le détail du filtrage en direct.
      </div>
    `;
    return;
  }

  filteredListEl.innerHTML = filteredOffers
    .map((item) => {
      const safeUrl = sanitizeUrl(item.url);
      const titleHtml = safeUrl
        ? `<a href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener noreferrer" class="font-medium text-[#1C1917] hover:underline">${escapeHtml(item.title || 'Annonce')}</a>`
        : `<span class="font-medium text-[#1C1917]">${escapeHtml(item.title || 'Annonce')}</span>`;

      return `
        <div class="p-2.5 rounded border border-[#E7E5E4] bg-[#FFFFFF] flex flex-wrap items-center justify-between gap-2 text-xs" data-filtered-id="${escapeHtml(item.id)}">
          <div class="flex flex-col gap-0.5 min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-1.5">
              <span class="jp-pill">${escapeHtml(formatSourceLabel(item.source))}</span>
              ${titleHtml}
              <span class="text-[#57534E]">· ${escapeHtml(item.company || 'Entreprise')}</span>
              <span class="text-[#78716C]">(${escapeHtml(item.location || 'Non précisée')})</span>
            </div>
            <div class="text-xs text-[#B45309]">
              Motif d'exclusion : ${escapeHtml(item.reject_reason || 'Hors critères')}
            </div>
          </div>
          <button type="button" class="jp-btn-secondary text-xs shrink-0" data-action="rescue-filtered" data-rescue-id="${escapeHtml(item.id)}">
            Repêcher dans le flux
          </button>
        </div>
      `;
    })
    .join('');
}

function bindCoverageAuditEvents() {
  const toggleBtn = document.getElementById('coverage-toggle-btn');
  if (toggleBtn) {
    toggleBtn.addEventListener('click', () => {
      const next = !coverageExpanded;
      setCoverageDrawerOpen(next);
      if (next && !cachedCoverageAudit) {
        refreshCoverageAudit();
      }
    });
  }

  const filteredListEl = document.getElementById('coverage-filtered-list');
  if (filteredListEl) {
    filteredListEl.addEventListener('click', async (event) => {
      const btn = event.target.closest('[data-action="rescue-filtered"]');
      if (!btn) return;
      const filteredId = btn.getAttribute('data-rescue-id');
      if (!filteredId) return;

      btn.disabled = true;
      btn.textContent = 'Repêchage...';
      try {
        const res = await rescueFilteredOpportunity(filteredId);
        if (res?.opportunity) {
          const current = getState().opportunities || [];
          setOpportunities([res.opportunity, ...current.filter((o) => o.id !== res.opportunity.id)]);
        }
        if (res?.coverage_audit) {
          cachedCoverageAudit = res.coverage_audit;
          renderCoverageAuditDom(res.coverage_audit);
        } else {
          await refreshCoverageAudit();
        }
        showToast('Offre repêchée et ajoutée en haut de votre flux !', 'success');
      } catch (err) {
        btn.disabled = false;
        btn.textContent = 'Repêcher dans le flux';
        showToast(err.message || 'Impossible de repêcher cette offre.', 'error');
      }
    });
  }
}
