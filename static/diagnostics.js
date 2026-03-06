const updatedEl = document.getElementById('diag-updated');
const tvEl = document.getElementById('diag-tv');
const windowEl = document.getElementById('diag-window');
const overrideEl = document.getElementById('diag-override');
const timerEl = document.getElementById('diag-timer');
const windowsEl = document.getElementById('diag-windows');
const nowEl = document.getElementById('diag-now');
const tzEl = document.getElementById('diag-timezone');
const actionEl = document.getElementById('diag-last-action');
const actionAtEl = document.getElementById('diag-last-action-at');

async function api(path) {
  const res = await fetch(path);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function fmtIso(iso) {
  if (!iso) return 'None';
  const dt = new Date(iso);
  if (Number.isNaN(dt.valueOf())) return iso;
  return dt.toLocaleString();
}

function renderWindows(windows) {
  if (!windows || !windows.length) {
    windowsEl.innerHTML = '<p class="diag-empty">No windows configured.</p>';
    return;
  }
  windowsEl.innerHTML = windows
    .map((w) => {
      return `<article class="diag-window"><p>${w.day}</p><p>${w.start} - ${w.end}</p></article>`;
    })
    .join('');
}

async function refresh() {
  try {
    const s = await api('/api/status');
    updatedEl.textContent = `Updated ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
    updatedEl.style.color = '#0b6bcb';

    tvEl.textContent = s.tv_is_on ? 'On' : 'Off';
    windowEl.textContent = s.inside_allowed_window ? 'Allowed' : 'Blocked';
    overrideEl.textContent = s.override?.mode || 'none';
    timerEl.textContent = s.timer_off_at ? fmtIso(s.timer_off_at) : 'None';

    nowEl.textContent = fmtIso(s.now);
    tzEl.textContent = s.timezone || '-';
    actionEl.textContent = s.last_action || 'None';
    actionAtEl.textContent = fmtIso(s.last_action_at);

    renderWindows(s.schedule?.windows || []);
  } catch (err) {
    updatedEl.textContent = String(err.message || err);
    updatedEl.style.color = '#b91c1c';
  }
}

refresh();
setInterval(refresh, 5000);
