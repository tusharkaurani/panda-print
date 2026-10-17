// fetch wrapper for /api/*. Handles the CSRF token for state-changing calls and never throws:
// it resolves to { ok, status, data, error, retryAfter } so callers can render errors in place.
let csrfToken = null;

async function csrf(force = false) {
  if (!csrfToken || force) {
    const r = await fetch('/api/csrf', { credentials: 'same-origin' });
    csrfToken = (await r.json()).token;
  }
  return csrfToken;
}

export async function api(path, { method = 'GET', body } = {}, retried = false) {
  try {
    const init = { method, credentials: 'same-origin', headers: {} };
    if (method !== 'GET') {
      init.headers['X-CSRF-Token'] = await csrf(retried);
      if (body instanceof FormData) {
        init.body = body;  // a file upload: the browser sets the multipart boundary itself
      } else {
        init.headers['Content-Type'] = 'application/json';
        init.body = JSON.stringify(body ?? {});
      }
    }
    const res = await fetch('/api' + path, init);
    if (res.ok && (res.headers.get('Content-Type') || '').startsWith('application/pdf')) {  // a generated file, not JSON
      return { ok: true, status: res.status, data: await res.blob(), error: null, retryAfter: 0 };
    }
    if (res.ok && (res.headers.get('Content-Type') || '').startsWith('text/plain')) {  // plain text, such as a file to copy
      return { ok: true, status: res.status, data: await res.text(), error: null, retryAfter: 0 };
    }
    let json = null;
    try { json = await res.json(); } catch { /* not JSON */ }
    if (res.status === 403 && method !== 'GET' && !retried) return api(path, { method, body }, true);
    const ok = res.ok && json && json.ok !== false;
    return {
      ok, status: res.status, data: json?.data ?? json, fetchedAt: json?.fetched_at,
      error: ok ? null : (json?.error || { message: `The server answered ${res.status}.` }),
      retryAfter: Number(res.headers.get('Retry-After')) || json?.error?.retry_after || 0,
    };
  } catch {
    return { ok: false, status: 0, data: null, error: { message: "Can't reach the panda-print server.", hint: 'Check your connection and try again.' } };
  }
}
