const actionLine = document.getElementById('action-line');
const timerStateEl = document.getElementById('timer-state');
const timerCountdownEl = document.getElementById('timer-countdown');
const timerEtaEl = document.getElementById('timer-eta');
const countdownCard = document.getElementById('countdown-card');
const timerInput = document.getElementById('timer-minutes');
const ring = document.getElementById('countdown-ring');

let latestStatus = null;
let timerTotalSeconds = null;
let lastBeepSecond = null;
let audioCtx = null;

const RING_RADIUS = 132;
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;
ring.style.strokeDasharray = `${RING_CIRCUMFERENCE}`;
ring.style.strokeDashoffset = `${RING_CIRCUMFERENCE}`;

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

function toMMSS(totalSec) {
  const sec = Math.max(0, Math.floor(totalSec));
  const min = Math.floor(sec / 60);
  const rem = sec % 60;
  return `${min.toString().padStart(2, '0')}:${rem.toString().padStart(2, '0')}`;
}

function setRingProgress(progress) {
  const clamped = Math.min(1, Math.max(0, progress));
  ring.style.strokeDashoffset = `${RING_CIRCUMFERENCE * (1 - clamped)}`;
}

function unlockAudio() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
  if (audioCtx.state === 'suspended') {
    audioCtx.resume().catch(() => {});
  }
}

function beepWarning(remainingSec) {
  if (!audioCtx || remainingSec < 1 || remainingSec > 10) return;
  if (lastBeepSecond === remainingSec) return;
  lastBeepSecond = remainingSec;

  const now = audioCtx.currentTime;
  const urgent = remainingSec <= 3;
  const pulseCount = urgent ? 2 : 1;
  const baseFrequency = urgent ? 1280 : 980;
  const altFrequency = urgent ? 980 : 780;
  const pulseGap = urgent ? 0.13 : 0.0;
  const pulseLength = urgent ? 0.12 : 0.18;

  for (let i = 0; i < pulseCount; i += 1) {
    const start = now + i * pulseGap;
    const oscA = audioCtx.createOscillator();
    const oscB = audioCtx.createOscillator();
    const gain = audioCtx.createGain();

    oscA.type = 'triangle';
    oscB.type = 'sine';
    oscA.frequency.setValueAtTime(baseFrequency, start);
    oscB.frequency.setValueAtTime(altFrequency, start);

    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(0.16, start + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + pulseLength);

    oscA.connect(gain);
    oscB.connect(gain);
    gain.connect(audioCtx.destination);
    oscA.start(start);
    oscB.start(start);
    oscA.stop(start + pulseLength + 0.01);
    oscB.stop(start + pulseLength + 0.01);
  }
}

function renderCountdown() {
  if (!latestStatus || !latestStatus.next_shutdown_at) {
    timerTotalSeconds = null;
    lastBeepSecond = null;
    timerStateEl.textContent = 'Idle';
    timerCountdownEl.textContent = '--:--';
    timerEtaEl.textContent = 'Set a timer to begin';
    setRingProgress(0);
    countdownCard.classList.remove('active', 'warning');
    return;
  }

  const now = new Date(latestStatus.now || Date.now());
  const at = new Date(latestStatus.next_shutdown_at);
  if (Number.isNaN(at.valueOf())) {
    timerStateEl.textContent = 'Armed';
    timerCountdownEl.textContent = '--:--';
    timerEtaEl.textContent = 'Shutdown scheduled';
    countdownCard.classList.add('active');
    countdownCard.classList.remove('warning');
    setRingProgress(1);
    return;
  }

  const remainingSec = Math.max(0, Math.ceil((at.getTime() - now.getTime()) / 1000));
  if (timerTotalSeconds == null || remainingSec > timerTotalSeconds) {
    timerTotalSeconds = remainingSec;
  }

  const progress = timerTotalSeconds > 0 ? remainingSec / timerTotalSeconds : 0;
  setRingProgress(progress);

  timerCountdownEl.textContent = toMMSS(remainingSec);
  timerEtaEl.textContent = `Shutdown at ${at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;

  countdownCard.classList.add('active');
  if (remainingSec <= 10 && remainingSec > 0) {
    timerStateEl.textContent = 'Final Countdown';
    countdownCard.classList.add('warning');
    beepWarning(remainingSec);
  } else if (remainingSec === 0) {
    timerStateEl.textContent = 'Shutting Down';
    countdownCard.classList.add('warning');
  } else {
    timerStateEl.textContent = 'Armed';
    countdownCard.classList.remove('warning');
  }
}

async function refresh() {
  try {
    const status = await api('/api/status');
    latestStatus = status;
    renderCountdown();
  } catch (err) {
    actionLine.textContent = err.message;
    actionLine.style.color = '#b91c1c';
    latestStatus = null;
    renderCountdown();
  }
}

function message(text) {
  actionLine.textContent = text;
  actionLine.style.color = '#1f6feb';
  setTimeout(refresh, 250);
}

document.getElementById('timer-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const minutes = Number(timerInput.value);
  await api('/api/timer', 'POST', { minutes });
  timerTotalSeconds = Math.max(1, Math.floor(minutes * 60));
  lastBeepSecond = null;
  message(`Timer set for ${minutes} minutes.`);
});

document.getElementById('cancel-timer').onclick = async () => {
  await api('/api/timer/cancel', 'POST', {});
  timerTotalSeconds = null;
  lastBeepSecond = null;
  message('Timer canceled.');
};

document.querySelectorAll('[data-minutes]').forEach((btn) => {
  btn.addEventListener('click', async () => {
    unlockAudio();
    const minutes = Number(btn.getAttribute('data-minutes'));
    timerInput.value = String(minutes);
    await api('/api/timer', 'POST', { minutes });
    timerTotalSeconds = minutes * 60;
    lastBeepSecond = null;
    message(`Timer set for ${minutes} minutes.`);
  });
});

['pointerdown', 'keydown', 'touchstart'].forEach((eventName) => {
  window.addEventListener(eventName, unlockAudio, { once: true, passive: true });
});

refresh();
setInterval(refresh, 5000);
setInterval(() => {
  if (!latestStatus) return;
  latestStatus = { ...latestStatus, now: new Date().toISOString() };
  renderCountdown();
}, 250);
