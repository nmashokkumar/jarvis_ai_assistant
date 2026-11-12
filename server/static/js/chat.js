/**
 * JARVIS — chat.js
 * Message rendering, markdown, streaming, conversation list management
 */

// ---------------------------------------------------------------------------
// MessageRenderer
// ---------------------------------------------------------------------------
class MessageRenderer {
  constructor(chatEl) {
    this.chatEl = chatEl;
    this._streamingBubble = null;
    this._streamingContent = '';
    this._streamingRaw = '';
  }

  /** Simple markdown → HTML (no external deps) */
  renderMarkdown(text) {
    if (!text) return '';
    let html = escapeHtml(text);

    // Fenced code blocks
    html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
      const langLabel = lang ? `<span class="code-lang">${escapeHtml(lang)}</span>` : '';
      return `<div class="code-block">${langLabel}<pre><code>${code}</code></pre><button class="copy-code-btn" onclick="copyToClipboard(this.previousElementSibling.textContent)">Copy</button></div>`;
    });

    // Inline code
    html = html.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');

    // Bold
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/__(.+?)__/g, '<strong>$1</strong>');

    // Italic
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
    html = html.replace(/_(.+?)_/g, '<em>$1</em>');

    // Strikethrough
    html = html.replace(/~~(.+?)~~/g, '<del>$1</del>');

    // Headers
    html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^## (.+)$/gm,  '<h2>$1</h2>');
    html = html.replace(/^# (.+)$/gm,   '<h1>$1</h1>');

    // Blockquotes
    html = html.replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>');

    // Unordered lists
    html = html.replace(/^(\s*[-*+] .+(\n|$))+/gm, match => {
      const items = match.trim().split('\n').map(line =>
        `<li>${line.replace(/^\s*[-*+] /, '')}</li>`
      ).join('');
      return `<ul>${items}</ul>`;
    });

    // Ordered lists
    html = html.replace(/^(\s*\d+\. .+(\n|$))+/gm, match => {
      const items = match.trim().split('\n').map(line =>
        `<li>${line.replace(/^\s*\d+\. /, '')}</li>`
      ).join('');
      return `<ol>${items}</ol>`;
    });

    // Links
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>');

    // Horizontal rule
    html = html.replace(/^---+$/gm, '<hr>');

    // Paragraphs: double newlines
    html = html.replace(/\n\n+/g, '</p><p>');

    // Single newlines
    html = html.replace(/\n/g, '<br>');

    return `<p>${html}</p>`;
  }

  /** Render a complete message bubble */
  renderMessage(role, content, toolUsed = null, animate = true) {
    const wrap = document.createElement('div');
    wrap.className = `msg msg-${role}${animate ? ' msg-enter' : ''}`;
    wrap.dataset.role = role;

    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';

    if (role === 'assistant') {
      bubble.innerHTML = this.renderMarkdown(content);
    } else {
      bubble.innerHTML = `<p>${escapeHtml(content)}</p>`;
    }

    if (toolUsed) {
      const badge = document.createElement('span');
      badge.className = 'tool-badge';
      badge.textContent = `🔧 ${toolUsed}`;
      bubble.appendChild(badge);
    }

    // Copy button for assistant messages
    if (role === 'assistant') {
      const actions = document.createElement('div');
      actions.className = 'msg-actions';
      const copyBtn = document.createElement('button');
      copyBtn.className = 'msg-action-btn';
      copyBtn.title = 'Copy';
      copyBtn.textContent = '⎘ Copy';
      copyBtn.addEventListener('click', () => copyToClipboard(content));
      actions.appendChild(copyBtn);
      wrap.appendChild(actions);
    }

    // Timestamp
    const ts = document.createElement('span');
    ts.className = 'msg-time';
    ts.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    wrap.appendChild(bubble);
    wrap.appendChild(ts);
    this.chatEl.appendChild(wrap);
    this._scrollToBottom();
    return wrap;
  }

  /** Create a streaming (assistant) bubble — returns the bubble element */
  startStreaming() {
    this._streamingRaw = '';
    this._streamingContent = '';
    const wrap = document.createElement('div');
    wrap.className = 'msg msg-assistant msg-streaming msg-enter';
    wrap.dataset.role = 'assistant';

    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    bubble.innerHTML = '<span class="cursor-blink">▌</span>';

    const ts = document.createElement('span');
    ts.className = 'msg-time';
    ts.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    wrap.appendChild(bubble);
    wrap.appendChild(ts);
    this.chatEl.appendChild(wrap);
    this._streamingBubble = { wrap, bubble };
    this._scrollToBottom();
    return wrap;
  }

  appendToStreaming(token) {
    if (!this._streamingBubble) this.startStreaming();
    this._streamingRaw += token;
    // Show raw text with cursor while streaming (no markdown parse mid-stream)
    const escaped = escapeHtml(this._streamingRaw);
    this._streamingBubble.bubble.innerHTML =
      `<p>${escaped.replace(/\n/g, '<br>')}</p><span class="cursor-blink">▌</span>`;
    this._scrollToBottom();
  }

  finalizeStreaming(toolUsed = null) {
    if (!this._streamingBubble) return '';
    const { wrap, bubble } = this._streamingBubble;
    // Re-render with full markdown
    bubble.innerHTML = this.renderMarkdown(this._streamingRaw);

    if (toolUsed) {
      const badge = document.createElement('span');
      badge.className = 'tool-badge';
      badge.textContent = `🔧 ${toolUsed}`;
      bubble.appendChild(badge);
    }

    // Add copy action
    const actions = document.createElement('div');
    actions.className = 'msg-actions';
    const rawContent = this._streamingRaw;
    const copyBtn = document.createElement('button');
    copyBtn.className = 'msg-action-btn';
    copyBtn.title = 'Copy';
    copyBtn.textContent = '⎘ Copy';
    copyBtn.addEventListener('click', () => copyToClipboard(rawContent));
    actions.appendChild(copyBtn);
    wrap.appendChild(actions);

    wrap.classList.remove('msg-streaming');
    this._streamingBubble = null;
    const result = this._streamingRaw;
    this._streamingRaw = '';
    this._scrollToBottom();
    return result;
  }

  _scrollToBottom(smooth = true) {
    this.chatEl.scrollTo({
      top: this.chatEl.scrollHeight,
      behavior: smooth ? 'smooth' : 'instant',
    });
  }

  clear() {
    this.chatEl.innerHTML = '';
    this._streamingBubble = null;
    this._streamingRaw = '';
  }
}

