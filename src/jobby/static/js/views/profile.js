/**
 * Mon Profil View Controller (Warm Porcelain).
 * Manages candidate dossier: CV upload (PDF/TXT/MD), links, skills, and "Ma Voix" writing sample.
 * Strictly decoupled from Flux & Chasse and ideal_job_brief (AD-2).
 */

import { getState, subscribe, setProfile } from '../state.js';
import { saveProfile, uploadProfileCv } from '../api.js';
import { showToast } from '../utils.js';

let profileDirty = false;
let isSavingProfile = false;
let isUploadingCv = false;
let isBound = false;

export function isProfileDirty() {
  return profileDirty;
}

export function setProfileDirty(val) {
  profileDirty = Boolean(val);
}

function getTrimmedVal(id) {
  const el = document.getElementById(id);
  if (!el) return null;
  const val = String(el.value || '').trim();
  return val || null;
}

function parseCommaList(id) {
  const el = document.getElementById(id);
  if (!el) return [];
  const val = String(el.value || '').trim();
  if (!val) return [];
  return val
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

function renderCvStatus(profile) {
  const statusContainer = document.getElementById('profile-cv-status');
  const badge = document.getElementById('profile-cv-status-badge');
  const filenameEl = document.getElementById('profile-cv-filename');
  const charsEl = document.getElementById('profile-cv-characters');
  const previewEl = document.getElementById('profile-cv-preview');

  if (!statusContainer || !filenameEl) return;

  const hasCv = Boolean(profile && (profile.cv_filename || profile.cv_text));

  if (hasCv) {
    const filename = profile.cv_filename || 'CV candidat';
    const textLen = profile.cv_text ? profile.cv_text.length : 0;
    const preview = profile.cv_text
      ? profile.cv_text.slice(0, 300) + (profile.cv_text.length > 300 ? '...' : '')
      : '';

    filenameEl.textContent = `Actif : ${filename}`;
    if (charsEl) {
      charsEl.textContent = textLen > 0 ? `${textLen} caractères` : '';
    }
    if (previewEl) {
      previewEl.textContent = preview;
    }
    statusContainer.classList.remove('hidden');
    if (badge) badge.classList.remove('hidden');
  } else {
    statusContainer.classList.add('hidden');
    if (badge) badge.classList.add('hidden');
    filenameEl.textContent = '';
    if (charsEl) charsEl.textContent = '';
    if (previewEl) previewEl.textContent = '';
  }
}

export function renderProfileView(snapshot = getState(), { force = false } = {}) {
  const root = document.getElementById('view-profile');
  if (!root) return;

  const profile = snapshot?.profile || null;
  const config = snapshot?.config || null;

  renderCvStatus(profile);

  // Active typing protection: do not overwrite inputs if dirty unless forced
  if (profileDirty && !force) {
    return;
  }

  const setInputValue = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.value = val ?? '';
  };

  // Links
  setInputValue('profile-portfolio-url', profile?.portfolio_url ?? '');
  setInputValue('profile-github-url', profile?.github_url ?? '');
  setInputValue('profile-linkedin-url', profile?.linkedin_url ?? '');

  // Roles & Skills (profile first, fallback to config defaults only when profile is completely absent)
  let rolesText = '';
  if (profile) {
    rolesText = Array.isArray(profile.target_roles) ? profile.target_roles.join(', ') : '';
  } else if (config && Array.isArray(config.target_roles)) {
    rolesText = config.target_roles.join(', ');
  }
  setInputValue('profile-target-roles', rolesText);

  let skillsText = '';
  if (profile) {
    skillsText = Array.isArray(profile.skills) ? profile.skills.join(', ') : '';
  } else if (config && Array.isArray(config.skills_keywords)) {
    skillsText = config.skills_keywords.join(', ');
  }
  setInputValue('profile-skills', skillsText);

  // Remote & Salary (fallback to config only when !profile, not when null/cleared)
  let remoteVal = '';
  if (profile) {
    remoteVal = profile.remote_preference ?? '';
  } else if (config && config.remote_only) {
    remoteVal = 'full';
  }
  setInputValue('profile-remote-preference', remoteVal);

  let salaryVal = '';
  if (profile) {
    salaryVal = profile.min_salary != null ? String(profile.min_salary) : '';
  } else if (config && config.min_salary != null) {
    salaryVal = String(config.min_salary);
  }
  setInputValue('profile-min-salary', salaryVal);

  // Bio & Ma Voix
  setInputValue('profile-bio', profile?.bio ?? '');
  setInputValue('profile-writing-sample', profile?.writing_sample ?? '');
  setInputValue(
    'profile-writing-tone',
    profile?.writing_tone ?? 'naturel, direct, enthousiaste et percutant'
  );

  const saveStatusEl = document.getElementById('profile-save-status');
  if (saveStatusEl && !isSavingProfile) {
    saveStatusEl.textContent =
      "Isolation active : aucune modification de profil n'affecte Flux & Chasse.";
  }
}

