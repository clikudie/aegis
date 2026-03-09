const actionLine = document.getElementById('action-line');
const timerStateEl = document.getElementById('timer-state');
const timerCountdownEl = document.getElementById('timer-countdown');
const timerEtaEl = document.getElementById('timer-eta');
const countdownCard = document.getElementById('countdown-card');
const timerInput = document.getElementById('timer-minutes');
const ring = document.getElementById('countdown-ring');

let shutdownAtMs = null;
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
  if (!shutdownAtMs) {
    timerTotalSeconds = null;
    lastBeepSecond = null;
    timerStateEl.textContent = 'Idle';
    timerCountdownEl.textContent = '--:--';
    timerEtaEl.textContent = 'Set a timer to begin';
    setRingProgress(0);
    countdownCard.classList.remove('active', 'warning');
    return;
  }

  const remainingSec = Math.max(0, Math.ceil((shutdownAtMs - Date.now()) / 1000));
  if (timerTotalSeconds == null || remainingSec > timerTotalSeconds) {
    timerTotalSeconds = remainingSec;
  }

  const progress = timerTotalSeconds > 0 ? remainingSec / timerTotalSeconds : 0;
  setRingProgress(progress);

  timerCountdownEl.textContent = toMMSS(remainingSec);
  timerEtaEl.textContent = `Shutdown at ${new Date(shutdownAtMs).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;

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

function message(text) {
  actionLine.textContent = text;
  actionLine.style.color = '#1f6feb';
}

function errorMessage(err) {
  actionLine.textContent = err.message || 'Request failed';
  actionLine.style.color = '#b91c1c';
}

async function refresh() {
  try {
    const status = await api('/api/status');
    shutdownAtMs = status.next_shutdown_at ? new Date(status.next_shutdown_at).getTime() : null;
    if (!shutdownAtMs) {
      timerTotalSeconds = null;
      lastBeepSecond = null;
    }
    renderCountdown();
  } catch (err) {
    errorMessage(err);
    renderCountdown();
  }
}

document.getElementById('timer-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    const minutes = Number(timerInput.value);
    const data = await api('/api/timer', 'POST', { minutes });
    shutdownAtMs = data.timer_off_at ? new Date(data.timer_off_at).getTime() : Date.now() + Math.floor(minutes * 60) * 1000;
    timerTotalSeconds = Math.max(1, Math.floor(minutes * 60));
    lastBeepSecond = null;
    message(`Timer set for ${minutes} minutes.`);
    renderCountdown();
    setTimeout(refresh, 250);
  } catch (err) {
    errorMessage(err);
  }
});

document.getElementById('cancel-timer').onclick = async () => {
  try {
    await api('/api/timer/cancel', 'POST', {});
    shutdownAtMs = null;
    timerTotalSeconds = null;
    lastBeepSecond = null;
    message('Timer canceled.');
    renderCountdown();
    setTimeout(refresh, 250);
  } catch (err) {
    errorMessage(err);
  }
};

document.querySelectorAll('[data-minutes]').forEach((btn) => {
  btn.addEventListener('click', async () => {
    try {
      unlockAudio();
      const minutes = Number(btn.getAttribute('data-minutes'));
      timerInput.value = String(minutes);
      const data = await api('/api/timer', 'POST', { minutes });
      shutdownAtMs = data.timer_off_at ? new Date(data.timer_off_at).getTime() : Date.now() + Math.floor(minutes * 60) * 1000;
      timerTotalSeconds = minutes * 60;
      lastBeepSecond = null;
      message(`Timer set for ${minutes} minutes.`);
      renderCountdown();
      setTimeout(refresh, 250);
    } catch (err) {
      errorMessage(err);
    }
  });
});

['pointerdown', 'keydown', 'touchstart'].forEach((eventName) => {
  window.addEventListener(eventName, unlockAudio, { once: true, passive: true });
});

refresh();
setInterval(refresh, 5000);
setInterval(renderCountdown, 250);
