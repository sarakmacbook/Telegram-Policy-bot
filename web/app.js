'use strict';

const state = {
  token: sessionStorage.getItem('policyReviewToken') || '',
  demo: false,
  view: 'overview',
  status: 'pending',
  cases: [],
  activeCase: null,
  selectedId: null,
  stats: null,
  settings: null,
  connection: null,
  busy: false,
  searchTimer: null,
  currentObjectUrl: '',
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[char]));

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set('Authorization', `Bearer ${state.token}`);
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  const response = await fetch(path, { ...options, headers, cache: 'no-store' });
  let payload = {};
  try { payload = await response.json(); } catch (_) { /* non-JSON error response */ }
  if (!response.ok) {
    if (response.status === 401) signOut(false);
    throw new Error(payload.error || `Request failed (${response.status})`);
  }
  return payload;
}

function showLogin(message = '') {
  $('#app-shell').hidden = true;
  $('#login-view').hidden = false;
  if (message) $('#login-error').textContent = message;
}

function showApp() {
  $('#login-view').hidden = true;
  $('#app-shell').hidden = false;
  $('#demo-banner').hidden = !state.demo;
}

function signOut(showMessage = true) {
  sessionStorage.removeItem('policyReviewToken');
  state.token = '';
  state.activeCase = null;
  if (state.currentObjectUrl) URL.revokeObjectURL(state.currentObjectUrl);
  state.currentObjectUrl = '';
  showLogin(showMessage ? 'You have signed out.' : 'Your session expired. Sign in again.');
}

async function bootstrap() {
  $('#current-date').textContent = new Intl.DateTimeFormat(undefined, { weekday: 'short', month: 'short', day: 'numeric' }).format(new Date());
  try {
    const publicConfig = await fetch('/api/public-config', { cache: 'no-store' }).then((response) => response.json());
    state.demo = Boolean(publicConfig.demo);
    if (state.demo && publicConfig.demo_password) {
      $('#demo-hint').hidden = false;
      $('#demo-hint').innerHTML = `<strong>Local demo mode.</strong> Sample records are simulated; no Telegram messages are changed. Demo key: <code>${escapeHtml(publicConfig.demo_password)}</code>`;
      $('#admin-token').value = publicConfig.demo_password;
      if (!state.token) {
        state.token = publicConfig.demo_password;
        sessionStorage.setItem('policyReviewToken', state.token);
      }
    }
    if (state.token) {
      await enterWorkspace();
    } else {
      showLogin();
    }
  } catch (error) {
    showLogin('Could not connect to the review server. Refresh the page to try again.');
  }
}

async function enterWorkspace() {
  try {
    await api('/api/stats');
    showApp();
    await refreshWorkspace();
  } catch (error) {
    if (state.token) {
      state.token = '';
      sessionStorage.removeItem('policyReviewToken');
    }
    showLogin(error.message === 'Unauthorized' ? 'That admin key was not accepted.' : 'Could not load your workspace. Check the server and try again.');
  }
}

function toast(message, isError = false) {
  const node = document.createElement('div');
  node.className = `toast${isError ? ' is-error' : ''}`;
  node.textContent = message;
  $('#toast-region').append(node);
  window.setTimeout(() => node.remove(), 4200);
}