export async function handleSaveProfile() {
  if (isSavingProfile) return;

  const btn = document.getElementById('btn-save-profile');
  const saveStatusEl = document.getElementById('profile-save-status');

  const rawSalary = getTrimmedVal('profile-min-salary');
  const parsedSalary = rawSalary ? parseInt(rawSalary, 10) : null;
  const clampedSalary = Number.isFinite(parsedSalary) ? Math.max(0, parsedSalary) : null;

  const payload = {
    portfolio_url: getTrimmedVal('profile-portfolio-url'),
    github_url: getTrimmedVal('profile-github-url'),
    linkedin_url: getTrimmedVal('profile-linkedin-url'),
    target_roles: parseCommaList('profile-target-roles'),
    skills: parseCommaList('profile-skills'),
    remote_preference: getTrimmedVal('profile-remote-preference'),
    min_salary: clampedSalary,
    bio: getTrimmedVal('profile-bio'),
    writing_sample: getTrimmedVal('profile-writing-sample'),
    writing_tone: getTrimmedVal('profile-writing-tone'),
  };

  isSavingProfile = true;
  if (btn) {
    btn.disabled = true;
    btn.setAttribute('aria-busy', 'true');
  }
  if (saveStatusEl) {
    saveStatusEl.textContent = 'Enregistrement du profil en cours...';
  }

  try {
    const res = await saveProfile(payload);
    profileDirty = false;

    const currentProfile = getState().profile || {};
    const updatedProfile = res.profile || {
      ...currentProfile,
      ...payload,
    };

    // Strict preservation of ideal_job_brief (AD-2)
    if (
      currentProfile.ideal_job_brief !== undefined &&
      updatedProfile.ideal_job_brief === undefined
    ) {
      updatedProfile.ideal_job_brief = currentProfile.ideal_job_brief;
    }

    setProfile(updatedProfile);
    renderProfileView(getState(), { force: true });
    showToast('Profil enregistré.', 'success');

    if (saveStatusEl) {
      saveStatusEl.textContent =
        "Profil enregistré. Isolation active : Flux & Chasse reste inchangé.";
    }
  } catch (err) {
    const errMsg = err?.message || "Erreur lors de l'enregistrement du profil.";
    showToast(errMsg, 'error');
    if (saveStatusEl) {
      saveStatusEl.textContent = 'Échec de la sauvegarde. Vos saisies sont conservées.';
    }
  } finally {
    isSavingProfile = false;
    if (btn) {
      btn.disabled = false;
      btn.removeAttribute('aria-busy');
    }
  }
}

