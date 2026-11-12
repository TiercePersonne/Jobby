import { generateCoverLetter } from '../api.js';
import { getState, setActiveModal } from '../state.js';
import { escapeHtml, showToast } from '../utils.js';

let activeOppId = null;
let requestCounter = 0;

export function openLetterModal(oppId, { force = false } = {}) {
  if (!oppId) return;
  activeOppId = oppId;
  const currentReq = ++requestCounter;

  const modal = document.getElementById('modal-letter');
  const titleEl = document.getElementById('letter-opp-title');
  const providerEl = document.getElementById('letter-provider');
  const loadingEl = document.getElementById('letter-loading');
  const errorEl = document.getElementById('letter-error');
  const bodyEl = document.getElementById('letter-body');
  const textEl = document.getElementById('letter-text');
  const regenBtn = document.getElementById('btn-letter-regenerate');
  const copyBtn = document.getElementById('btn-letter-copy');

  if (!modal) return;

  // Retrieve opportunity metadata from state
  const snapshot = getState();
  const opp = (snapshot.opportunities || []).find((o) => o.id === oppId) ||
              (snapshot.applications || []).find((a) => a.opportunity_id === oppId || a.id === oppId)?.opportunity || {};

  const oppTitle = opp.title || `Opportunité ${oppId}`;
  const company = opp.company || '';
  if (titleEl) {
    titleEl.textContent = company ? `${oppTitle} — ${company}` : oppTitle;
  }

  // Show modal
  modal.classList.remove('hidden');
  modal.classList.add('flex');
  modal.setAttribute('aria-hidden', 'false');
  setActiveModal('modal-letter');

  // UI state: loading
  if (loadingEl) loadingEl.classList.remove('hidden');
  if (errorEl) {
    errorEl.classList.add('hidden');
    errorEl.textContent = '';
  }
  if (bodyEl) bodyEl.classList.add('hidden');
  if (providerEl) providerEl.innerHTML = '';
  if (regenBtn) regenBtn.disabled = true;
  if (copyBtn) copyBtn.disabled = true;

  generateCoverLetter(oppId, { force })
    .then((data) => {
      if (currentReq !== requestCounter) return; // Stale request guard
      if (loadingEl) loadingEl.classList.add('hidden');
      if (bodyEl) bodyEl.classList.remove('hidden');
      if (textEl) {
        textEl.value = data.cover_letter || '';
        textEl.focus();
      }
      if (regenBtn) regenBtn.disabled = false;
      if (copyBtn) copyBtn.disabled = false;

      // Render provider provenance
      if (providerEl) {
        const isGemini = data.provider_used === 'gemini' && !data.used_fallback;
        if (isGemini) {
          providerEl.innerHTML = `
            <span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-medium bg-[#F0FDF4] text-[#15803D] border border-[#BBF7D0]">
              <span class="w-1.5 h-1.5 rounded-full bg-[#15803D]"></span>
              Gemini 1.5 Flash (Ma Voix appliquée)
            </span>
          `;
        } else {
          const reason = data.fallback_reason ? ` (${escapeHtml(data.fallback_reason)})` : '';
          providerEl.innerHTML = `
            <span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-medium bg-[#FEFCE8] text-[#854D0E] border border-[#FEF08A]">
              <span class="w-1.5 h-1.5 rounded-full bg-[#EAB308]"></span>
              Repli heuristique${reason}
            </span>
          `;
        }
      }
    })
    .catch((err) => {
      if (currentReq !== requestCounter) return;
      if (loadingEl) loadingEl.classList.add('hidden');
      if (errorEl) {
        errorEl.classList.remove('hidden');
        errorEl.textContent = err?.detail || err?.message || 'Erreur lors de la génération de la lettre.';
      }
      if (regenBtn) regenBtn.disabled = false;
      if (copyBtn) copyBtn.disabled = true;
    });
}

export function initLetterModal() {
  const modal = document.getElementById('modal-letter');
  if (!modal) return;

  const regenBtn = document.getElementById('btn-letter-regenerate');
  const copyBtn = document.getElementById('btn-letter-copy');
  const copyLabel = document.getElementById('btn-letter-copy-label');
  const textEl = document.getElementById('letter-text');

  if (regenBtn) {
    regenBtn.addEventListener('click', () => {
      if (activeOppId) {
        openLetterModal(activeOppId, { force: true });
      }
    });
  }

  if (copyBtn && textEl) {
    copyBtn.addEventListener('click', async () => {
      const text = textEl.value || '';
      try {
        let copied = false;
        if (navigator.clipboard && navigator.clipboard.writeText) {
          try {
            await navigator.clipboard.writeText(text);
            copied = true;
          } catch (_) {
            // Document focus or permission fallback
          }
        }
        if (!copied) {
          textEl.select();
          document.execCommand('copy');
        }
        if (copyLabel) copyLabel.textContent = 'Copié !';
        copyBtn.classList.add('bg-[#15803D]');
        setTimeout(() => {
          if (copyLabel) copyLabel.textContent = 'Copier le texte';
          copyBtn.classList.remove('bg-[#15803D]');
        }, 2000);
      } catch (err) {
        showToast('Impossible de copier dans le presse-papier.', 'error');
      }
    });
  }

  modal.querySelectorAll('[data-modal-close="modal-letter"]').forEach((closeBtn) => {
    closeBtn.addEventListener('click', () => {
      modal.classList.add('hidden');
      modal.classList.remove('flex');
      modal.setAttribute('aria-hidden', 'true');
      setActiveModal(null);
    });
  });

  // Delegated click handler on the document for any letter button
  document.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-action="generate-letter"], [data-action="letter"]');
    if (!trigger) return;

    const oppId = trigger.getAttribute('data-kanban-opp-id') ||
                  trigger.getAttribute('data-opp-id') ||
                  trigger.closest('[data-opp-id]')?.getAttribute('data-opp-id');
    if (oppId) {
      openLetterModal(oppId);
    }
  });
}
