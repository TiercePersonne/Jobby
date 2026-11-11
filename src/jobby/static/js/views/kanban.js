import {
  updateApplicationNotes as apiUpdateApplicationNotes,
  updateApplicationStatus,
} from '../api.js';
import {
  getActiveKanbanCount,
  getState,
  subscribe,
  updateApplicationNotes,
  updateApplicationStage,
} from '../state.js';
import {
  ICONS,
  escapeHtml,
  formatRelativeDate,
  sanitizeUrl,
  showToast,
} from '../utils.js';

export const KANBAN_STAGES = ['saved', 'applied', 'interview', 'offer'];
export const ALL_KANBAN_STAGES = ['saved', 'applied', 'interview', 'offer', 'rejected'];

let currentDraggedAppId = null;
let eventsBound = false;

const STAGE_LABELS = Object.freeze({
  saved: 'Repérés',
  applied: 'Candidaté',
  interview: 'Entretiens',
  offer: 'Offres reçues',
  rejected: 'Archivées / Refusées',
});

function announceKanbanMove(title, targetStage) {
  const liveRegion = document.getElementById('kanban-live-region');
  if (liveRegion) {
    const stageLabel = STAGE_LABELS[targetStage] || targetStage;
    liveRegion.textContent = `Candidature ${title} déplacée vers ${stageLabel}.`;
  }
}

function normalizeStageKey(raw) {
  const st = String(raw || 'saved').trim().toLowerCase();
  if (st === 'favorite' || st === 'kept') return 'saved';
  if (st === 'archived') return 'rejected';
  return ALL_KANBAN_STAGES.includes(st) ? st : 'saved';
}