export async function handleCvUpload(file) {
  if (!file) return;

  if (isUploadingCv) return;

  const validExts = ['.pdf', '.txt', '.md'];
  const ext = file.name ? file.name.substring(file.name.lastIndexOf('.')).toLowerCase() : '';
  const validMimes = ['text/plain', 'text/markdown', 'application/pdf'];

  if (!validExts.includes(ext) && !validMimes.includes(file.type)) {
    showToast('Format non supporté. Formats acceptés : .pdf, .txt, .md', 'error');
    return;
  }

  const fileInput = document.getElementById('profile-cv-file');
  const dropzone = document.getElementById('profile-cv-dropzone');
  const saveStatusEl = document.getElementById('profile-save-status');

  isUploadingCv = true;
  if (dropzone) dropzone.setAttribute('aria-busy', 'true');
  if (saveStatusEl) {
    saveStatusEl.textContent = 'Téléversement et extraction du CV en cours...';
  }

  try {
    const res = await uploadProfileCv(file);

    const currentProfile = getState().profile || {};
    const updatedProfile = res.profile || {
      ...currentProfile,
      cv_filename: res.filename,
      cv_text: res.preview || '',
    };

    // Strict preservation of ideal_job_brief (AD-2)
    if (
      currentProfile.ideal_job_brief !== undefined &&
      updatedProfile.ideal_job_brief === undefined
    ) {
      updatedProfile.ideal_job_brief = currentProfile.ideal_job_brief;
    }

    setProfile(updatedProfile);
    renderCvStatus(updatedProfile);
    showToast(`CV « ${res.filename} » téléversé avec succès.`, 'success');

    if (saveStatusEl) {
      saveStatusEl.textContent = `CV « ${res.filename} » actif (${res.characters_extracted || 0} caractères extraits).`;
    }
  } catch (err) {
    const errMsg = err?.message || 'Erreur lors du téléversement du CV.';
    showToast(errMsg, 'error');
    if (saveStatusEl) {
      saveStatusEl.textContent = 'Échec du téléversement du CV.';
    }
  } finally {
    isUploadingCv = false;
    if (dropzone) dropzone.removeAttribute('aria-busy');
    if (fileInput) fileInput.value = '';
  }
}

function bindProfileEvents() {
  if (isBound) return;
  isBound = true;

  const form = document.getElementById('profile-form');
  const saveBtn = document.getElementById('btn-save-profile');
  const dropzone = document.getElementById('profile-cv-dropzone');
  const fileInput = document.getElementById('profile-cv-file');
  const browseBtn = document.getElementById('btn-profile-browse-cv');

  // Mark dirty on any user edit
  if (form) {
    form.addEventListener('input', () => {
      profileDirty = true;
    });
    form.addEventListener('change', () => {
      profileDirty = true;
    });
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      handleSaveProfile();
    });
  }

  if (saveBtn) {
    saveBtn.addEventListener('click', (e) => {
      e.preventDefault();
      handleSaveProfile();
    });
  }

  // File browse click
  if (browseBtn && fileInput) {
    browseBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      fileInput.click();
    });
  }

  // Dropzone drag-and-drop & click
  if (dropzone && fileInput) {
    dropzone.addEventListener('click', (e) => {
      if (e.target !== fileInput && e.target !== browseBtn) {
        fileInput.click();
      }
    });

    dropzone.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        fileInput.click();
      }
    });

    const preventDefaults = (e) => {
      e.preventDefault();
      e.stopPropagation();
    };

    ['dragenter', 'dragover'].forEach((eventName) => {
      dropzone.addEventListener(eventName, (e) => {
        preventDefaults(e);
        dropzone.classList.add('is-dragover');
      });
    });

    ['dragleave', 'dragend'].forEach((eventName) => {
      dropzone.addEventListener(eventName, (e) => {
        preventDefaults(e);
        dropzone.classList.remove('is-dragover');
      });
    });

    dropzone.addEventListener('drop', (e) => {
      preventDefaults(e);
      dropzone.classList.remove('is-dragover');
      const files = e.dataTransfer?.files;
      if (files && files.length > 0) {
        handleCvUpload(files[0]);
      }
    });
  }

  // File input change
  if (fileInput) {
    fileInput.addEventListener('change', () => {
      const files = fileInput.files;
      if (files && files.length > 0) {
        handleCvUpload(files[0]);
      }
    });
  }
}

export function initProfileView() {
  bindProfileEvents();

  let prevProfile = getState().profile;
  let prevConfig = getState().config;

  subscribe((snapshot) => {
    if (snapshot.activeView === 'profile') {
      renderProfileView(snapshot);
    } else if (snapshot.profile !== prevProfile || snapshot.config !== prevConfig) {
      // Background store update while on other views
      renderProfileView(snapshot);
    }
    prevProfile = snapshot.profile;
    prevConfig = snapshot.config;
  });

  renderProfileView(getState(), { force: true });
}