// ---------------------------------------------------------------------------
// ConversationManager
// ---------------------------------------------------------------------------
class ConversationManager {
  constructor(listEl, onSelect, onDelete) {
    this.listEl = listEl;
    this.onSelect = onSelect;
    this.onDelete = onDelete;
    this.conversations = [];
    this.activeId = null;
  }

  async load() {
    try {
      const data = await API.get('/api/conversations');
      this.conversations = Array.isArray(data) ? data : (data.conversations || []);
      this.render();
    } catch (err) {
      console.error('[ConvManager] Failed to load:', err);
    }
  }

  render() {
    this.listEl.innerHTML = '';
    if (!this.conversations.length) {
      this.listEl.innerHTML = '<div class="conv-empty">No conversations yet</div>';
      return;
    }
    // Newest first
    const sorted = [...this.conversations].sort((a, b) =>
      new Date(b.updated_at || b.created_at || 0) - new Date(a.updated_at || a.created_at || 0)
    );
    sorted.forEach(conv => this._renderItem(conv));
  }

  _renderItem(conv) {
    const item = document.createElement('div');
    item.className = `conv-item${conv.id === this.activeId ? ' active' : ''}`;
    item.dataset.id = conv.id;

    const title = conv.title || conv.first_message || 'New Chat';
    const shortTitle = title.length > 40 ? title.slice(0, 37) + '…' : title;
    const ago = timeAgo(conv.updated_at || conv.created_at);

    item.innerHTML = `
      <div class="conv-icon">💬</div>
      <div class="conv-info">
        <div class="conv-title">${escapeHtml(shortTitle)}</div>
        <div class="conv-time">${ago}</div>
      </div>
      <button class="conv-delete-btn" title="Delete" data-id="${conv.id}">🗑</button>
    `;

    item.addEventListener('click', (e) => {
      if (e.target.closest('.conv-delete-btn')) return;
      this.setActive(conv.id);
      this.onSelect(conv.id);
    });

    item.querySelector('.conv-delete-btn').addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!confirm('Delete this conversation?')) return;
      try {
        await API.delete(`/api/conversations/${conv.id}`);
        this.conversations = this.conversations.filter(c => c.id !== conv.id);
        this.render();
        this.onDelete(conv.id);
      } catch (err) {
        showToast('Failed to delete conversation', 'error');
      }
    });

    this.listEl.appendChild(item);
  }

  setActive(id) {
    this.activeId = id;
    this.listEl.querySelectorAll('.conv-item').forEach(el => {
      el.classList.toggle('active', el.dataset.id === id);
    });
  }

  prepend(conv) {
    // Remove if already exists, then prepend
    this.conversations = this.conversations.filter(c => c.id !== conv.id);
    this.conversations.unshift(conv);
    this.render();
    this.setActive(conv.id);
  }

  updateTitle(id, title) {
    const conv = this.conversations.find(c => c.id === id);
    if (conv) {
      conv.title = title;
      this.render();
    }
  }
}
