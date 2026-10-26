/* ==========================================================================
   Music Bot 管理后台 · 公共脚本
   - 图标：内联 SVG，避免外链 CDN（离线部署也能用）
   - api()：统一处理 401 / 非 JSON / success:false
   - 时间：SQLite 的 CURRENT_TIMESTAMP 是 UTC，这里统一转本地时区
   ========================================================================== */

/* ---------------- 图标 ---------------- */
const ICON_PATHS = {
  dashboard: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z"/>',
  playlist: '<path d="M3 6h11M3 12h11M3 18h7"/><circle cx="18" cy="16" r="3"/><path d="M21 16V7l-3 1"/>',
  history: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/><path d="M12 8v4l3 2"/>',
  logs: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3M13 15h4"/>',
  account: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-6 8-6s8 2 8 6"/>',
  music: '<circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/><path d="M9 18V6l12-2v12"/>',
  download: '<path d="M12 3v12"/><path d="M7 11l5 5 5-5"/><path d="M4 20h16"/>',
  upload: '<path d="M12 16V4"/><path d="M7 8l5-5 5 5"/><path d="M4 20h16"/>',
  queue: '<path d="M4 6h16M4 12h16M4 18h10"/><circle cx="19" cy="18" r="2"/>',
  send: '<path d="M21 3L3 10.5l7 2.5 2.5 7L21 3z"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 2.5 15.4 0 18M12 3c-2.5 2.6-2.5 15.4 0 18"/>',
  server: '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>',
  check: '<path d="M4 12.5l5 5L20 6.5"/>',
  checkCircle: '<circle cx="12" cy="12" r="9"/><path d="M8.5 12.5l2.5 2.5 4.5-5"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  xCircle: '<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>',
  alert: '<path d="M12 8v5M12 17h.01"/><path d="M10.3 3.9L2.7 17a2 2 0 0 0 1.7 3h15.2a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 3v6h-6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
  play: '<path d="M7 4l13 8-13 8z"/>',
  pause: '<rect x="7" y="5" width="3.5" height="14" rx="1"/><rect x="13.5" y="5" width="3.5" height="14" rx="1"/>',
  external: '<path d="M14 4h6v6"/><path d="M20 4L10 14"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h9"/>',
  qr: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3h-3zM20 14h1M14 20h3M20 20h1"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 4l3 3M14 7l3 3"/>',
  shield: '<path d="M12 3l8 3v6c0 5-3.4 8-8 9-4.6-1-8-4-8-9V6l8-3z"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5z"/><path d="M14 3v5h5"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5M21 12H9"/>',
  chevronDown: '<path d="M6 9l6 6 6-6"/>',
  chevronRight: '<path d="M9 6l6 6-6 6"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  sparkles: '<path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z"/><path d="M18 15l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z"/>',
  save: '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8M7 3v5h8"/>',
  link: '<path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/>',
  wifi: '<path d="M2.5 8.5a15 15 0 0 1 19 0M5.5 12a11 11 0 0 1 13 0M8.5 15.5a7 7 0 0 1 7 0"/><path d="M12 19h.01"/>',
  logoutSmall: '<path d="M15 12H3M6 9l-3 3 3 3"/>',
};

function icon(name, size = 18, extraClass = '') {
  const path = ICON_PATHS[name] || ICON_PATHS.info;
  return `<svg class="${extraClass}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`;
}

/* ---------------- 请求封装 ---------------- */
async function api(path, options = {}) {
  const { method = 'GET', body, headers = {} } = options;
  const init = { method, credentials: 'same-origin', headers: { ...headers } };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }

  const res = await fetch(path, init);
  if (res.status === 401) {
    window.location.href = '/login';
    throw new Error('登录已失效');
  }

  let json;
  const text = await res.text();
  try {
    json = text ? JSON.parse(text) : {};
  } catch (e) {
    throw new Error(`响应不是 JSON (HTTP ${res.status})`);
  }

  if (!res.ok || json.success === false) {
    throw new Error(json.error || json.message || `请求失败 (HTTP ${res.status})`);
  }
  return json.data !== undefined ? json.data : json;
}

/* ---------------- Toast ---------------- */
function toast(message, type = 'info') {
  let stack = document.querySelector('.toast-stack');
  if (!stack) {
    stack = document.createElement('div');
    stack.className = 'toast-stack';
    document.body.appendChild(stack);
  }
  const el = document.createElement('div');
  el.className = `toast ${type === 'ok' ? 'ok' : type === 'err' ? 'err' : ''}`;
  const glyph = type === 'ok' ? icon('checkCircle', 15) : type === 'err' ? icon('xCircle', 15) : icon('info', 15);
  el.innerHTML = `${glyph}<span>${escapeHtml(message)}</span>`;
  stack.appendChild(el);
  setTimeout(() => {
    el.style.transition = 'opacity .2s, transform .2s';
    el.style.opacity = '0';
    el.style.transform = 'translateY(4px)';
    setTimeout(() => el.remove(), 220);
  }, type === 'err' ? 4200 : 2600);
}

