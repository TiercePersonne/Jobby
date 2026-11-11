import { fetchOpportunities, markAllOpportunitiesSeen } from '../api.js';
import {
  createDefaultFeedFilters,
  getState,
  markAllOpportunitiesSeenInState,
  resetFeedFilters,
  restoreOpportunitiesSeen,
  setFeedSort,
  setFeedType,
  setHideAlternance,
  setLoadingFeed,
  setOnlyNew,
  setOpportunities,
  subscribe,
  toggleFeedMode,
  toggleFeedRegion,
  toggleFeedSource,
} from '../state.js';
import {
  FEED_SOURCES,
  detectClientRemotePolicy,
  detectOpportunityRegion,
  escapeHtml,
  formatRelativeDate,
  formatSourceLabel,
  isAlternanceOrStage,
  sanitizeUrl,
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
    kept: '<span class="jp-pill jp-pill-sage">Gardé</span>',
    applied: '<span class="jp-pill">Candidaté</span>',
    interview: '<span class="jp-pill">Entretien</span>',
  })
);

let isMarkingAllSeen = false;

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
    if (filters.onlyNew && Boolean(opp.is_seen)) {
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

function renderOpportunityRow(opp) {
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
  const statusChip = Object.hasOwn(STATUS_CHIPS, statusKey)
    ? STATUS_CHIPS[statusKey]
    : '<span class="jp-label">Disponible</span>';

  const isPost = opp.opportunity_type === 'post';
  const ctaText = isPost ? 'Voir le post &rarr;' : "Voir l'offre &rarr;";
  const ariaAction = isPost ? 'Voir le post' : "Voir l'offre";
  const ctaAriaLabel = escapeHtml(`${ariaAction} : ${rawTitle} — ${rawCompany}`);

  const linkHtml = safeUrl
    ? `<a href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener noreferrer" class="jp-btn-primary shrink-0" aria-label="${ctaAriaLabel}">${ctaText}</a>`
    : '<span class="jp-label">Lien indisponible</span>';

  return `
    <article class="jp-card flex flex-col gap-3" data-opp-id="${escapeHtml(opp.id || '')}" data-is-seen="${opp.is_seen ? 'true' : 'false'}">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div class="jp-label flex flex-wrap items-center gap-2 min-w-0">
          ${newBadgeHtml}
          <span>${metaParts.join(' &bull; ')}</span>
        </div>
        ${statusChip}
      </div>

      <div class="min-w-0">
        <h2 class="jp-card-title break-words">${title}</h2>
        <p class="jp-body mt-0.5 break-words">
          <span class="font-medium text-[#1C1917]">${company}</span>${locationText ? ` &bull; <span>${locationText}</span>` : ''}
        </p>
      </div>

      <div class="flex items-center justify-between gap-3 pt-3 border-t border-[#E7E5E4]">
        <span class="jp-label">${isPost ? 'Post réseau' : 'Offre'}</span>
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

export function renderFeedView(snapshot = getState()) {
  const listEl = document.getElementById('feed-list');
  const countEl = document.getElementById('feed-count');
  const model = computeFeedViewModel(snapshot);

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
    return;
  }

  if (totalActive === 0) {
    listEl.innerHTML = `
      <div class="jp-card jp-body text-center py-8" role="status">
        Aucune opportunité disponible pour le moment.
      </div>
    `;
    return;
  }

  if (visibleCount === 0) {
    listEl.innerHTML = `
      <div class="jp-card flex flex-col items-center gap-3 py-8 text-center" role="status">
        <p class="jp-body">Aucune opportunité ne correspond à ces critères.</p>
        <button type="button" id="btn-reset-filters" class="jp-btn-secondary">Réinitialiser les filtres</button>
      </div>
    `;
    return;
  }

  listEl.innerHTML = model.filteredOpportunities.map(renderOpportunityRow).join('');
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

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    if (!isSourcesPopoverOpen()) return;
    if (getState().activeModal !== null) return;

    event.preventDefault();
    setSourcesPopoverOpen(false, { restoreFocus: true });
  });

  window.addEventListener('resize', () => {
    if (isSourcesPopoverOpen()) {
      positionSourcesPopover();
    }
  });
}

export function initFeedView() {
  const listEl = document.getElementById('feed-list');
  if (listEl) {
    listEl.addEventListener('click', (event) => {
      const retryBtn = event.target.closest('#btn-retry-feed');
      if (retryBtn) {
        reloadFeed();
        return;
      }

      const resetBtn = event.target.closest('#btn-reset-filters');
      if (resetBtn) {
        resetFeedFilters();
        document.getElementById('filter-only-new')?.focus();
      }
    });
  }

  bindFilterBarEvents();

  let prev = getState();
  subscribe((snapshot) => {
    if (snapshot.activeView !== prev.activeView && snapshot.activeView !== 'feed') {
      setSourcesPopoverOpen(false);
    }
    const changed =
      snapshot.opportunities !== prev.opportunities ||
      snapshot.feedFilters !== prev.feedFilters ||
      snapshot.isLoadingFeed !== prev.isLoadingFeed ||
      snapshot.feedError !== prev.feedError;
    prev = snapshot;
    if (changed) {
      renderFeedView(snapshot);
    }
  });

  renderFeedView(getState());
}
