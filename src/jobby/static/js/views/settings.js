import {
  getSettingsStatus,
  saveLinkedInCookie,
  testLinkedInConnection,
  updateConfig,
  runAutomationNow,
} from '../api.js';
import { getState, setConfig, subscribe } from '../state.js';
import { escapeHtml, showToast } from '../utils.js';

let cachedSettingsStatus = null;
let isLoadingStatus = false;

export async function fetchAndRenderStatus() {
  if (isLoadingStatus) return;
  isLoadingStatus = true;
  try {
    const data = await getSettingsStatus();
    cachedSettingsStatus = data;
    renderConnectorsTable(data);
    renderLinkedInBadge(data);
    renderGeminiStatus(data);
  } catch (err) {
    console.error('Erreur chargement statut paramètres :', err);
  } finally {
    isLoadingStatus = false;
  }
}

function renderConnectorsTable(statusData) {
  const tbody = document.getElementById('connectors-table-body');
  if (!tbody) return;

  const connectors = statusData?.connectors || [];
  if (connectors.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="3" class="py-4 text-center text-[#78716C]">Aucun connecteur disponible.</td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = connectors.map((c) => {
    let badgeClass = 'bg-[#F0FDF4] text-[#15803D] border-[#BBF7D0]'; // vert
    if (c.status === 'missing' || c.status === 'warning') {
      badgeClass = 'bg-[#FEFCE8] text-[#B45309] border-[#FEF08A]'; // ambre
    } else if (c.status === 'inactive' || c.status === 'error') {
      badgeClass = 'bg-[#F5F5F4] text-[#78716C] border-[#E7E5E4]'; // gris
    }

    let typeLabel = 'Public';
    if (c.type === 'authenticated') typeLabel = 'Session / Cookie';
    else if (c.type === 'restricted') typeLabel = 'Restreint';

    return `
      <tr class="hover:bg-[#FAF9F6]">
        <td class="py-3 px-3 font-medium text-[#1C1917]">${escapeHtml(c.name)}</td>
        <td class="py-3 px-3 text-[#57534E]">${escapeHtml(typeLabel)}</td>
        <td class="py-3 px-3">
          <span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border ${badgeClass}">
            ${escapeHtml(c.label || c.status)}
          </span>
        </td>
      </tr>
    `;
  }).join('');
}

function renderLinkedInBadge(statusData) {
  const badge = document.getElementById('linkedin-status-badge');
  if (!badge) return;

  const isConnected = Boolean(statusData?.linkedin_connected);
  if (isConnected) {
    badge.className = 'px-2.5 py-1 rounded text-xs font-medium bg-[#F0FDF4] text-[#15803D] border border-[#BBF7D0]';
    badge.textContent = 'Connecté (li_at actif)';
  } else {
    badge.className = 'px-2.5 py-1 rounded text-xs font-medium bg-[#FEFCE8] text-[#B45309] border border-[#FEF08A]';
    badge.textContent = 'Non configuré';
  }
}

function renderGeminiStatus(statusData) {
  const statusEl = document.getElementById('gemini-key-status');
  if (!statusEl) return;

  if (statusData?.has_gemini_key) {
    const masked = statusData.gemini_key_masked || 'Configurée';
    statusEl.innerHTML = `<span class="text-[#15803D] font-medium">Clé active :</span> <code class="font-mono text-xs">${escapeHtml(masked)}</code>`;
  } else {
    statusEl.innerHTML = '<span class="text-[#B45309]">Aucune clé Gemini configurée (repli heuristique actif).</span>';
  }
}

export function renderSettingsView(snapshot = getState()) {
  const summaryEl = document.getElementById('settings-shell-summary');
  if (!summaryEl) return;

  if (snapshot.configError) {
    summaryEl.textContent = 'Configuration indisponible pour le moment.';
    return;
  }

  const cfg = snapshot.config;
  if (!cfg) {
    summaryEl.textContent = 'Configuration en attente de chargement...';
    return;
  }

  const rolesCount = Array.isArray(cfg.target_roles) ? cfg.target_roles.length : 0;
  const provider = cfg.ai_provider || 'auto';
  summaryEl.textContent = `${rolesCount} rôle${rolesCount > 1 ? 's' : ''} configuré${rolesCount > 1 ? 's' : ''} • Fournisseur IA : ${provider}`;

  // Fill form inputs if not dirty
  const inputQueries = document.getElementById('input-settings-queries');
  if (inputQueries && !inputQueries.matches(':focus')) {
    inputQueries.value = queries.join(', ');
  }

  const inputTimes = document.getElementById('input-settings-times');
  if (inputTimes && !inputTimes.matches(':focus')) {
    inputTimes.value = Array.isArray(cfg.auto_search_times) ? cfg.auto_search_times.join(', ') : '12:00, 17:00';
  }
}

export function initSettingsView() {
  let prevState = getState();
  subscribe((snapshot) => {
    if (
      snapshot.config !== prevState.config ||
      snapshot.configError !== prevState.configError
    ) {
      prevState = snapshot;
      renderSettingsView(snapshot);
    }
  });

  renderSettingsView(getState());
  fetchAndRenderStatus();

  // 1. Bouton actualiser connecteurs
  const refreshBtn = document.getElementById('btn-refresh-connectors');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', () => {
      fetchAndRenderStatus();
      showToast('Statuts des connecteurs actualisés.');
    });
  }

  // 2. Bascule du tiroir de saisie manuelle cookie
  const toggleManualBtn = document.getElementById('btn-toggle-manual-cookie');
  const manualDrawer = document.getElementById('linkedin-manual-drawer');
  if (toggleManualBtn && manualDrawer) {
    toggleManualBtn.addEventListener('click', () => {
      const isHidden = manualDrawer.classList.contains('hidden');
      if (isHidden) {
        manualDrawer.classList.remove('hidden');
        toggleManualBtn.textContent = 'Masquer la saisie manuelle ↑';
      } else {
        manualDrawer.classList.add('hidden');
        toggleManualBtn.textContent = 'Saisie manuelle du cookie ↓';
      }
    });
  }

  // 3. Enregistrement manuel cookie LinkedIn
  const saveCookieBtn = document.getElementById('btn-save-linkedin-cookie');
  const inputCookie = document.getElementById('input-linkedin-li-at');
  if (saveCookieBtn && inputCookie) {
    saveCookieBtn.addEventListener('click', async () => {
      const val = inputCookie.value.trim();
      if (!val) {
        showToast('Veuillez renseigner le cookie li_at.', 'error');
        return;
      }
      try {
        saveCookieBtn.disabled = true;
        saveCookieBtn.textContent = 'Enregistrement...';
        await saveLinkedInCookie(val);
        inputCookie.value = '';
        showToast('Cookie LinkedIn li_at enregistré !', 'success');
        await fetchAndRenderStatus();
      } catch (err) {
        showToast(err.message || 'Erreur lors de l’enregistrement du cookie.', 'error');
      } finally {
        saveCookieBtn.disabled = false;
        saveCookieBtn.textContent = 'Enregistrer';
      }
    });
  }

  // 4. Test de connexion LinkedIn
  const testLinkedInBtn = document.getElementById('btn-test-linkedin');
  const testFeedback = document.getElementById('linkedin-test-feedback');
  if (testLinkedInBtn) {
    testLinkedInBtn.addEventListener('click', async () => {
      try {
        testLinkedInBtn.disabled = true;
        testLinkedInBtn.textContent = 'Vérification en cours...';
        if (testFeedback) testFeedback.classList.add('hidden');

        const res = await testLinkedInConnection();
        if (testFeedback) {
          testFeedback.classList.remove('hidden');
          if (res.valid) {
            testFeedback.className = 'text-xs p-3 rounded bg-[#F0FDF4] border border-[#BBF7D0] text-[#15803D]';
            testFeedback.textContent = res.message || 'Connexion LinkedIn active et session valide !';
            showToast('Connexion LinkedIn opérationnelle !', 'success');
          } else {
            testFeedback.className = 'text-xs p-3 rounded bg-[#FEFCE8] border border-[#FEF08A] text-[#B45309]';
            testFeedback.textContent = res.message || 'Impossible de valider la session LinkedIn.';
            showToast('Session LinkedIn non valide.', 'warning');
          }
        }
        await fetchAndRenderStatus();
      } catch (err) {
        if (testFeedback) {
          testFeedback.classList.remove('hidden');
          testFeedback.className = 'text-xs p-3 rounded bg-[#FEF2F2] border border-[#FECACA] text-[#991B1B]';
          testFeedback.textContent = err.message || 'Erreur lors du test de connexion.';
        }
        showToast('Erreur test LinkedIn.', 'error');
      } finally {
        testLinkedInBtn.disabled = false;
        testLinkedInBtn.textContent = 'Tester la connexion LinkedIn';
      }
    });
  }

  // 5. Sauvegarde formulaire veille & clé Gemini
  const form = document.getElementById('form-settings-automation');
  if (form) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const inputQueries = document.getElementById('input-settings-queries');
      const inputTimes = document.getElementById('input-settings-times');
      const inputGemini = document.getElementById('input-settings-gemini-key');
      const saveBtn = document.getElementById('btn-save-settings');

      const rawQueries = inputQueries ? inputQueries.value : '';
      const rawTimes = inputTimes ? inputTimes.value : '';
      const newGeminiKey = inputGemini ? inputGemini.value.trim() : '';

      const parsedQueries = rawQueries
        .split(',')
        .map((q) => q.trim())
        .filter(Boolean);

      const parsedTimes = rawTimes
        .split(',')
        .map((t) => t.trim())
        .filter(Boolean);

      const currentConfig = getState().config || {};
      const updated = {
        ...currentConfig,
        auto_search_queries: parsedQueries.length > 0 ? parsedQueries : currentConfig.auto_search_queries,
        target_roles: parsedQueries.length > 0 ? parsedQueries : currentConfig.target_roles,
        auto_search_times: parsedTimes.length > 0 ? parsedTimes : currentConfig.auto_search_times,
      };

      if (newGeminiKey) {
        updated.ai_api_key = newGeminiKey;
        updated.ai_provider = 'gemini';
      }

      try {
        if (saveBtn) {
          saveBtn.disabled = true;
          saveBtn.textContent = 'Enregistrement...';
        }
        const resp = await updateConfig(updated);
        if (resp && resp.config) {
          setConfig(resp.config);
        }
        if (inputGemini) inputGemini.value = '';
        showToast('Paramètres enregistrés avec succès !', 'success');
        await fetchAndRenderStatus();
      } catch (err) {
        showToast(err.message || 'Erreur lors de l’enregistrement des paramètres.', 'error');
      } finally {
        if (saveBtn) {
          saveBtn.disabled = false;
          saveBtn.textContent = 'Enregistrer les paramètres';
        }
      }
    });
  }

  // 6. Exécuter la chasse maintenant
  const runNowBtn = document.getElementById('btn-run-automation-now');
  const runFeedback = document.getElementById('automation-run-feedback');
  if (runNowBtn) {
    runNowBtn.addEventListener('click', async () => {
      try {
        runNowBtn.disabled = true;
        runNowBtn.textContent = 'Chasse en cours...';
        if (runFeedback) runFeedback.classList.add('hidden');

        const res = await runAutomationNow();
        if (runFeedback) {
          runFeedback.classList.remove('hidden');
          runFeedback.className = 'text-xs p-3 rounded bg-[#F0FDF4] border border-[#BBF7D0] text-[#15803D]';
          runFeedback.textContent = res.message || 'Chasse automatique terminée avec succès.';
        }
        showToast(res.message || 'Chasse terminée !', 'success');
      } catch (err) {
        if (runFeedback) {
          runFeedback.classList.remove('hidden');
          runFeedback.className = 'text-xs p-3 rounded bg-[#FEF2F2] border border-[#FECACA] text-[#991B1B]';
          runFeedback.textContent = err.message || 'Erreur lors de l’exécution de la chasse.';
        }
        showToast('Erreur exécution chasse.', 'error');
      } finally {
        runNowBtn.disabled = false;
        runNowBtn.textContent = 'Exécuter la chasse maintenant';
      }
    });
  }
}