function shortTime(value) {
  if (!value) return '—';
  const then = new Date(value).getTime();
  if (!Number.isFinite(then)) return '—';
  const diff = Math.max(0, Date.now() - then);
  const min = Math.floor(diff / 60000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min}m ago`;
  const hours = Math.floor(min / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(new Date(value));
}

function fullTime(value) {
  if (!value) return 'Unknown time';
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) return 'Unknown time';
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(parsed);
}

function initials(name) {
  const words = String(name || '?').trim().split(/\s+/).filter(Boolean);
  return (words.length > 1 ? words[0][0] + words[words.length - 1][0] : (words[0] || '?').slice(0, 2)).toUpperCase();
}

function severityClass(value) {
  return String(value || 'medium').toLowerCase().replace(/[^a-z]/g, '');
}

function caseStatusLabel(item) {
  if (item.status === 'approved') return 'Approved';
  if (item.status === 'dismissed') return 'Dismissed';
  return item.removed ? 'Hidden · pending' : 'Open';
}

function renderCaseCard(item, selected = false, history = false) {
  const username = item.sender_username ? `@${item.sender_username}` : escapeHtml(item.sender_name || 'Unknown sender');
  const location = `${username} · ${escapeHtml(item.chat_title || item.chat_type || 'Telegram chat')}`;
  const preview = item.message_text || (item.media_type ? `Shared ${item.media_type} · no text caption` : 'No message text was archived');
  const title = escapeHtml(item.sender_name || 'Unknown sender');
  const statusChip = history ? `<span class="case-mini-status">${escapeHtml(caseStatusLabel(item))}</span>` : '';
  const demoBadge = item.is_demo ? '<span class="status-chip status-demo">DEMO</span>' : '';
  return `<button class="case-item${selected ? ' is-selected' : ''}" type="button" data-case-id="${Number(item.id)}" aria-label="Review case ${Number(item.id)} from ${title}">
    <span class="case-item-top">
      <span class="case-avatar tone-${Number(item.id) % 5}">${escapeHtml(initials(item.sender_name))}</span>
      <span class="case-sender"><strong>${title}</strong><small>${location}</small></span>
      <span class="case-time">${escapeHtml(shortTime(item.created_at))}</span>
    </span>
    <span class="case-item-preview">${escapeHtml(preview)}</span>
    <span class="case-item-footer"><span class="category-chip">${escapeHtml(item.category || 'Needs review')}</span><span class="severity-chip sev-${severityClass(item.severity)}">${escapeHtml(item.severity || 'medium')}</span>${item.removed ? '<span class="hidden-mini" title="Hidden in Telegram">◉</span>' : ''}${demoBadge}${statusChip}</span>
  </button>`;
}

function emptyList(title, description, icon = '✓') {
  return `<div class="list-empty"><span class="empty-mini">${icon}</span><strong>${escapeHtml(title)}</strong><p>${escapeHtml(description)}</p></div>`;
}

function filteredCases(filter) {
  if (!filter || filter === 'all') return state.cases;
  return state.cases.filter((item) => item.severity === filter);
}

function renderLists() {
  const priority = $('#priority-filter')?.value || 'all';
  const queuePriority = $('#queue-priority-filter')?.value || 'all';
  const overviewItems = filteredCases(priority).filter((item) => item.status === 'pending').slice(0, 5);
  const queueItems = filteredCases(queuePriority).filter((item) => item.status === 'pending');
  const historyItems = state.cases;

  $('#case-list').innerHTML = overviewItems.length
    ? overviewItems.map((item) => renderCaseCard(item, Number(item.id) === Number(state.selectedId))).join('')
    : emptyList(state.cases.length ? 'No cases at this priority' : 'All caught up', state.cases.length ? 'Try another priority or open the full queue.' : 'New potential policy concerns will appear here.', state.cases.length ? '⌕' : '✓');
  $('#queue-case-list').innerHTML = queueItems.length
    ? queueItems.map((item) => renderCaseCard(item, Number(item.id) === Number(state.selectedId))).join('')
    : emptyList(state.cases.length ? 'No matches' : 'Queue is clear', state.cases.length ? 'Adjust the severity filter or search terms.' : 'There are no open cases waiting for review.', '✓');
  $('#history-case-list').innerHTML = historyItems.length
    ? historyItems.map((item) => renderCaseCard(item, Number(item.id) === Number(state.selectedId), true)).join('')
    : emptyList('No cases yet', 'Flagged messages and moderator decisions will be retained here.', '↻');

  $('#list-count').textContent = String(state.cases.filter((item) => item.status === 'pending').length);
  $('#queue-list-count').textContent = String(queueItems.length);
  $('#history-list-count').textContent = String(historyItems.length);
  $('#section-pending-count').textContent = String(state.stats?.pending ?? 0);
  $('#queue-summary-count').textContent = `${queueItems.length} open ${queueItems.length === 1 ? 'case' : 'cases'}`;
  $('#nav-pending').textContent = String(state.stats?.pending ?? 0);
  bindCaseSelection();
}

function bindCaseSelection() {
  $$('[data-case-id]').forEach((button) => {
    button.addEventListener('click', () => selectCase(Number(button.dataset.caseId)));
  });
}

async function selectCase(id) {
  state.selectedId = id;
  renderLists();
  try {
    const item = await api(`/api/cases/${id}`);
    state.activeCase = item;
    renderAllDetails();
  } catch (error) {
    toast(error.message, true);
  }
}

function severityIcon(severity) {
  if (severity === 'critical' || severity === 'high') return '!';
  if (severity === 'low') return '✓';
  return 'i';
}

function renderDetail(item, history = false) {
  if (!item) return `<div class="detail-empty"><div class="empty-orbit"><span>⌑</span><i></i><b></b></div><h3>Select a case</h3><p>Choose a flagged message to see its context, evidence, and available actions.</p></div>`;
  const sev = severityClass(item.severity);
  const sender = item.sender_username ? `${escapeHtml(item.sender_name)} · @${escapeHtml(item.sender_username)}` : escapeHtml(item.sender_name || 'Unknown sender');
  const text = item.message_text ? escapeHtml(item.message_text) : '<span class="muted-message">No text or caption was archived.</span>';
  const flags = Array.isArray(item.signals) ? item.signals : [];
  const flagMarkup = flags.map((flag) => `<div class="detail-finding-secondary">• ${escapeHtml(flag.category || 'Review signal')} <span>· ${escapeHtml(flag.source || 'Local review')}</span></div>`).join('');
  const link = item.message_link ? `<a class="icon-button detail-open-link" href="${escapeHtml(item.message_link)}" target="_blank" rel="noopener noreferrer" title="Open original Telegram message" aria-label="Open original Telegram message">↗</a>` : '';
  const statusClass = item.status === 'approved' ? 'status-approved' : item.status === 'dismissed' ? 'status-dismissed' : '';
  const mediaLabel = item.media_type ? item.media_type[0].toUpperCase() + item.media_type.slice(1) : '';
  const hasImagePreview = item.has_preview || (item.is_demo && item.media_type === 'photo');
  const mediaNote = hasImagePreview ? '· blurred by default' : '· preview unavailable';
  const media = item.media_type ? `<div class="media-section"><div class="detail-section-label"><span class="section-mini-icon">▧</span> ${escapeHtml(mediaLabel)} attachment <span>${mediaNote}</span></div>${mediaFrame(item)}</div>` : '';
  let actionMarkup = '';
  const noteField = '<input class="review-note-input" data-review-note type="text" maxlength="1000" placeholder="Optional decision note for the audit trail…">';
  if (item.removed) {
    const confirmRemoval = item.status === 'pending' ? `<button class="button action-dismiss" data-action="dismiss" data-id="${Number(item.id)}">Confirm removal</button>` : '';
    actionMarkup = `<div class="detail-action-area">${noteField}<div class="action-buttons"><button class="button action-restore" data-action="restore" data-id="${Number(item.id)}">↗ &nbsp;Restore copy</button>${confirmRemoval}</div><p class="action-footnote">Restoring posts a new copy to the chat. Telegram cannot recreate the original message or sender metadata.</p>${item.status !== 'pending' && item.review_note ? `<div class="readonly-actions">Previous decision: ${escapeHtml(item.review_note)}</div>` : ''}</div>`;
  } else if (item.status === 'pending') {
    actionMarkup = `<div class="detail-action-area">${noteField}<div class="action-buttons"><button class="button action-approve" data-action="approve" data-id="${Number(item.id)}">✓ &nbsp;Approve &amp; keep visible</button><button class="button action-hide" data-action="hide" data-id="${Number(item.id)}">Hide &amp; dismiss</button><button class="button action-dismiss" data-action="dismiss" data-id="${Number(item.id)}" title="Dismiss this alert without removing the message">×</button></div><p class="action-footnote">Approving leaves the original visible. Hiding requires Telegram delete permissions.</p></div>`;
  } else {
    actionMarkup = `<div class="detail-action-area"><div class="readonly-actions">${item.review_note ? `Moderator note: ${escapeHtml(item.review_note)}` : 'This case has a recorded moderator decision.'}</div></div>`;
  }
  const note = item.operation_note && !item.is_demo ? `<div class="operation-note"><strong>Action note</strong>${escapeHtml(item.operation_note)}</div>` : '';
  const demoNote = item.is_demo ? '<div class="demo-detail-note">Simulated sample — all actions here update the demo record only.</div>' : '';
  const receivedTime = fullTime(item.created_at);
  const hiddenChip = item.removed ? '<span class="status-chip">Hidden in Telegram</span>' : '';
  const idText = item.message_id ? `Message #${escapeHtml(item.message_id)}` : `Case #${Number(item.id)}`;
  return `<div class="detail-content">
    <div class="detail-topline"><span class="detail-state-icon ${sev}">${severityIcon(sev)}</span><div class="detail-heading-copy"><h3>${escapeHtml(item.category || 'Needs review')}</h3><p>Case #${Number(item.id)} · ${escapeHtml(idText)} · ${escapeHtml(shortTime(item.created_at))}</p><div class="detail-badges"><span class="severity-chip sev-${sev}">${escapeHtml(item.severity || 'medium')} priority</span><span class="status-chip ${statusClass}">${escapeHtml(caseStatusLabel(item))}</span>${hiddenChip}${item.is_demo ? '<span class="status-chip status-demo">DEMO</span>' : ''}</div></div>${link}</div>
    <div class="detail-message-card"><div class="detail-section-label"><span class="section-mini-icon">▤</span> Message content</div><p class="message-quote">${text}</p><div class="message-meta"><span>${sender}</span><span>${escapeHtml(item.chat_title || item.chat_type || 'Telegram chat')}</span><span>${escapeHtml(receivedTime)}</span></div></div>
    <div class="signal-card signal-${sev}"><div class="signal-header"><span class="signal-mark">${severityIcon(sev)}</span><strong>Why it was flagged</strong><span class="signal-source">${escapeHtml(item.source || 'Local review')}</span></div><p>${escapeHtml(item.reason || 'A local review rule matched. Human review is required.')}</p>${flagMarkup}</div>
    ${media}
    <div class="case-context-row"><div class="context-cell"><span>Review scope</span><strong>${escapeHtml(state.settings?.jurisdictions || 'Cambodia')}</strong></div><div class="context-cell"><span>Detection</span><strong>${escapeHtml(item.source || 'Phrase check')}</strong></div></div>
    ${note}${demoNote}${actionMarkup}
  </div>`;
}