function renderKanbanCard(app, opp = {}) {
  const appId = escapeHtml(app.id);
  const oppId = escapeHtml(app.opportunity_id || '');
  const title = opp.title || (app.opportunity_id ? `Opportunité ${app.opportunity_id}` : 'Candidature');
  const company = opp.company || 'Entreprise non précisée';
  const location = opp.location || '';
  const source = (opp.source || 'Direct').toUpperCase();
  const safeUrl = sanitizeUrl(opp.url);
  const notes = app.notes || '';
  const score = Number.isFinite(opp?.score) ? Math.round(opp.score) : null;
  const currentStage = normalizeStageKey(app.status);
  const isRejected = currentStage === 'rejected';

  let scoreBadgeHtml = '';
  if (score !== null) {
    let scoreColor = 'bg-[#F5F5F4] text-[#78716C] border-[#E7E5E4]';
    if (score >= 80) {
      scoreColor = 'bg-[#F0FDF4] text-[#14532D] border-[#BBF7D0]';
    } else if (score >= 50) {
      scoreColor = 'bg-[#FEFCE8] text-[#713F12] border-[#FEF08A]';
    }
    scoreBadgeHtml = `<span class="inline-flex items-center px-1.5 py-0.5 rounded text-[11px] font-semibold border ${scoreColor}">${score}%</span>`;
  }

  const titleHtml = safeUrl
    ? `<a href="${safeUrl}" target="_blank" rel="noopener noreferrer" class="jp-card-title hover:underline line-clamp-2" title="${escapeHtml(title)}">${escapeHtml(title)}</a>`
    : `<h3 class="jp-card-title line-clamp-2" title="${escapeHtml(title)}">${escapeHtml(title)}</h3>`;

  return `
    <article 
      class="jp-kanban-card p-3.5 flex flex-col gap-2.5" 
      draggable="true" 
      data-app-id="${appId}" 
      data-kanban-opp-id="${oppId}" 
      data-stage="${currentStage}"
      aria-label="${escapeHtml(title)} chez ${escapeHtml(company)}"
    >
      <!-- En-tête de carte : source, score et date -->
      <div class="flex items-center justify-between gap-2">
        <div class="flex items-center gap-1.5 flex-wrap">
          <span class="jp-pill text-[11px] py-0.5 px-2 font-medium">${escapeHtml(source)}</span>
          ${scoreBadgeHtml}
        </div>
        ${opp.created_at || app.applied_at || app.updated_at ? `<span class="text-[11px] text-[#78716C] whitespace-nowrap">${formatRelativeDate(opp.created_at || app.applied_at || app.updated_at)}</span>` : ''}
      </div>

      <!-- Corps : Titre et entreprise -->
      <div class="flex flex-col gap-0.5">
        ${titleHtml}
        <div class="flex items-center gap-1.5 text-xs text-[#57534E]">
          <span class="font-medium truncate">${escapeHtml(company)}</span>
          ${location ? `<span class="text-[#78716C]">&bull;</span><span class="text-[#78716C] truncate">${escapeHtml(location)}</span>` : ''}
        </div>
      </div>

      <!-- Notes de relance modifiables en ligne -->
      <div class="flex flex-col gap-1 mt-1">
        <div class="flex items-center justify-between text-[11px] text-[#78716C] font-medium">
          <label for="notes-${appId}" class="cursor-pointer">Notes de relance</label>
          <span class="notes-status text-[10px] text-[#15803D] hidden font-semibold">Enregistré</span>
        </div>
        <textarea 
          id="notes-${appId}" 
          data-action="edit-notes" 
          data-app-id="${appId}" 
          rows="2" 
          placeholder="Ajouter une note de contact ou relance..." 
          class="jp-kanban-notes w-full resize-none text-xs"
        >${escapeHtml(notes)}</textarea>
      </div>

      <!-- Actions de carte : sélecteur tactile & bouton lettre -->
      <div class="flex items-center justify-between gap-2 pt-2 border-t border-[#E7E5E4] mt-0.5">
        <div class="flex items-center gap-1.5 flex-1 min-w-0">
          <label for="stage-select-${appId}" class="sr-only">Changer l'étape</label>
          <select 
            id="stage-select-${appId}" 
            data-action="change-stage" 
            data-app-id="${appId}" 
            class="jp-kanban-select w-full truncate"
          >
            <option value="saved" ${currentStage === 'saved' ? 'selected' : ''}>Repérés</option>
            <option value="applied" ${currentStage === 'applied' ? 'selected' : ''}>Candidaté</option>
            <option value="interview" ${currentStage === 'interview' ? 'selected' : ''}>Entretiens</option>
            <option value="offer" ${currentStage === 'offer' ? 'selected' : ''}>Offres reçues</option>
            <option value="rejected" ${isRejected ? 'selected' : ''}>Archivée / Refusée</option>
          </select>
        </div>

        <button 
          type="button" 
          data-action="generate-letter" 
          data-modal-open="modal-letter"
          data-app-id="${appId}" 
          data-kanban-opp-id="${oppId}"
          class="jp-btn-secondary text-xs py-1 px-2.5 whitespace-nowrap flex items-center gap-1"
          title="Générer une lettre de motivation"
        >
          ${ICONS.fileText('w-3.5 h-3.5')}
          <span>Lettre IA</span>
        </button>
      </div>

      <!-- Action discrète d'archivage ou restauration -->
      <div class="flex items-center justify-end text-[11px] pt-1">
        ${isRejected 
          ? `<button type="button" data-action="restore-card" data-app-id="${appId}" class="text-[#2563EB] hover:underline flex items-center gap-1">${ICONS.restore('w-3 h-3')}<span>Restaurer</span></button>`
          : `<button type="button" data-action="archive-card" data-app-id="${appId}" class="text-[#78716C] hover:text-[#991B1B] hover:underline flex items-center gap-1">${ICONS.archive('w-3 h-3')}<span>Archiver</span></button>`
        }
      </div>
    </article>
  `;
}