/* ---------------- 确认对话框 ---------------- */
function confirmDialog({ title, desc = '', okText = '确认', cancelText = '取消', danger = false }) {
  return new Promise((resolve) => {
    const mask = document.createElement('div');
    mask.className = 'modal-mask';
    mask.innerHTML = `
      <div class="modal" style="max-width:400px">
        <div class="modal-head"><div class="modal-title">${escapeHtml(title)}</div>
          ${desc ? `<div class="modal-desc">${escapeHtml(desc)}</div>` : ''}</div>
        <div class="modal-foot">
          <button class="btn" data-act="cancel">${escapeHtml(cancelText)}</button>
          <button class="btn ${danger ? 'btn-danger' : 'btn-primary'}" data-act="ok">${escapeHtml(okText)}</button>
        </div>
      </div>`;
    document.body.appendChild(mask);
    requestAnimationFrame(() => mask.classList.add('show'));

    const close = (value) => {
      mask.classList.remove('show');
      setTimeout(() => mask.remove(), 160);
      resolve(value);
    };
    mask.addEventListener('click', (e) => {
      if (e.target === mask) return close(false);
      const act = e.target.closest('[data-act]')?.dataset.act;
      if (act === 'ok') close(true);
      if (act === 'cancel') close(false);
    });
    document.addEventListener('keydown', function onKey(e) {
      if (e.key === 'Escape') { document.removeEventListener('keydown', onKey); close(false); }
    });
  });
}

/* ---------------- 通用弹窗 ----------------
 * 页面里自己拼 modal 时统一走这里，省得每个弹窗各写一遍开关逻辑，
 * 也让 Esc 关闭、点遮罩关闭、锁滚动、Enter 提交这些细节保持一致。
 * options: { onAction(act, event), onClose(result), submitOnEnter }
 */
function openModal(html, options = {}) {
  const mask = document.createElement('div');
  mask.className = 'modal-mask';
  mask.innerHTML = html;
  document.body.appendChild(mask);

  const prevOverflow = document.body.style.overflow;
  document.body.style.overflow = 'hidden';
  requestAnimationFrame(() => mask.classList.add('show'));

  let closed = false;
  const close = (result) => {
    if (closed) return;
    closed = true;
    mask.classList.remove('show');
    document.body.style.overflow = prevOverflow;
    document.removeEventListener('keydown', onKey, true);
    setTimeout(() => mask.remove(), 160);
    if (options.onClose) options.onClose(result);
  };

  function onKey(e) {
    if (e.key === 'Escape') {
      e.stopPropagation();
      close();
      return;
    }
    if (e.key === 'Enter' && options.submitOnEnter && !e.isComposing) {
      const target = e.target;
      if (target && target.tagName === 'TEXTAREA') return;
      const btn = mask.querySelector('[data-act="submit"], [data-act="ok"]');
      if (btn && !btn.disabled) { e.preventDefault(); btn.click(); }
    }
  }
  document.addEventListener('keydown', onKey, true);

  mask.addEventListener('click', (e) => {
    const act = e.target.closest('[data-act]')?.dataset.act;
    if (e.target === mask || act === 'close') { close(); return; }
    if (options.onAction) options.onAction(act, e);
  });

  // 弹窗里第一个可输入的控件自动聚焦
  const firstInput = mask.querySelector('input:not([type=hidden]), textarea, select');
  if (firstInput) setTimeout(() => firstInput.focus(), 60);

  return { mask, close };
}

