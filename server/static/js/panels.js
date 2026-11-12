/**
 * JARVIS — panels.js
 * Side panel controller for Reminders, Todos, Notes, Calendar & System Settings.
 * Designed to work gracefully both when connected to the backend and during offline UI preview.
 */

class PanelManager {
  constructor() {
    this.drawer = document.getElementById('panel-drawer');
    this.titleEl = document.getElementById('panel-title');
    this.bodyEl = document.getElementById('panel-body');
    this.closeBtn = document.getElementById('panel-close-btn');
    this.activePanel = null;

    this._bindEvents();
  }

  _bindEvents() {
    if (this.closeBtn) {
      this.closeBtn.addEventListener('click', () => this.close());
    }

    // Close drawer when clicking the dimmed backdrop
    if (this.drawer) {
      this.drawer.addEventListener('click', (e) => {
        if (e.target === this.drawer) this.close();
      });
    }

    // ESC key closes drawer
    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.isOpen()) this.close();
    });
  }

  isOpen() {
    return this.drawer && this.drawer.classList.contains('open');
  }

  open(panelType) {
    if (!this.drawer) return;
    this.activePanel = panelType;
    this.drawer.classList.add('open');
    this.drawer.setAttribute('aria-hidden', 'false');

    // Highlight active toolbar button
    document.querySelectorAll('.panel-nav-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.panel === panelType);
    });

    switch (panelType) {
      case 'reminders':
        this.renderReminders();
        break;
      case 'todos':
        this.renderTodos();
        break;
      case 'notes':
        this.renderNotes();
        break;
      case 'calendar':
        this.renderCalendar();
        break;
      case 'settings':
        this.renderSettings();
        break;
      default:
        this.close();
    }
  }

  close() {
    if (!this.drawer) return;
    this.drawer.classList.remove('open');
    this.drawer.setAttribute('aria-hidden', 'true');
    this.activePanel = null;
    document.querySelectorAll('.panel-nav-btn').forEach(btn => btn.classList.remove('active'));
  }

  _renderOfflineMessage(sectionName) {
    return `
      <div class="panel-empty">
        <div style="font-size:1.5rem; margin-bottom:0.5rem;">🔌</div>
        <div>Server not connected.</div>
        <div style="font-size:0.78rem; color:var(--text-dim); margin-top:0.4rem;">
          Connect to your JARVIS server in <strong>System Settings (⚙️)</strong> to view and manage ${escapeHtml(sectionName)}.
        </div>
      </div>
    `;
  }

  // ---------------------------------------------------------------------------
  // Reminders Panel
  // ---------------------------------------------------------------------------
  async renderReminders() {
    this.titleEl.innerHTML = `<span>⏰ Reminders & Alarms</span>`;
    this.bodyEl.innerHTML = `
      <div class="panel-add-form">
        <input type="text" id="rem-title" placeholder="Reminder title (e.g. Call Mom, Buy coffee)" class="hud-input">
        <div class="form-row">
          <input type="datetime-local" id="rem-time" class="hud-input">
          <button id="rem-add-btn" class="hud-btn hud-btn-primary">+ Add</button>
        </div>
      </div>
      <div id="reminders-list" class="panel-list"><div class="panel-loading">Loading reminders...</div></div>
    `;

    document.getElementById('rem-add-btn').addEventListener('click', () => this._addReminder());

    const listEl = document.getElementById('reminders-list');
    try {
      const data = await API.get('/api/reminders');
      const reminders = Array.isArray(data) ? data : (data.reminders || []);

      if (!reminders.length) {
        listEl.innerHTML = `<div class="panel-empty">No active reminders. You're all clear, Boss.</div>`;
        return;
      }

      listEl.innerHTML = reminders.map(r => `
        <div class="panel-card ${r.is_completed ? 'completed' : ''}" data-id="${r.id}">
          <div class="card-header">
            <span class="card-title">${escapeHtml(r.title)}</span>
            <button class="card-action-del" onclick="panelManager.deleteReminder(${r.id})" title="Delete">🗑</button>
          </div>
          <div class="card-meta">
            <span>📅 ${formatDateTime(r.remind_at)}</span>
            ${r.repeat && r.repeat !== 'none' ? `<span class="badge">🔁 ${escapeHtml(r.repeat)}</span>` : ''}
          </div>
        </div>
      `).join('');
    } catch (err) {
      listEl.innerHTML = this._renderOfflineMessage('reminders');
    }
  }

  async _addReminder() {
    const title = document.getElementById('rem-title').value.trim();
    const time = document.getElementById('rem-time').value;
    if (!title || !time) {
      showToast('Please specify title and time', 'error');
      return;
    }
    try {
      await API.post('/api/reminders', { title, remind_at: time });
      showToast('Reminder saved', 'success');
      this.renderReminders();
    } catch (err) {
      showToast('Cannot save: server not connected', 'error');
    }
  }

  async deleteReminder(id) {
    try {
      await API.delete(`/api/reminders/${id}`);
      showToast('Reminder deleted', 'info');
      this.renderReminders();
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  // ---------------------------------------------------------------------------
  // Todos Panel
  // ---------------------------------------------------------------------------
  async renderTodos() {
    this.titleEl.innerHTML = `<span>✅ Tasks & To-Dos</span>`;
    this.bodyEl.innerHTML = `
      <div class="panel-add-form">
        <div class="form-row">
          <input type="text" id="todo-input" placeholder="New task..." class="hud-input">
          <button id="todo-add-btn" class="hud-btn hud-btn-primary">+ Add</button>
        </div>
      </div>
      <div id="todos-list" class="panel-list"><div class="panel-loading">Loading tasks...</div></div>
    `;

    document.getElementById('todo-add-btn').addEventListener('click', () => this._addTodo());
    document.getElementById('todo-input').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') this._addTodo();
    });

    const listEl = document.getElementById('todos-list');
    try {
      const data = await API.get('/api/notes?category=todo');
      const todos = Array.isArray(data) ? data : (data.notes || []);

      if (!todos.length) {
        listEl.innerHTML = `<div class="panel-empty">No active tasks. Nice work, Boss!</div>`;
        return;
      }

      listEl.innerHTML = todos.map(t => `
        <div class="panel-card todo-card ${t.is_completed ? 'completed' : ''}" data-id="${t.id}">
          <label class="todo-label">
            <input type="checkbox" ${t.is_completed ? 'checked' : ''} onchange="panelManager.toggleTodo(${t.id}, this.checked)">
            <span class="todo-text">${escapeHtml(t.title)}</span>
          </label>
          <button class="card-action-del" onclick="panelManager.deleteNote(${t.id}, 'todos')" title="Delete">🗑</button>
        </div>
      `).join('');
    } catch (err) {
      listEl.innerHTML = this._renderOfflineMessage('tasks');
    }
  }

  async _addTodo() {
    const input = document.getElementById('todo-input');
    const title = input.value.trim();
    if (!title) return;
    try {
      await API.post('/api/notes', { title, content: '', category: 'todo', priority: 3 });
      input.value = '';
      this.renderTodos();
    } catch (err) {
      showToast('Cannot add task: server not connected', 'error');
    }
  }

  async toggleTodo(id, completed) {
    try {
      await API.patch(`/api/notes/${id}/complete`, { is_completed: completed });
      this.renderTodos();
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  // ---------------------------------------------------------------------------
  // Notes Panel
  // ---------------------------------------------------------------------------
  async renderNotes() {
    this.titleEl.innerHTML = `<span>📝 Notes</span>`;
    this.bodyEl.innerHTML = `
      <div class="panel-add-form">
        <input type="text" id="note-title" placeholder="Note title..." class="hud-input">
        <textarea id="note-body" placeholder="Write your note here..." rows="3" class="hud-input"></textarea>
        <button id="note-add-btn" class="hud-btn hud-btn-primary">+ Save Note</button>
      </div>
      <div id="notes-list" class="panel-list"><div class="panel-loading">Loading notes...</div></div>
    `;

    document.getElementById('note-add-btn').addEventListener('click', () => this._addNote());

    const listEl = document.getElementById('notes-list');
    try {
      const data = await API.get('/api/notes?category=note');
      const notes = Array.isArray(data) ? data : (data.notes || []);

      if (!notes.length) {
        listEl.innerHTML = `<div class="panel-empty">No notes saved yet.</div>`;
        return;
      }

      listEl.innerHTML = notes.map(n => `
        <div class="panel-card note-card" data-id="${n.id}">
          <div class="card-header">
            <span class="card-title">${escapeHtml(n.title)}</span>
            <button class="card-action-del" onclick="panelManager.deleteNote(${n.id}, 'notes')" title="Delete">🗑</button>
          </div>
          <p class="card-body-text">${escapeHtml(n.content || '')}</p>
          <div class="card-meta">
            <span>🕒 ${timeAgo(n.updated_at || n.created_at)}</span>
          </div>
        </div>
      `).join('');
    } catch (err) {
      listEl.innerHTML = this._renderOfflineMessage('notes');
    }
  }

  async _addNote() {
    const title = document.getElementById('note-title').value.trim();
    const content = document.getElementById('note-body').value.trim();
    if (!title) {
      showToast('Note requires a title', 'error');
      return;
    }
    try {
      await API.post('/api/notes', { title, content, category: 'note' });
      showToast('Note saved', 'success');
      this.renderNotes();
    } catch (err) {
      showToast('Cannot save note: server not connected', 'error');
    }
  }

  async deleteNote(id, returnPanel = 'notes') {
    try {
      await API.delete(`/api/notes/${id}`);
      showToast('Deleted', 'info');
      if (returnPanel === 'todos') this.renderTodos();
      else this.renderNotes();
    } catch (err) {
      showToast(err.message, 'error');
    }
  }

  // ---------------------------------------------------------------------------
  // Calendar Panel
  // ---------------------------------------------------------------------------
  async renderCalendar() {
    this.titleEl.innerHTML = `<span>📅 Schedule & Calendar</span>`;
    this.bodyEl.innerHTML = `
      <div class="panel-add-form">
        <input type="text" id="cal-title" placeholder="Event or meeting title..." class="hud-input">
        <div class="form-row">
          <input type="datetime-local" id="cal-start" class="hud-input">
          <button id="cal-add-btn" class="hud-btn hud-btn-primary">+ Add</button>
        </div>
      </div>
      <div id="calendar-list" class="panel-list"><div class="panel-loading">Loading schedule...</div></div>
    `;

    document.getElementById('cal-add-btn').addEventListener('click', () => this._addCalendarEvent());

    const listEl = document.getElementById('calendar-list');
    try {
      const data = await API.get('/api/events');
      const events = Array.isArray(data) ? data : (data.events || []);

      if (!events.length) {
        listEl.innerHTML = `<div class="panel-empty">No upcoming events. Your schedule is open, Boss.</div>`;
        return;
      }

      listEl.innerHTML = events.map(e => `
        <div class="panel-card event-card" data-id="${e.id}">
          <div class="card-header">
            <span class="card-title">${escapeHtml(e.title)}</span>
          </div>
          <div class="card-meta">
            <span>⏱️ ${formatDateTime(e.start_time)}</span>
            ${e.description ? `<p style="margin-top:0.35rem; color:var(--text-main);">${escapeHtml(e.description)}</p>` : ''}
          </div>
        </div>
      `).join('');
    } catch (err) {
      listEl.innerHTML = this._renderOfflineMessage('your schedule');
    }
  }

  async _addCalendarEvent() {
    const title = document.getElementById('cal-title').value.trim();
    const start_time = document.getElementById('cal-start').value;
    if (!title || !start_time) {
      showToast('Event title and start time required', 'error');
      return;
    }
    try {
      await API.post('/api/events', { title, start_time });
      showToast('Event added to schedule', 'success');
      this.renderCalendar();
    } catch (err) {
      showToast('Cannot add event: server not connected', 'error');
    }
  }

  // ---------------------------------------------------------------------------
  // System Settings Panel (Works 100% offline & online)
  // ---------------------------------------------------------------------------
  async renderSettings() {
    const currentToken = localStorage.getItem('jarvis_token') || '';
    const savedServerUrl = localStorage.getItem('jarvis_server_url') || '';
    const effectiveUrl = CONFIG.getServerUrl();
    const currentTTS = localStorage.getItem('jarvis_tts') !== 'false';
    const currentMode = (window.jarvisApp && window.jarvisApp.currentMode) || localStorage.getItem('jarvis_mode') || 'regular';

    this.titleEl.innerHTML = `<span>⚙️ System Settings</span>`;
    this.bodyEl.innerHTML = `
      <div class="settings-group">
        <label class="settings-label">Server URL (Portable Endpoint)</label>
        <div class="form-row">
          <input type="text" id="cfg-server-url" value="${escapeHtml(savedServerUrl)}" placeholder="Auto (${escapeHtml(effectiveUrl)})" class="hud-input">
          <button id="cfg-url-save" class="hud-btn hud-btn-primary">Save</button>
        </div>
        <div class="form-row" style="margin-top:0.5rem; align-items:center; justify-content:space-between;">
          <span class="settings-hint">Leave blank to auto-detect when hosted.</span>
          <button id="cfg-test-conn" class="hud-btn" style="background:rgba(0,229,255,0.12); color:var(--cyan-primary); border:1px solid rgba(0,229,255,0.3); padding:0.4rem 0.75rem;">Test Link</button>
        </div>
        <div id="cfg-conn-status" class="settings-hint" style="margin-top:0.35rem;"></div>
      </div>

      <div class="settings-group">
        <label class="settings-label">Access Token</label>
        <div class="form-row">
          <input type="password" id="cfg-token" value="${escapeHtml(currentToken)}" class="hud-input">
          <button id="cfg-token-save" class="hud-btn hud-btn-primary">Update</button>
        </div>
        <span class="settings-hint">Private security token for accessing your personal assistant.</span>
      </div>

      <div class="settings-group">
        <label class="settings-label">Voice Output (Text-to-Speech)</label>
        <label class="hud-switch">
          <input type="checkbox" id="cfg-tts" ${currentTTS ? 'checked' : ''}>
          <span class="slider"></span>
          <span class="switch-text">Enable Jarvis Voice Responses</span>
        </label>
      </div>

      <div class="settings-group">
        <label class="settings-label">Assistant Mode</label>
        <select id="cfg-mode" class="hud-input">
          <option value="regular" ${currentMode === 'regular' ? 'selected' : ''}>Regular (Friendly & Sarcastic)</option>
          <option value="productivity" ${currentMode === 'productivity' ? 'selected' : ''}>Productivity (Clean & Precise)</option>
        </select>
      </div>

      <div class="settings-group danger-zone">
        <label class="settings-label">Session Control</label>
        <button id="cfg-logout-btn" class="hud-btn hud-btn-danger">🔒 Lock & Logout</button>
      </div>
    `;

    // Save Server URL & reconnect WebSocket
    document.getElementById('cfg-url-save').addEventListener('click', () => {
      const url = document.getElementById('cfg-server-url').value.trim();
      CONFIG.setServerUrl(url);
      showToast(url ? `Server URL set to ${url}` : 'Server URL reset to auto-detect', 'success');
      if (window.jarvisApp) {
        window.jarvisApp.reconnectWebSocket();
      }
    });

    // Test server connection
    document.getElementById('cfg-test-conn').addEventListener('click', async () => {
      const statusEl = document.getElementById('cfg-conn-status');
      statusEl.innerHTML = `<span style="color:var(--amber-alert);">⏳ Pinging ${escapeHtml(CONFIG.getServerUrl())}...</span>`;
      try {
        const res = await API.checkHealth();
        statusEl.innerHTML = `<span style="color:var(--emerald-success);">✅ Connected! Status: ${escapeHtml(res.status || 'ok')}</span>`;
      } catch (err) {
        statusEl.innerHTML = `<span style="color:var(--crimson-danger);">❌ Cannot reach server (${escapeHtml(err.message)})</span>`;
      }
    });

    // Update token
    document.getElementById('cfg-token-save').addEventListener('click', () => {
      const val = document.getElementById('cfg-token').value.trim();
      if (val) {
        localStorage.setItem('jarvis_token', val);
        if (window.jarvisApp) {
          window.jarvisApp.token = val;
          window.jarvisApp.reconnectWebSocket();
        }
        showToast('Access token updated', 'success');
      }
    });

    // Toggle TTS
    document.getElementById('cfg-tts').addEventListener('change', (e) => {
      localStorage.setItem('jarvis_tts', e.target.checked);
      if (window.jarvisApp) window.jarvisApp.ttsEnabled = e.target.checked;
      showToast(`Voice output ${e.target.checked ? 'enabled' : 'muted'}`, 'info');
    });

    // Mode change
    document.getElementById('cfg-mode').addEventListener('change', (e) => {
      const mode = e.target.value;
      localStorage.setItem('jarvis_mode', mode);
      if (window.jarvisApp) window.jarvisApp.setMode(mode);
    });

    // Logout
    document.getElementById('cfg-logout-btn').addEventListener('click', () => {
      if (confirm('Lock session and log out?')) {
        localStorage.removeItem('jarvis_token');
        window.location.reload();
      }
    });
  }
}