export function renderKanbanView(snapshot = getState()) {
  const summaryEl = document.getElementById('kanban-shell-summary');
  const apps = snapshot.applications || [];
  const opps = snapshot.opportunities || [];
  const activeCount = getActiveKanbanCount(apps);

  if (summaryEl) {
    summaryEl.textContent = `${activeCount} candidature${activeCount > 1 ? 's' : ''} active${activeCount > 1 ? 's' : ''}`;
  }

  const stageCounts = Object.assign(Object.create(null), {
    saved: 0,
    applied: 0,
    interview: 0,
    offer: 0,
    rejected: 0,
  });

  const stageApps = Object.assign(Object.create(null), {
    saved: [],
    applied: [],
    interview: [],
    offer: [],
    rejected: [],
  });

  apps.forEach((app) => {
    if (!app) return;
    const st = normalizeStageKey(app.status);
    if (Object.hasOwn(stageCounts, st)) {
      stageCounts[st] += 1;
      stageApps[st].push(app);
    }
  });

  // Update counters across 4 active columns + archived
  ALL_KANBAN_STAGES.forEach((stage) => {
    const counterEl = document.querySelector(`[data-kanban-col-count="${stage}"]`);
    if (counterEl) {
      counterEl.textContent = String(stageCounts[stage]);
    }
  });

  // Preserve focus if active element is a notes textarea
  const activeEl = document.activeElement;
  const isEditingNotes =
    activeEl instanceof HTMLTextAreaElement &&
    activeEl.getAttribute('data-action') === 'edit-notes';
  const activeAppId = isEditingNotes ? activeEl.getAttribute('data-app-id') : null;
  const cursorPos = isEditingNotes ? activeEl.selectionStart : null;
  const currentEditingVal = isEditingNotes ? activeEl.value : null;

  // Render cards into respective columns
  ALL_KANBAN_STAGES.forEach((stage) => {
    const container = document.querySelector(`[data-kanban-cards="${stage}"]`);
    const emptyEl = document.querySelector(`[data-kanban-empty="${stage}"]`);
    if (!container) return;

    const list = stageApps[stage] || [];
    if (list.length === 0) {
      container.innerHTML = '';
      if (emptyEl) emptyEl.classList.remove('hidden');
    } else {
      if (emptyEl) emptyEl.classList.add('hidden');
      container.innerHTML = list
        .map((app) => {
          if (!app) return '';
          const opp =
            app.opportunity || opps.find((o) => o.id === app.opportunity_id) || {};
          return renderKanbanCard(app, opp);
        })
        .join('');
    }
  });

  // Restore focus if needed
  if (isEditingNotes && activeAppId) {
    const restoredTextarea = document.querySelector(
      `textarea[data-action="edit-notes"][data-app-id="${activeAppId}"]`
    );
    if (restoredTextarea) {
      if (currentEditingVal !== null) {
        restoredTextarea.value = currentEditingVal;
      }
      restoredTextarea.focus();
      if (cursorPos !== null) {
        try {
          restoredTextarea.setSelectionRange(cursorPos, cursorPos);
        } catch {
          // Ignore range errors
        }
      }
    }
  }
}

async function handleStageChange(appId, targetStage) {
  if (!appId || !targetStage) return;
  const snapshot = getState();
  const apps = snapshot.applications || [];
  const app = apps.find((a) => a.id === appId || a.opportunity_id === appId);
  if (!app) return;

  const opp =
    app.opportunity || snapshot.opportunities.find((o) => o.id === app.opportunity_id) || {};
  const oppTitle =
    opp.title || (app.opportunity_id ? `Opportunité ${app.opportunity_id}` : 'Candidature');

  const currentStage = normalizeStageKey(app.status);
  const normalizedTarget = normalizeStageKey(targetStage);
  if (currentStage === normalizedTarget) return;

  // Optimistic update in state.js
  updateApplicationStage(appId, normalizedTarget);
  announceKanbanMove(oppTitle, normalizedTarget);

  try {
    await updateApplicationStatus(appId, normalizedTarget);
  } catch {
    // Rollback to previous stage
    updateApplicationStage(appId, currentStage);
    announceKanbanMove(oppTitle, currentStage);
    showToast(
      "Impossible de déplacer la candidature. Rétablissement de l'étape précédente.",
      'error'
    );
  }
}