function mediaFrame(item) {
  if (item.has_preview) {
    return `<div class="media-frame" data-media-frame="${Number(item.id)}"><img class="sensitive-image" data-sensitive-image alt="Sensitive image preview, blurred until revealed"><div class="media-veil"><span class="media-lock">⌑</span><strong>Sensitive image hidden</strong><button type="button" data-reveal-media="${Number(item.id)}">Click to reveal</button></div></div>`;
  }
  if (item.is_demo && item.media_type === 'photo') {
    return `<div class="media-frame" data-media-frame="${Number(item.id)}"><div class="media-demo-art"></div><span class="media-demo-label">ILLUSTRATIVE DEMO PLACEHOLDER</span><div class="media-veil"><span class="media-lock">⌑</span><strong>Sensitive image hidden</strong><button type="button" data-reveal-media="${Number(item.id)}">Click to reveal</button></div></div>`;
  }
  return '<div class="media-unavailable">No in-console preview is available for this attachment.<br>Any archived file remains private to authorized reviewers.</div>';
}

function renderAllDetails() {
  $('#case-detail').innerHTML = renderDetail(state.activeCase, false);
  $('#queue-case-detail').innerHTML = renderDetail(state.activeCase, false);
  $('#history-case-detail').innerHTML = renderDetail(state.activeCase, true);
  bindDetailActions();
  loadPrivatePreview();
}

