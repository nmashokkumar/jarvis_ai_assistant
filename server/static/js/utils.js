/**
 * JARVIS — utils.js
 * Shared configuration, API client, formatters, toast notifications, and helpers.
 * Designed to be portable: works both when hosted on the server and when opened locally
 * or packaged inside a desktop/mobile wrapper (Electron, Tauri, Flutter, Capacitor).
 */

// ---------------------------------------------------------------------------
// Dynamic Configuration (No hardcoded endpoints)
// ---------------------------------------------------------------------------
const CONFIG = {
  /**
   * Returns the base HTTP URL for the JARVIS backend.
   * Priority:
   * 1. Explicitly saved URL in localStorage ('jarvis_server_url')
   * 2. Current origin if served over http:// or https://
   * 3. Default local dev fallback ('http://localhost:8000') when opened via file://
   */
  getServerUrl() {
    const saved = localStorage.getItem('jarvis_server_url');
    if (saved && saved.trim()) {
      return saved.trim().replace(/\/+$/, '');
    }
    if (window.location.protocol.startsWith('http')) {
      return window.location.origin;
    }
    return 'http://localhost:8000';
  },

  /**
   * Save a custom backend URL (useful for local preview or native mobile/desktop apps).
   */
  setServerUrl(url) {
    const clean = (url || '').trim().replace(/\/+$/, '');
    if (clean) {
      localStorage.setItem('jarvis_server_url', clean);
    } else {
      localStorage.removeItem('jarvis_server_url');
    }
  },

  /**
   * Converts the HTTP server URL into a WebSocket URL (ws:// or wss://).
   */
  getWsUrl(path = '/ws/chat') {
    const base = this.getServerUrl();
    const wsBase = base.replace(/^http/i, 'ws');
    return `${wsBase}${path}`;
  },

  /**
   * Returns true if the user opened index.html directly from disk (file://)
   * without a custom remote server URL configured.
   */
  isLocalFilePreview() {
    return window.location.protocol === 'file:' && !localStorage.getItem('jarvis_server_url');
  }
};

// ---------------------------------------------------------------------------
// API Client
// ---------------------------------------------------------------------------
class API {
  static get token() {
    return localStorage.getItem('jarvis_token') || '';
  }

  static _headers(extra = {}) {
    return {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${API.token}`,
      ...extra,
    };
  }

  static async _request(method, path, body = null) {
    const url = path.startsWith('http') ? path : `${CONFIG.getServerUrl()}${path}`;
    const opts = { method, headers: API._headers() };
    if (body !== null) opts.body = JSON.stringify(body);

    try {
      const res = await fetch(url, opts);
      if (res.status === 401) {
        // Token invalid – clear token and return to login screen
        localStorage.removeItem('jarvis_token');
        window.location.reload();
        return null;
      }
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`HTTP ${res.status}: ${text}`);
      }
      const ct = res.headers.get('content-type') || '';
      if (ct.includes('application/json')) return res.json();
      return res.text();
    } catch (err) {
      console.error(`[API] ${method} ${url} failed:`, err);
      throw err;
    }
  }

  static get(path)              { return API._request('GET',    path); }
  static post(path, data = {})  { return API._request('POST',   path, data); }
  static patch(path, data = {}) { return API._request('PATCH',  path, data); }
  static delete(path)           { return API._request('DELETE', path); }

  /**
   * Checks backend health without requiring auth token.
   */
  static async checkHealth() {
    const url = `${CONFIG.getServerUrl()}/api/health`;
    const res = await fetch(url, { method: 'GET' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }
}

// ---------------------------------------------------------------------------
// Date / Time helpers
// ---------------------------------------------------------------------------
function formatDateTime(isoString) {
  if (!isoString) return '';
  const d = new Date(isoString);
  if (isNaN(d)) return isoString;
  return d.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

function timeAgo(isoString) {
  if (!isoString) return '';
  const d = new Date(isoString);
  if (isNaN(d)) return isoString;
  const diff = Math.floor((Date.now() - d.getTime()) / 1000);
  if (diff < 60)         return `${diff}s ago`;
  if (diff < 3600)       return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400)      return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)}d ago`;
  return formatDateTime(isoString);
}

// ---------------------------------------------------------------------------
// Toast notifications
// ---------------------------------------------------------------------------
function showToast(message, type = 'info', duration = 4000) {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'toast-container';
    document.body.appendChild(container);
  }

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;

  const icons = { info: 'ℹ', success: '✓', error: '✗', reminder: '🔔' };
  toast.innerHTML = `
    <span class="toast-icon">${icons[type] || 'ℹ'}</span>
    <span class="toast-msg">${escapeHtml(message)}</span>
    <button class="toast-close" aria-label="Close">×</button>
  `;

  toast.querySelector('.toast-close').addEventListener('click', () => toast.remove());
  container.appendChild(toast);

  if (duration > 0) {
    setTimeout(() => {
      if (toast.parentElement) {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(8px)';
        toast.style.transition = 'all 0.25s ease';
        setTimeout(() => toast.remove(), 250);
      }
    }, duration);
  }

  return toast;
}

// ---------------------------------------------------------------------------
// Misc helpers
// ---------------------------------------------------------------------------
function generateId() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

function debounce(fn, ms) {
  let timer;
  return function (...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), ms);
  };
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function stripMarkdown(text) {
  return text
    .replace(/```[\s\S]*?```/g, '')
    .replace(/`[^`]*`/g, '')
    .replace(/#{1,6}\s+/g, '')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/\*(.+?)\*/g, '$1')
    .replace(/~~(.+?)~~/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/^[-*+]\s+/gm, '')
    .replace(/^\d+\.\s+/gm, '')
    .replace(/>\s+/g, '')
    .replace(/\n{2,}/g, ' ')
    .trim();
}

function copyToClipboard(text) {
  if (navigator.clipboard) {
    navigator.clipboard.writeText(text).then(() => showToast('Copied to clipboard', 'success', 1500));
  } else {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
    showToast('Copied to clipboard', 'success', 1500);
  }
}