/* ---------------- 格式化 ---------------- */
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function formatBytes(bytes, decimals = 1) {
  const n = Number(bytes) || 0;
  if (n <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  const value = n / Math.pow(1024, i);
  return `${value.toFixed(i === 0 ? 0 : decimals)} ${units[i]}`;
}

/**
 * SQLite 的 DEFAULT CURRENT_TIMESTAMP 写的是 UTC，而 last_check_time 之类
 * 是应用层用本地时间写的。这里按"无时区后缀 → 视为 UTC"处理，
 * 否则时间会整体偏移 8 小时（旧界面就是这个表现）。
 */
function parseDbTime(input) {
  if (!input) return null;
  if (input instanceof Date) return input;
  let text = String(input).trim();
  if (/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$/.test(text)) text += 'Z';
  const date = new Date(text.replace(' ', 'T'));
  return isNaN(date.getTime()) ? null : date;
}

function fmtTime(input, withSeconds = false) {
  const date = parseDbTime(input);
  if (!date) return '—';
  const pad = (n) => String(n).padStart(2, '0');
  const body = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
    + `${pad(date.getHours())}:${pad(date.getMinutes())}`
    + (withSeconds ? `:${pad(date.getSeconds())}` : '');
  return body;
}

function fmtRelative(input) {
  const date = parseDbTime(input);
  if (!date) return '—';
  const diff = (Date.now() - date.getTime()) / 1000;
  if (diff < 0) return fmtTime(input);
  if (diff < 60) return '刚刚';
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)} 天前`;
  return fmtTime(input);
}

function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined || seconds < 0) return '—';
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

function fmtNumber(n) {
  return new Intl.NumberFormat('zh-CN').format(Number(n) || 0);
}

/* ---------------- 状态徽标 ---------------- */
const STATUS_MAP = {
  completed: { text: '已完成', cls: 'badge-green' },
  done: { text: '已完成', cls: 'badge-green' },
  running: { text: '进行中', cls: 'badge-brand' },
  retrying: { text: '重试中', cls: 'badge-amber' },
  pending: { text: '排队中', cls: '' },
  queued: { text: '排队中', cls: '' },
  failed: { text: '失败', cls: 'badge-red' },
  cancelled: { text: '已取消', cls: '' },
  valid: { text: '已登录', cls: 'badge-green' },
  expired: { text: '已失效', cls: 'badge-red' },
  empty: { text: '未登录', cls: '' },
  unknown: { text: '未检测', cls: '' },
};

function statusBadge(status, textOverride) {
  const meta = STATUS_MAP[status] || { text: status || '未知', cls: '' };
  return `<span class="badge ${meta.cls}">${escapeHtml(textOverride || meta.text)}</span>`;
}

const PLATFORM_LABEL = { netease: '网易云', apple_music: 'Apple Music' };
const TYPE_LABEL = { song: '单曲', album: '专辑', playlist: '歌单' };

/* ---------------- 小工具 ---------------- */
function $(sel, scope = document) { return scope.querySelector(sel); }
function $$(sel, scope = document) { return Array.from(scope.querySelectorAll(sel)); }

function debounce(fn, wait = 300) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

function poll(fn, interval = 5000) {
  let stopped = false;
  const tick = async () => {
    if (stopped) return;
    try { await fn(); } catch (e) { /* 轮询失败静默 */ }
    if (!stopped) setTimeout(tick, interval);
  };
  tick();
  return () => { stopped = true; };
}

function copyText(text) {
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).then(() => toast('已复制', 'ok')).catch(() => fallback());
  } else { fallback(); }
  function fallback() {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); toast('已复制', 'ok'); } catch (e) { toast('复制失败', 'err'); }
    ta.remove();
  }
}

function setBusy(button, busy, busyText = '处理中…') {
  if (!button) return;
  if (busy) {
    button.dataset.originalHtml = button.innerHTML;
    button.disabled = true;
    button.innerHTML = `<span class="spin">${icon('refresh', 15)}</span>${escapeHtml(busyText)}`;
  } else {
    button.disabled = false;
    if (button.dataset.originalHtml) button.innerHTML = button.dataset.originalHtml;
  }
}

/* ---------------- 任务轮询 ---------------- */
async function watchJob(jobId, onUpdate, interval = 1500) {
  return new Promise((resolve, reject) => {
    const timer = setInterval(async () => {
      try {
        const job = await api(`/api/jobs/${jobId}`);
        onUpdate?.(job);
        if (['done', 'failed', 'cancelled'].includes(job.status)) {
          clearInterval(timer);
          if (job.status === 'done') resolve(job);
          else reject(new Error(job.error || job.message || '任务未完成'));
        }
      } catch (e) {
        clearInterval(timer);
        reject(e);
      }
    }, interval);
  });
}

/* ---------------- 侧边栏 ---------------- */
function initShell() {
  const toggle = $('.sidebar-toggle');
  const sidebar = $('.sidebar');
  if (toggle && sidebar) {
    toggle.addEventListener('click', (e) => {
      e.stopPropagation();
      sidebar.classList.toggle('open');
    });
    document.addEventListener('click', (e) => {
      if (window.innerWidth <= 960 && sidebar.classList.contains('open')
        && !sidebar.contains(e.target) && !toggle.contains(e.target)) {
        sidebar.classList.remove('open');
      }
    });
  }

  const logout = $('[data-action="logout"]');
  if (logout) {
    logout.addEventListener('click', async () => {
      const ok = await confirmDialog({ title: '退出登录？', desc: '需要重新输入账号密码。', okText: '退出' });
      if (!ok) return;
      await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' });
      window.location.href = '/login';
    });
  }
}

/* 注入 SVG 图标的占位元素：<i data-icon="music" data-size="16"></i> */
function hydrateIcons(scope = document) {
  $$('[data-icon]', scope).forEach((el) => {
    if (el.dataset.hydrated) return;
    el.innerHTML = icon(el.dataset.icon, Number(el.dataset.size || 18));
    el.dataset.hydrated = '1';
    el.style.display = 'inline-flex';
  });
}

document.addEventListener('DOMContentLoaded', () => {
  hydrateIcons();
  initShell();
});