async function loadPrivatePreview() {
  if (state.currentObjectUrl) URL.revokeObjectURL(state.currentObjectUrl);
  state.currentObjectUrl = '';
  const item = state.activeCase;
  if (!item?.has_preview) return;
  try {
    const response = await fetch(`/api/media/${Number(item.id)}`, {
      headers: { Authorization: `Bearer ${state.token}` },
      cache: 'no-store',
    });
    if (!response.ok) throw new Error('Preview unavailable');
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    state.currentObjectUrl = objectUrl;
    $$(`[data-sensitive-image]`).forEach((image) => { image.src = objectUrl; });
  } catch (_) {
    // The card remains hidden; a missing preview is preferable to exposing a URL.
  }
}

function bindDetailActions() {
  $$('[data-action]').forEach((button) => {
    button.addEventListener('click', () => reviewCase(Number(button.dataset.id), button.dataset.action, button));
  });
  $$('[data-reveal-media]').forEach((button) => {
    button.addEventListener('click', () => {
      const frame = button.closest('[data-media-frame]');
      if (frame) frame.classList.add('is-revealed');
    });
  });
}

async function reviewCase(id, action, button) {
  const item = state.activeCase;
  if (!item || item.id !== id || state.busy) return;
  if (action === 'hide' && !item.is_demo && !window.confirm('Hide this message in Telegram? The saved case stays here and can only be restored by posting a new copy. If private notices are enabled and the sender has started the bot, the review reason may be sent to them.')) return;
  if (action === 'restore' && !item.is_demo && !window.confirm('Post a new copy to the Telegram chat? This cannot recreate the original message, sender, timestamp, or message ID.')) return;
  const detail = button.closest('.case-detail-panel');
  const note = $('[data-review-note]', detail)?.value.trim() || '';
  state.busy = true;
  button.disabled = true;
  try {
    const updated = await api(`/api/cases/${id}/review`, { method: 'POST', body: JSON.stringify({ action, note }) });
    state.activeCase = updated;
    const message = action === 'restore' ? 'A copy was restored to the chat.' : action === 'hide' ? 'Message hidden and case dismissed.' : action === 'approve' ? 'Case approved; original remains visible.' : 'Alert dismissed.';
    toast(message);
    await refreshWorkspace({ keepSelection: action === 'dismiss' || action === 'hide' });
  } catch (error) {
    toast(error.message, true);
  } finally {
    state.busy = false;
  }
}

