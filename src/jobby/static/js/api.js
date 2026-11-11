/**
 * Centralized REST HTTP client for Jobby.
 * All fetch() calls flow through request() so 401/4xx/5xx and network errors
 * produce typed errors and trigger sober user notifications (never silent).
 */

export class ApiError extends Error {
  constructor(message, { status = 0, code = 'API_ERROR', detail = null } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.detail = detail;
    this.isAuthError = status === 401;
  }
}

let toastNotifier = null;
let lastToastKey = '';
let lastToastAt = 0;

/**
 * Registers the UI toast callback used when HTTP or network errors occur.
 */
export function setApiToastNotifier(fn) {
  toastNotifier = typeof fn === 'function' ? fn : null;
}

function notifyError(err) {
  if (!toastNotifier) return;
  const key = `${err.code}:${err.status}:${err.message}`;
  const now = Date.now();
  if (key === lastToastKey && now - lastToastAt < 1500) {
    return;
  }
  lastToastKey = key;
  lastToastAt = now;
  toastNotifier(err.message, 'error', err);
}

async function parseErrorDetail(response) {
  const contentType = response.headers.get('content-type') || '';
  try {
    if (contentType.includes('application/json')) {
      const payload = await response.json();
      if (payload && typeof payload.detail === 'string') return payload.detail;
      if (payload && Array.isArray(payload.detail)) {
        const msgs = payload.detail
          .map((item) => {
            if (typeof item === 'string') return item.trim();
            if (item && typeof item.msg === 'string') return item.msg.trim();
            return '';
          })
          .filter(Boolean);
        if (msgs.length > 0) return msgs.join('; ');
      }
      if (payload && typeof payload.message === 'string') return payload.message;
    } else {
      const text = (await response.text()).trim();
      if (text) return text.slice(0, 180);
    }
  } catch {
    // Ignore parse failure on error body and fall back to status text
  }
  return null;
}

function normalizeHeaders(customHeaders) {
  if (!customHeaders) return {};
  if (typeof Headers !== 'undefined' && customHeaders instanceof Headers) {
    return Object.fromEntries(customHeaders.entries());
  }
  return { ...customHeaders };
}

function isRawFetchBody(body) {
  if (body === undefined || body === null || typeof body === 'string') return true;
  if (typeof FormData !== 'undefined' && body instanceof FormData) return true;
  if (typeof URLSearchParams !== 'undefined' && body instanceof URLSearchParams) return true;
  if (typeof Blob !== 'undefined' && body instanceof Blob) return true;
  if (typeof ArrayBuffer !== 'undefined' && (body instanceof ArrayBuffer || ArrayBuffer.isView(body))) {
    return true;
  }
  return false;
}

/**
 * Performs an HTTP request with unified error typing and toast reporting.
 */
export async function request(path, options = {}) {
  const { silent = false, headers: customHeaders = {}, body, ...fetchOptions } = options;

  const headers = normalizeHeaders(customHeaders);
  let preparedBody = body;
  if (!isRawFetchBody(body)) {
    const hasContentType = Object.keys(headers).some(
      (key) => key.toLowerCase() === 'content-type'
    );
    if (!hasContentType) {
      headers['Content-Type'] = 'application/json';
    }
    preparedBody = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(path, {
      credentials: 'same-origin',
      ...fetchOptions,
      headers,
      body: preparedBody,
    });
  } catch (networkErr) {
    const err = new ApiError('Connexion au serveur impossible. Vérifiez votre réseau.', {
      status: 0,
      code: 'NETWORK_ERROR',
      detail: networkErr instanceof Error ? networkErr.message : String(networkErr),
    });
    if (!silent) notifyError(err);
    throw err;
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response);
    let message;
    let code;

    if (response.status === 401) {
      code = 'UNAUTHORIZED';
      message = detail || 'Authentification requise (401).';
    } else if (response.status >= 500) {
      code = 'SERVER_ERROR';
      message = detail || `Erreur serveur (${response.status}).`;
    } else {
      code = 'HTTP_ERROR';
      message = detail || `Requête refusée (${response.status}).`;
    }

    const err = new ApiError(message, {
      status: response.status,
      code,
      detail,
    });
    if (!silent) notifyError(err);
    throw err;
  }

  if (response.status === 204) {
    return null;
  }

  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    try {
      return await response.json();
    } catch (parseErr) {
      const err = new ApiError('Réponse JSON invalide reçue du serveur.', {
        status: response.status,
        code: 'INVALID_RESPONSE',
        detail: parseErr instanceof Error ? parseErr.message : String(parseErr),
      });
      if (!silent) notifyError(err);
      throw err;
    }
  }
  return response.text();
}

export function fetchOpportunities({ limit = 100, offset = 0, status, source } = {}) {
  const params = new URLSearchParams();
  if (limit !== undefined) params.set('limit', String(limit));
  if (offset) params.set('offset', String(offset));
  if (status) params.set('status', status);
  if (source) params.set('source', source);
  const qs = params.toString();
  return request(`/api/opportunities${qs ? `?${qs}` : ''}`);
}

export function fetchApplications() {
  return request('/api/applications');
}

export function fetchConfig() {
  return request('/api/config');
}

export function updateOpportunityStatus(oppId, status) {
  return request(`/api/opportunities/${encodeURIComponent(oppId)}/status`, {
    method: 'PATCH',
    body: { status },
  });
}

export function updateApplicationStatus(appId, status) {
  return request(`/api/applications/${encodeURIComponent(appId)}/status`, {
    method: 'PATCH',
    body: { status },
  });
}