async function handleNotesSave(appId, nextNotes, prevNotes) {
  if (!appId) return;
  // Optimistic update
  updateApplicationNotes(appId, nextNotes);

  try {
    await apiUpdateApplicationNotes(appId, nextNotes);
    const card = document.querySelector(`article[data-app-id="${appId}"]`);
    const statusEl = card?.querySelector('.notes-status');
    if (statusEl) {
      statusEl.textContent = 'Enregistré';
      statusEl.classList.remove('hidden');
      setTimeout(() => {
        statusEl.classList.add('hidden');
      }, 2000);
    }
  } catch {
    // Rollback notes in state
    updateApplicationNotes(appId, prevNotes);
    const textareaEl = document.querySelector(
      `textarea[data-action="edit-notes"][data-app-id="${appId}"]`
    );
    if (textareaEl) {
      textareaEl.value = prevNotes;
      textareaEl.dataset.initialValue = prevNotes;
    }
    showToast("Impossible d'enregistrer la note. Modification annulée.", 'error');
  }
}

function bindKanbanEvents() {
  if (eventsBound) return;
  const viewKanban = document.getElementById('view-kanban');
  if (!viewKanban) return;
  eventsBound = true;

  // 1. Accordion toggle for archived section
  const toggleBtn = document.getElementById('kanban-archived-toggle');
  const drawerEl = document.getElementById('kanban-archived-drawer');
  const iconEl = document.getElementById('kanban-archived-icon');
  if (toggleBtn && drawerEl) {
    toggleBtn.addEventListener('click', () => {
      const isHidden = drawerEl.classList.contains('hidden');
      drawerEl.classList.toggle('hidden', !isHidden);
      toggleBtn.setAttribute('aria-expanded', String(isHidden));
      if (iconEl) {
        iconEl.classList.toggle('rotate-90', isHidden);
      }
    });
  }

  // 2. Drag & Drop events
  viewKanban.addEventListener('dragstart', (e) => {
    if (e.target.closest('button, a, select, input, textarea')) {
      e.preventDefault();
      return;
    }
    const card = e.target.closest('.jp-kanban-card');
    if (!card) return;

    const appId = card.getAttribute('data-app-id');
    if (!appId) return;

    currentDraggedAppId = appId;
    e.dataTransfer.setData('text/plain', appId);
    e.dataTransfer.effectAllowed = 'move';
    card.classList.add('is-dragging');
  });

  viewKanban.addEventListener('dragend', (e) => {
    const card = e.target.closest('.jp-kanban-card');
    if (card) card.classList.remove('is-dragging');
    currentDraggedAppId = null;
    document.querySelectorAll('.kanban-dropzone').forEach((dz) => {
      dz.classList.remove('is-dragover');
    });
  });

  viewKanban.addEventListener('dragover', (e) => {
    const dropzone = e.target.closest('.kanban-dropzone');
    if (dropzone) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      dropzone.classList.add('is-dragover');
    }

    // Auto-open archived drawer if dragging over archived toggle/section
    const archivedSection = e.target.closest('#kanban-archived-section');
    if (archivedSection && drawerEl && drawerEl.classList.contains('hidden')) {
      drawerEl.classList.remove('hidden');
      toggleBtn?.setAttribute('aria-expanded', 'true');
      iconEl?.classList.add('rotate-90');
    }
  });

  viewKanban.addEventListener('dragleave', (e) => {
    const dropzone = e.target.closest('.kanban-dropzone');
    if (dropzone && !dropzone.contains(e.relatedTarget)) {
      dropzone.classList.remove('is-dragover');
    }
  });

  viewKanban.addEventListener('drop', (e) => {
    const dropzone = e.target.closest('.kanban-dropzone');
    if (!dropzone) return;
    e.preventDefault();
    dropzone.classList.remove('is-dragover');

    const appId = e.dataTransfer.getData('text/plain') || currentDraggedAppId;
    const targetStage = dropzone.getAttribute('data-kanban-dropzone');
    if (appId && targetStage) {
      handleStageChange(appId, targetStage);
    }
  });

  // 3. Mobile select change stage
  viewKanban.addEventListener('change', (e) => {
    const target = e.target;
    if (target.matches('select[data-action="change-stage"]')) {
      const appId = target.getAttribute('data-app-id');
      const targetStage = target.value;
      if (appId && targetStage) {
        handleStageChange(appId, targetStage);
      }
    }
  });

  // 4. Notes editing (focusin to capture initial, blur to save)
  viewKanban.addEventListener('focusin', (e) => {
    const target = e.target;
    if (target.matches('textarea[data-action="edit-notes"]')) {
      target.dataset.initialValue = target.value;
    }
  });

  viewKanban.addEventListener(
    'blur',
    (e) => {
      const target = e.target;
      if (target.matches('textarea[data-action="edit-notes"]')) {
        const appId = target.getAttribute('data-app-id');
        const initialVal = target.dataset.initialValue ?? '';
        const currentVal = target.value;
        if (currentVal !== initialVal) {
          target.dataset.initialValue = currentVal;
          handleNotesSave(appId, currentVal, initialVal);
        }
      }
    },
    true
  );

  // 5. Button clicks (Archive / Restore)
  viewKanban.addEventListener('click', (e) => {
    const archiveBtn = e.target.closest('button[data-action="archive-card"]');
    if (archiveBtn) {
      const appId = archiveBtn.getAttribute('data-app-id');
      if (appId) handleStageChange(appId, 'rejected');
      return;
    }

    const restoreBtn = e.target.closest('button[data-action="restore-card"]');
    if (restoreBtn) {
      const appId = restoreBtn.getAttribute('data-app-id');
      if (appId) handleStageChange(appId, 'saved');
      return;
    }
  });
}

