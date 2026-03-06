const stateLine = document.getElementById('status-line');
const windowsEl = document.getElementById('windows');
const summaryTv = document.getElementById('summary-tv');
const summaryWindow = document.getElementById('summary-window');
const summaryOverride = document.getElementById('summary-override');
const summaryNextShutdown = document.getElementById('summary-next-shutdown');
const summaryLastAction = document.getElementById('summary-last-action');

let scheduleDraft = {
  enabled: true,
  mode: 'strict',
  grace_minutes: 0,
  windows: [],
};
let scheduleDirty = false;
let latestStatus = null;

async function api(path, method = 'GET', body = null) {
  const res = await fetch(path, {
    method,
    headers: { 'content-type': 'application/json' },
    body: body ? JSON.stringify(body) : null,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || `Request failed (${res.status})`);
  }
  return data;
}

function renderWindows() {
  windowsEl.innerHTML = '';
  if (!scheduleDraft.windows.length) {
    const li = document.createElement('li');
    const left = document.createElement('span');
    left.textContent = 'No windows configured yet.';
    li.appendChild(left);
    windowsEl.appendChild(li);
    return;
  }
  scheduleDraft.windows.forEach((w, i) => {
    const li = document.createElement('li');
    const left = document.createElement('span');
    left.textContent = `${w.day} ${w.start}-${w.end}`;
    const del = document.createElement('button');
    del.type = 'button';
    del.textContent = 'Remove';
    del.onclick = () => {
      scheduleDirty = true;
      scheduleDraft.windows.splice(i, 1);
      renderWindows();
    };
    li.appendChild(left);
    li.appendChild(del);
    windowsEl.appendChild(li);
  });
}

function formatReason(reason) {
  const map = {
    timer: 'Timer',
    schedule_strict: 'Schedule',
    schedule_graceful: 'Schedule (Grace)',
  };
  return map[reason] || 'Policy';
}

function renderNextShutdown() {
  if (!latestStatus || !latestStatus.next_shutdown_at) {
    summaryNextShutdown.textContent = 'None';
    return;
  }

  const now = new Date(latestStatus.now || Date.now());
  const at = new Date(latestStatus.next_shutdown_at);
  const deltaMs = at.getTime() - now.getTime();
  const reason = formatReason(latestStatus.next_shutdown_reason);

  if (Number.isNaN(at.valueOf())) {
    summaryNextShutdown.textContent = `Scheduled (${reason})`;
    return;
  }

  if (deltaMs <= 0) {
    summaryNextShutdown.textContent = `Now (${reason})`;
    return;
  }

  const totalSec = Math.floor(deltaMs / 1000);
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  summaryNextShutdown.textContent = `${min}m ${sec.toString().padStart(2, '0')}s (${reason})`;
}

async function refresh() {
  try {
    const status = await api('/api/status');
    latestStatus = status;
    stateLine.textContent = `Live ${new Date(status.now).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
    stateLine.style.color = '#5b6472';
    summaryTv.textContent = status.tv_is_on ? 'On' : 'Off';
    summaryWindow.textContent = status.inside_allowed_window ? 'Allowed' : 'Blocked';
    summaryOverride.textContent = status.override.mode;
    renderNextShutdown();
    summaryLastAction.textContent = status.last_action || 'None';

    if (!scheduleDirty) {
      scheduleDraft = {
        enabled: status.schedule.enabled,
        mode: status.schedule.mode,
        grace_minutes: status.schedule.grace_minutes,
        windows: [...(status.schedule.windows || [])],
      };
      document.getElementById('sched-enabled').checked = scheduleDraft.enabled;
      document.getElementById('sched-mode').value = scheduleDraft.mode;
      document.getElementById('sched-grace').value = scheduleDraft.grace_minutes;
    }
    renderWindows();
  } catch (err) {
    stateLine.textContent = err.message;
    stateLine.style.color = '#b91c1c';
    latestStatus = null;
    summaryNextShutdown.textContent = 'None';
  }
}

function message(text) {
  stateLine.textContent = text;
  stateLine.style.color = '#0b6bcb';
  setTimeout(refresh, 300);
}

document.getElementById('timer-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const minutes = Number(document.getElementById('timer-minutes').value);
  await api('/api/timer', 'POST', { minutes });
  message(`Timer set for ${minutes} minutes.`);
});

document.getElementById('cancel-timer').onclick = async () => {
  await api('/api/timer/cancel', 'POST', {});
  message('Timer canceled.');
};

document.getElementById('add-window').onclick = () => {
  const day = document.getElementById('win-day').value;
  const start = document.getElementById('win-start').value;
  const end = document.getElementById('win-end').value;
  if (!start || !end) {
    stateLine.textContent = 'Start and end time are required.';
    stateLine.style.color = '#b91c1c';
    return;
  }
  scheduleDirty = true;
  scheduleDraft.windows.push({ day, start, end });
  renderWindows();
};

document.getElementById('save-schedule').onclick = async () => {
  scheduleDraft.enabled = document.getElementById('sched-enabled').checked;
  scheduleDraft.mode = document.getElementById('sched-mode').value;
  scheduleDraft.grace_minutes = Number(document.getElementById('sched-grace').value);
  await api('/api/schedule', 'POST', scheduleDraft);
  scheduleDirty = false;
  message('Schedule saved.');
};

document.getElementById('override-none').onclick = async () => {
  await api('/api/override', 'POST', { mode: 'none' });
  message('Override disabled.');
};

document.getElementById('override-perm').onclick = async () => {
  await api('/api/override', 'POST', { mode: 'permanent' });
  message('Permanent override enabled.');
};

document.getElementById('override-temp-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const minutes = Number(document.getElementById('override-minutes').value);
  await api('/api/override', 'POST', { mode: 'temporary', minutes });
  message(`Temporary override enabled for ${minutes} minutes.`);
});

refresh();
setInterval(refresh, 5000);
setInterval(() => {
  if (!latestStatus) return;
  const tickNow = new Date();
  latestStatus = { ...latestStatus, now: tickNow.toISOString() };
  renderNextShutdown();
}, 1000);
