/**
 * Pure utility helpers for Jobby ES6 modules.
 */

export const ICONS = {
  globe: (cls = 'w-3.5 h-3.5') =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>`,
  building: (cls = 'w-3.5 h-3.5') =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="2" width="16" height="20" rx="2" ry="2"/><path d="M9 22v-4h6v4"/><line x1="8" y1="6" x2="8.01" y2="6"/><line x1="16" y1="6" x2="16.01" y2="6"/><line x1="12" y1="6" x2="12.01" y2="6"/><line x1="8" y1="10" x2="8.01" y2="10"/><line x1="16" y1="10" x2="16.01" y2="10"/><line x1="12" y1="10" x2="12.01" y2="10"/></svg>`,
  mapPin: (cls = 'w-3.5 h-3.5') =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>`,
  clock: (cls = 'w-3.5 h-3.5') =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,
  star: (cls = 'w-3.5 h-3.5', filled = false) =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="${filled ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>`,
  check: (cls = 'w-3.5 h-3.5') =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6L9 17l-5-5"/></svg>`,
  archive: (cls = 'w-3.5 h-3.5') =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="21 8 21 21 3 21 3 8"/><rect x="1" y="3" width="22" height="5"/><line x1="10" y1="12" x2="14" y2="12"/></svg>`,
  restore: (cls = 'w-3.5 h-3.5') =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>`,
  briefcase: (cls = 'w-3.5 h-3.5') =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="7" width="20" height="14" rx="2" ry="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/></svg>`,
  megaphone: (cls = 'w-3.5 h-3.5') =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 11l18-5v12L3 13v-2z"/><path d="M11.6 16.8a3 3 0 1 1-5.8-1.6"/></svg>`,
  graduation: (cls = 'w-4 h-4') =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 10v6M2 10l10-5 10 5-10 5z"/><path d="M6 12v5c3 3 9 3 12 0v-5"/></svg>`,
  user: (cls = 'w-4 h-4') =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`,
  spark: (cls = 'w-3.5 h-3.5') =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2l2.4 7.2L22 12l-7.6 2.8L12 22l-2.4-7.2L2 12l7.6-2.8z"/></svg>`,
  help: (cls = 'w-3.5 h-3.5') =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
  currency: (cls = 'w-3.5 h-3.5') =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M15 9.5a3.5 3.5 0 0 0-5 0c-1.5 1.5-1.5 3.5 0 5a3.5 3.5 0 0 0 5 0"/><line x1="7" y1="12" x2="13" y2="12"/></svg>`,
  fileText: (cls = 'w-3.5 h-3.5') =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>`,
};

/**
 * Escapes HTML special characters to prevent XSS when inserting strings into HTML templates.
 */
export function escapeHtml(text) {
  if (text === null || text === undefined) return '';
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

/**
 * Validates that a URL uses an explicit http/https protocol before binding to <a href>.
 */
export function sanitizeUrl(rawUrl) {
  if (!rawUrl || typeof rawUrl !== 'string') return null;
  const trimmed = rawUrl.trim();
  if (!trimmed) return null;
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
      return parsed.href;
    }
  } catch {
    return null;
  }
  return null;
}

/**
 * Formats an ISO timestamp into a concise French relative label.
 */
export function formatRelativeDate(isoStr) {
  if (!isoStr) return 'Récemment';
  try {
    const date = new Date(isoStr);
    if (Number.isNaN(date.getTime())) return 'Récemment';
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffSecs = Math.max(0, Math.floor(diffMs / 1000));
    if (diffSecs < 60) return "À l'instant";
    const diffMins = Math.floor(diffSecs / 60);
    if (diffMins < 60) return `Il y a ${diffMins} min`;
    const diffHours = Math.floor(diffMins / 60);
    if (diffHours < 24) return `Il y a ${diffHours} h`;
    const diffDays = Math.floor(diffHours / 24);
    if (diffDays === 1) return 'Hier';
    if (diffDays < 7) return `Il y a ${diffDays} j`;
    const options = { day: 'numeric', month: 'short' };
    if (date.getFullYear() !== now.getFullYear()) {
      options.year = 'numeric';
    }
    return date.toLocaleDateString('fr-FR', options);
  } catch {
    return 'Récemment';
  }
}

const REMOTE_POLICY_ALIASES = Object.freeze(
  Object.assign(Object.create(null), {
    full: 'full',
    hybrid: 'hybrid',
    partial: 'hybrid',
    onsite: 'onsite',
    'on-site': 'onsite',
    on_site: 'onsite',
  })
);

/**
 * Infers workplace remote policy from opportunity metadata and text.
 */
export function detectClientRemotePolicy(opp) {
  if (!opp) return 'unknown';
  const rawPolicy = String(opp.remote_policy || '').trim().toLowerCase();
  if (rawPolicy && rawPolicy !== 'unknown' && Object.hasOwn(REMOTE_POLICY_ALIASES, rawPolicy)) {
    return REMOTE_POLICY_ALIASES[rawPolicy];
  }
  const text = `${opp.title || ''} ${opp.location || ''} ${opp.description || ''}`.toLowerCase();
  if (
    text.includes('100% remote') ||
    text.includes('full remote') ||
    text.includes('full-remote') ||
    text.includes('100% télétravail') ||
    text.includes('100% teletravail') ||
    text.includes('télétravail total') ||
    text.includes('totalement à distance') ||
    text.includes('remote only')
  ) {
    return 'full';
  }
  if (
    text.includes('hybride') ||
    text.includes('hybrid') ||
    text.includes('jours de télétravail') ||
    text.includes('j/semaine') ||
    text.includes('jours/semaine') ||
    text.includes('télétravail possible') ||
    text.includes('télétravail partiel') ||
    text.includes('mixte')
  ) {
    return 'hybrid';
  }
  if (
    text.includes('sur site') ||
    text.includes('sur-site') ||
    text.includes('présentiel') ||
    text.includes('presentiel') ||
    text.includes('on-site') ||
    text.includes('pas de télétravail')
  ) {
    return 'onsite';
  }
  return 'unknown';
}

const ALT_REGEX = /\b(alternan(?:ce|t|te|ts|tes)|apprentissage|contrat\s+pro|contrat\s+de\s+professionnalisation|stage|stagiaire|stagiaires|internship|intern)\b/i;

/**
 * Detects internships and apprenticeship offers/posts.
 */
export function isAlternanceOrStage(opp) {
  if (!opp) return false;
  const title = (opp.title || '').toLowerCase();
  const contract = (opp.contract_type || '').toLowerCase();
  const intent = opp.post_intent || '';

  if (intent === 'candidate_seeking_alternance' || intent === 'hiring_alternance') return true;
  if (
    contract.includes('alternance') ||
    contract.includes('apprentissage') ||
    contract.includes('stage') ||
    contract.includes('intern')
  ) {
    return true;
  }
  if (
    Array.isArray(opp.tags) &&
    (opp.tags.includes('alternance') || opp.tags.includes('stage') || opp.tags.includes('alternance_ou_stage'))
  ) {
    return true;
  }
  return ALT_REGEX.test(title);
}

const FR_REGEX = /\b(france|paris|boulogne|issy|neuilly|toulouse|d[eé]fense|asson|levallois|brest|marseille|sainte-colombe|saint-herblain|relecq|lyon|bordeaux|challes|courbevoie|nantes|lille|strasbourg|rennes|nice|montpellier|grenoble|rouen|tours|angers|dijon|aix|metz|nancy|reims|clermont|caen|orléans|orleans|perpignan|le havre|versailles|cergy)\b/i;
const EU_OTHER_REGEX = /\b(europe|london|londres|uk|united kingdom|great britain|england|berlin|munich|germany|allemagne|frankfurt|hamburg|madrid|barcelona|spain|espagne|amsterdam|rotterdam|netherlands|pays-bas|brussels|bruxelles|belgium|belgique|dublin|ireland|irlande|milan|rome|italy|italie|zurich|geneva|gen[eè]ve|switzerland|suisse|vienna|austria|stockholm|sweden|copenhagen|denmark|oslo|norway|helsinki|finland|lisbon|portugal|warsaw|poland|prague|czech|athens|greece)\b/i;
const US_REGEX = /\b(usa|united states|[eé]tats-unis|los angeles|new york|san francisco|denver|san diego|austin|chicago|sunnyvale|seattle|boston|miami|atlanta|dallas|houston|california|texas|silicon valley|\bny\b|\bca\b|\btx\b|\bwa\b|\bsf\b|\bnyc\b)\b/i;

/**
 * Classifies opportunity geographic region into FR / EU (hors FR) / US / other.
 */
export function detectOpportunityRegion(opp) {
  if (!opp) return { isFrance: false, isEurope: false, isUSA: false, primary: 'other' };
  const loc = (opp.location || '').toLowerCase();
  const title = (opp.title || '').toLowerCase();
  const full = `${loc} ${title}`;

  const isFrance = FR_REGEX.test(loc) || FR_REGEX.test(full);
  const isEuropeOther = !isFrance && (EU_OTHER_REGEX.test(loc) || EU_OTHER_REGEX.test(full));
  const isUSA = !isFrance && (US_REGEX.test(loc) || US_REGEX.test(full));

  let primary = 'other';
  if (isFrance) primary = 'france';
  else if (isEuropeOther) primary = 'europe';
  else if (isUSA) primary = 'usa';

  return { isFrance, isEurope: isEuropeOther, isUSA, primary };
}

const SOURCE_LABELS = Object.freeze(
  Object.assign(Object.create(null), {
    wttj: 'WTTJ',
    linkedin: 'LinkedIn',
    hellowork: 'HelloWork',
    indeed: 'Indeed',
    glassdoor: 'Glassdoor',
    teamtailor: 'Teamtailor',
    jobgether: 'Jobgether',
  })
);

/**
 * Formats an opportunity source into a consistent human-readable label.
 */
export function formatSourceLabel(opp) {
  if (!opp) return 'Offre';
  if (opp.opportunity_type === 'post') return 'Post Réseau';
  const key = String(opp.source || '').trim().toLowerCase();
  return Object.hasOwn(SOURCE_LABELS, key)
    ? SOURCE_LABELS[key]
    : opp.source
      ? String(opp.source)
      : 'Offre';
}