function haveAppsChangedSignificantly(prevApps, nextApps) {
  if (prevApps === nextApps) return false;
  if (!prevApps || !nextApps) return true;
  if (prevApps.length !== nextApps.length) return true;
  for (let i = 0; i < prevApps.length; i++) {
    const p = prevApps[i];
    const n = nextApps[i];
    if (!p || !n) return true;
    if (p.id !== n.id) return true;
    if (p.opportunity_id !== n.opportunity_id) return true;
    if (normalizeStageKey(p.status) !== normalizeStageKey(n.status)) return true;
  }
  return false;
}

export function initKanbanView() {
  let prevApps = getState().applications;
  let prevOpps = getState().opportunities;

  subscribe((snapshot) => {
    const oppsChanged = snapshot.opportunities !== prevOpps;
    const appsChanged = snapshot.applications !== prevApps;
    if (!oppsChanged && !appsChanged) return;

    const structureChanged = haveAppsChangedSignificantly(prevApps, snapshot.applications);
    prevApps = snapshot.applications;
    prevOpps = snapshot.opportunities;

    if (oppsChanged || structureChanged) {
      renderKanbanView(snapshot);
    } else {
      // Light update: only sync notes in DOM if changed, without destroying cards
      snapshot.applications.forEach((app) => {
        if (!app) return;
        const textarea = document.querySelector(
          `textarea[data-action="edit-notes"][data-app-id="${app.id}"]`
        );
        if (
          textarea &&
          document.activeElement !== textarea &&
          textarea.value !== (app.notes || '')
        ) {
          textarea.value = app.notes || '';
          textarea.dataset.initialValue = app.notes || '';
        }
      });
    }
  });

  bindKanbanEvents();
  renderKanbanView(getState());
}
