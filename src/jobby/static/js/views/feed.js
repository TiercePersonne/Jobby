import { fetchOpportunities } from '../api.js';
import { getState, setLoadingFeed, setOpportunities, subscribe } from '../state.js';
import {
  detectClientRemotePolicy,
  detectOpportunityRegion,
  escapeHtml,
  formatRelativeDate,
  formatSourceLabel,
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

const STATUS_CHIPS = Object.freeze(
  Object.assign(Object.create(null), {
    kept: '<span class="jp-pill jp-pill-sage">Gardé</span>',
    applied: '<span class="jp-pill">Candidaté</span>',
    interview: '<span class="jp-pill">Entretien</span>',
  })
);

function renderOpportunityRow(opp) {
  const rawTitle = opp.title || 'Opportunité sans titre';
  const rawCompany = opp.company || 'Entreprise non précisée';
  const title = escapeHtml(rawTitle);
  const company = escapeHtml(rawCompany);
  const sourceLabel = escapeHtml(formatSourceLabel(opp));
  const locationText = opp.location ? escapeHtml(opp.location) : '';
  const safeUrl = sanitizeUrl(opp.url);
  const relDate = escapeHtml(formatRelativeDate(opp.published_at || opp.discovered_at));

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
    <article class="jp-card flex flex-col gap-3" data-opp-id="${escapeHtml(opp.id || '')}">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div class="jp-label flex flex-wrap items-center gap-1.5 min-w-0">
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

export function renderFeedView(snapshot = getState()) {
  const listEl = document.getElementById('feed-list');
  const countEl = document.getElementById('feed-count');
  if (!listEl) return;

  const activeOpportunities = (snapshot.opportunities || []).filter(
    (opp) => opp.status !== 'archived' && opp.status !== 'rejected'
  );

  if (countEl) {
    if (snapshot.isLoadingFeed && activeOpportunities.length === 0) {
      countEl.textContent = 'Chargement...';
    } else {
      const n = activeOpportunities.length;
      countEl.textContent = `${n} opportunité${n > 1 ? 's' : ''}`;
    }
  }

  if (snapshot.isLoadingFeed && activeOpportunities.length === 0) {
    listEl.innerHTML = `
      <div class="jp-card jp-body text-center py-8" role="status">
        Chargement des opportunités...
      </div>
    `;
    return;
  }

  if (snapshot.feedError && activeOpportunities.length === 0) {
    listEl.innerHTML = `
      <div class="jp-card flex flex-col items-center gap-3 py-8 text-center" role="status">
        <p class="jp-body text-[#991B1B]">${escapeHtml(snapshot.feedError)}</p>
        <button type="button" id="btn-retry-feed" class="jp-btn-secondary">Réessayer</button>
      </div>
    `;
    return;
  }

  if (activeOpportunities.length === 0) {
    listEl.innerHTML = `
      <div class="jp-card jp-body text-center py-8" role="status">
        Aucune opportunité disponible pour le moment.
      </div>
    `;
    return;
  }

  listEl.innerHTML = activeOpportunities.map(renderOpportunityRow).join('');
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

export function initFeedView() {
  const listEl = document.getElementById('feed-list');
  if (listEl) {
    listEl.addEventListener('click', (event) => {
      const retryBtn = event.target.closest('#btn-retry-feed');
      if (retryBtn) {
        reloadFeed();
      }
    });
  }

  let prev = getState();
  subscribe((snapshot) => {
    const changed =
      snapshot.opportunities !== prev.opportunities ||
      snapshot.isLoadingFeed !== prev.isLoadingFeed ||
      snapshot.feedError !== prev.feedError;
    prev = snapshot;
    if (changed) {
      renderFeedView(snapshot);
    }
  });

  renderFeedView(getState());
}