function setConnectionUI(connection) {
  state.connection = connection;
  const configured = Boolean(connection?.bot_configured);
  const pill = $('#connection-indicator');
  if (configured) {
    pill.className = 'connection-pill is-connected';
    pill.innerHTML = '<span class="status-dot"></span><span>Bot connected</span>';
  } else {
    pill.className = 'connection-pill is-disconnected';
    pill.innerHTML = '<span class="status-dot"></span><span>Bot not connected</span>';
  }
  const badge = $('#bot-connection-badge');
  if (configured) {
    badge.textContent = connection.bot_username ? `@${connection.bot_username}` : 'Bot connected';
    badge.classList.add('is-connected');
  } else {
    badge.textContent = 'Not connected';
    badge.classList.remove('is-connected');
  }

  const summary = $('#telegram-connection-summary');
  summary.classList.toggle('is-connected', configured);
  if (configured) {
    const identity = connection.bot_username ? `Connected as @${connection.bot_username}.` : 'Bot token is configured.';
    const webhook = connection.webhook_secret_configured ? 'Webhook secret is ready.' : 'Connect below to generate a webhook secret.';
    summary.textContent = `${identity} ${webhook}${connection.webhook_url ? ` Last registered: ${connection.webhook_url}` : ''}`;
  } else {
    summary.textContent = 'No Telegram bot is connected yet. Add a bot token and a public HTTPS URL to set it up.';
  }

  const tokenField = $('#bot-token-field');
  const tokenInput = $('#telegram-bot-token');
  tokenField.hidden = Boolean(connection?.managed_by_environment);
  tokenInput.required = !configured && !connection?.managed_by_environment;
  tokenInput.placeholder = configured ? 'Leave blank to keep the current bot, or enter a replacement token' : 'Paste the token from @BotFather';
  if (!$('#telegram-public-url').value) $('#telegram-public-url').value = window.location.origin;
  const connectForm = $('#telegram-connect-form');
  const feedback = $('#telegram-connect-feedback');
  if (state.demo) {
    connectForm.hidden = false;
    $$('input, button', connectForm).forEach((control) => { control.disabled = true; });
    feedback.classList.remove('is-error');
    feedback.textContent = 'Telegram connection is disabled in demo mode; no Telegram requests are made.';
  } else if (window.location.protocol !== 'https:') {
    connectForm.hidden = true;
    feedback.classList.add('is-error');
    feedback.textContent = 'Open this console over HTTPS before entering a Telegram bot token.';
  } else {
    connectForm.hidden = false;
  }
}

function renderStats(stats) {
  state.stats = stats;
  $('#stat-pending').textContent = String(stats.pending);
  $('#stat-hidden').textContent = String(stats.hidden);
  $('#stat-reviewed').textContent = String(stats.reviewed_today);
  $('#stat-jurisdictions').textContent = String(stats.jurisdiction_count);
  $('#urgent-chip').textContent = `${stats.urgent} urgent`;
  $('#nav-pending').textContent = String(stats.pending);
  $('#section-pending-count').textContent = String(stats.pending);
  $('#auto-hide-foot').innerHTML = `<span class="scope-indicator" style="background:${stats.auto_hide ? '#59a17e' : '#c3a778'}"></span><span>Auto-hide is ${stats.auto_hide ? 'on' : 'off'}</span>`;
  const alert = $('#auto-hide-alert');
  if (stats.auto_hide) {
    alert.classList.add('is-auto-hide');
    alert.innerHTML = '<span class="status-banner-icon">!</span><div><strong>Auto-hide is enabled</strong><span>Phrase matches may be deleted before review. False positives are possible; check each saved case.</span></div><button data-go-settings class="text-link" type="button">Review settings <span>→</span></button>';
  } else {
    alert.classList.remove('is-auto-hide');
    alert.innerHTML = '<span class="status-banner-icon">i</span><div><strong>Review-first mode is on</strong><span>Flagged messages stay in the chat until a moderator decides what to do.</span></div><button data-go-settings class="text-link" type="button">Configure <span>→</span></button>';
  }
  $$('[data-go-settings]').forEach((button) => button.addEventListener('click', () => navigate('settings')));
}

async function refreshWorkspace(options = {}) {
  try {
    const [stats, settings, connection] = await Promise.all([
      api('/api/stats'), api('/api/settings'), api('/api/connection')
    ]);
    renderStats(stats);
    state.settings = settings;
    setConnectionUI(connection);
    await loadCases(options);
    if (state.view === 'settings') populateSettings();
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadCases(options = {}) {
  const keepSelection = options.keepSelection !== false;
  let status = state.view === 'history' ? ($('#history-status-filter')?.value || 'all') : 'pending';
  let query = '';
  if (state.view === 'queue') query = $('#queue-search')?.value || '';
  if (state.view === 'history') query = $('#history-search')?.value || '';
  const params = new URLSearchParams({ status });
  if (query.trim()) params.set('q', query.trim());
  const response = await api(`/api/cases?${params.toString()}`);
  state.cases = response.cases || [];
  const selectionAvailable = keepSelection && state.cases.some((item) => Number(item.id) === Number(state.selectedId));
  if (!selectionAvailable) state.selectedId = state.cases.length ? Number(state.cases[0].id) : null;
  if (state.selectedId) {
    const selected = state.cases.find((item) => Number(item.id) === Number(state.selectedId));
    if (selected) {
      state.activeCase = await api(`/api/cases/${state.selectedId}`);
    } else {
      state.activeCase = null;
    }
  } else {
    state.activeCase = null;
  }
  renderLists();
  renderAllDetails();
}

function navigate(view) {
  state.view = view;
  const titles = { overview: 'Overview', queue: 'Review queue', history: 'Case history', settings: 'Settings' };
  $('#topbar-title').textContent = titles[view] || 'Overview';
  $$('.nav-item').forEach((button) => button.classList.toggle('is-active', button.dataset.view === view));
  $$('.view-section').forEach((section) => { section.hidden = section.id !== `${view}-view`; });
  if (view === 'settings') populateSettings();
  else loadCases({ keepSelection: view !== 'history' });
  closeMobileNav();
}

function populateSettings() {
  if (!state.settings) return;
  $('#setting-auto-hide').checked = Boolean(state.settings.auto_hide);
  $('#setting-auto-hide-media').checked = Boolean(state.settings.auto_hide_media);
  $('#setting-review-media').checked = Boolean(state.settings.review_all_media);
  $('#setting-notify').checked = Boolean(state.settings.notify_chat);
  $('#setting-dm-remove').checked = Boolean(state.settings.dm_on_remove);
  $('#jurisdiction-input').value = state.settings.jurisdictions || 'Cambodia';
  renderCustomRules();
  $('#settings-save-status').textContent = 'Changes save to this server.';
}

async function connectTelegramBot(event) {
  event.preventDefault();
  const tokenInput = $('#telegram-bot-token');
  const button = $('#connect-bot-button');
  const feedback = $('#telegram-connect-feedback');
  const botToken = tokenInput.value.trim();
  const publicUrl = $('#telegram-public-url').value.trim();
  button.disabled = true;
  button.textContent = 'Connecting…';
  feedback.classList.remove('is-error');
  feedback.textContent = 'Verifying the bot with Telegram and registering the webhook…';
  try {
    const connection = await api('/api/telegram/connect', {
      method: 'POST',
      body: JSON.stringify({ bot_token: botToken, public_url: publicUrl }),
    });
    setConnectionUI(connection);
    feedback.classList.remove('is-error');
    const successMessage = connection.bot_username
      ? `Connected to @${connection.bot_username}; the webhook is registered.`
      : 'Telegram bot connected and webhook registered.';
    feedback.textContent = connection.warning ? `${successMessage} ${connection.warning}` : successMessage;
    toast('Telegram bot connected.');
    await refreshWorkspace();
  } catch (error) {
    feedback.classList.add('is-error');
    feedback.textContent = error.message;
  } finally {
    tokenInput.value = '';
    button.disabled = false;
    button.innerHTML = state.connection?.bot_configured
      ? 'Register / update webhook <span aria-hidden="true">→</span>'
      : 'Verify bot &amp; connect <span aria-hidden="true">→</span>';
  }
}

function renderCustomRules() {
  const rules = state.settings?.custom_rules || [];
  $('#custom-rule-count').textContent = String(rules.length);
  if (!rules.length) {
    $('#custom-rules-list').innerHTML = '<div class="empty-rules">No custom checks yet.</div>';
    return;
  }
  $('#custom-rules-list').innerHTML = rules.map((rule, index) => `<div class="custom-rule-item"><span class="custom-rule-indicator"></span><strong>${escapeHtml(rule.name)}</strong><small>${escapeHtml((rule.terms || []).join(', '))}</small><button class="remove-rule" type="button" data-remove-rule="${index}" aria-label="Remove ${escapeHtml(rule.name)}">×</button></div>`).join('');
  $$('[data-remove-rule]').forEach((button) => button.addEventListener('click', () => {
    const index = Number(button.dataset.removeRule);
    state.settings.custom_rules.splice(index, 1);
    renderCustomRules();
    $('#settings-save-status').textContent = 'Unsaved changes';
  }));
}

async function saveSettings() {
  const settings = {
    auto_hide: $('#setting-auto-hide').checked,
    auto_hide_media: $('#setting-auto-hide-media').checked,
    review_all_media: $('#setting-review-media').checked,
    notify_chat: $('#setting-notify').checked,
    dm_on_remove: $('#setting-dm-remove').checked,
    jurisdictions: $('#jurisdiction-input').value.trim() || 'Cambodia',
    custom_rules: state.settings?.custom_rules || [],
  };
  $('#settings-save-status').textContent = 'Saving…';
  $('#save-settings').disabled = true;
  try {
    state.settings = await api('/api/settings', { method: 'POST', body: JSON.stringify(settings) });
    $('#settings-save-status').textContent = 'Saved just now';
    toast('Settings saved to this server.');
    const stats = await api('/api/stats');
    renderStats(stats);
    await loadCases();
  } catch (error) {
    $('#settings-save-status').textContent = 'Could not save';
    toast(error.message, true);
  } finally {
    $('#save-settings').disabled = false;
  }
}

function openModal(id) {
  const modal = $(id);
  if (modal) {
    modal.hidden = false;
    const focusTarget = $('textarea, input:not([type="checkbox"]), button', modal);
    window.setTimeout(() => focusTarget?.focus(), 0);
  }
}
function closeModals() { $$('.modal-backdrop').forEach((modal) => { modal.hidden = true; }); }

async function runScanTest() {
  const text = $('#test-message').value.trim();
  const hasMedia = $('#test-has-media').checked;
  const resultNode = $('#test-result');
  resultNode.hidden = false;
  resultNode.className = 'test-result';
  resultNode.innerHTML = '<div class="test-result-head"><span>…</span> Checking local rules</div>';
  const button = $('#run-scan-test');
  button.disabled = true;
  try {
    const result = await api('/api/scan-test', { method: 'POST', body: JSON.stringify({
      text,
      has_media: hasMedia,
      review_all_media: $('#setting-review-media')?.checked ?? Boolean(state.settings?.review_all_media),
      custom_rules: state.settings?.custom_rules || [],
    }) });
    if (result.flagged) {
      resultNode.classList.add('is-flagged');
      const list = result.findings.map((finding) => `<li><strong>${escapeHtml(finding.category)}</strong> · ${escapeHtml(finding.severity)} — ${escapeHtml(finding.reason)}</li>`).join('');
      resultNode.innerHTML = `<div class="test-result-head"><span>!</span> ${result.findings.length} review ${result.findings.length === 1 ? 'signal' : 'signals'} found</div><ul>${list}</ul><p>This is a phrase match, not a policy or legal verdict.</p>`;
    } else {
      resultNode.classList.add('is-clear');
      resultNode.innerHTML = '<div class="test-result-head"><span>✓</span> No configured checks matched</div><p>This does not mean the message is safe, permitted, or lawful.</p>';
    }
  } catch (error) {
    resultNode.classList.add('is-flagged');
    resultNode.innerHTML = `<div class="test-result-head"><span>!</span> ${escapeHtml(error.message)}</div>`;
  } finally {
    button.disabled = false;
  }
}

function openTestModal() {
  $('#test-result').hidden = true;
  $('#test-message').value = '';
  $('#test-has-media').checked = false;
  openModal('#test-modal');
}

function closeMobileNav() {
  $('#sidebar').classList.remove('is-open');
  $('#mobile-backdrop').hidden = true;
}

function bindEvents() {
  $('#login-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const token = $('#admin-token').value.trim();
    if (!token) return;
    state.token = token;
    sessionStorage.setItem('policyReviewToken', token);
    $('#login-error').textContent = '';
    await enterWorkspace();
  });
  $('#toggle-token').addEventListener('click', () => {
    const input = $('#admin-token');
    input.type = input.type === 'password' ? 'text' : 'password';
    $('#toggle-token').setAttribute('aria-label', input.type === 'password' ? 'Show admin key' : 'Hide admin key');
  });
  $('#logout-button').addEventListener('click', () => signOut(true));
  $('#topbar-avatar').addEventListener('click', () => signOut(true));
  $$('.nav-item').forEach((button) => button.addEventListener('click', () => navigate(button.dataset.view)));
  $('#see-all-queue').addEventListener('click', () => navigate('queue'));
  $('#priority-filter').addEventListener('change', renderLists);
  $('#queue-priority-filter').addEventListener('change', renderLists);
  $('#queue-search').addEventListener('input', () => {
    window.clearTimeout(state.searchTimer);
    state.searchTimer = window.setTimeout(() => loadCases(), 220);
  });
  $('#history-search').addEventListener('input', () => {
    window.clearTimeout(state.searchTimer);
    state.searchTimer = window.setTimeout(() => loadCases(), 220);
  });
  $('#history-status-filter').addEventListener('change', () => loadCases({ keepSelection: false }));
  ['#open-test-scan', '#queue-test-scan', '#settings-test-scan'].forEach((selector) => $(selector).addEventListener('click', openTestModal));
  $('#run-scan-test').addEventListener('click', runScanTest);
  $$('[data-close-modal]').forEach((button) => button.addEventListener('click', closeModals));
  $$('.modal-backdrop').forEach((modal) => modal.addEventListener('click', (event) => { if (event.target === modal) modal.hidden = true; }));
  $('#privacy-learn').addEventListener('click', () => openModal('#privacy-modal'));
  $('#topbar-help').addEventListener('click', () => openModal('#privacy-modal'));
  $('#save-settings').addEventListener('click', saveSettings);
  $('#telegram-connect-form').addEventListener('submit', connectTelegramBot);
  $('#jurisdiction-input').addEventListener('input', () => { $('#settings-save-status').textContent = 'Unsaved changes'; });
  ['#setting-auto-hide', '#setting-auto-hide-media', '#setting-review-media', '#setting-notify', '#setting-dm-remove'].forEach((selector) => $(selector).addEventListener('change', () => { $('#settings-save-status').textContent = 'Unsaved changes'; }));
  $('#add-rule-form').addEventListener('submit', (event) => {
    event.preventDefault();
    const name = $('#rule-name').value.trim();
    const terms = $('#rule-terms').value.split(',').map((term) => term.trim()).filter(Boolean);
    if (!name || !terms.length) return;
    if ((state.settings?.custom_rules || []).length >= 30) {
      toast('You can add up to 30 custom checks.', true);
      return;
    }
    state.settings.custom_rules = state.settings.custom_rules || [];
    state.settings.custom_rules.push({ name: name.slice(0, 48), terms: [...new Set(terms)].slice(0, 30) });
    $('#rule-name').value = '';
    $('#rule-terms').value = '';
    renderCustomRules();
    $('#settings-save-status').textContent = 'Unsaved changes';
  });
  $('#copy-webhook-path').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(`${window.location.origin}/telegram/webhook`);
      toast('Webhook URL copied.');
    } catch (_) {
      toast('Webhook path: /telegram/webhook');
    }
  });
  $('#mobile-menu').addEventListener('click', () => {
    $('#sidebar').classList.add('is-open');
    $('#mobile-backdrop').hidden = false;
  });
  $('#mobile-backdrop').addEventListener('click', closeMobileNav);
  $('#demo-banner-close').addEventListener('click', () => { $('#demo-banner').hidden = true; });
  document.addEventListener('keydown', (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      if (state.view === 'queue') $('#queue-search').focus();
      else if (state.view === 'history') $('#history-search').focus();
      else navigate('queue');
    }
    if (event.key === 'Escape') {
      closeModals();
      closeMobileNav();
    }
  });
}

bindEvents();
bootstrap();
