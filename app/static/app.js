/* Notra — front end.
   Recording happens in the browser; everything else is the FastAPI backend. */

const $ = (id) => document.getElementById(id);

const UNCATEGORIZED = '__uncategorized__'; // sentinel: never a real course name

const state = {
  lectures: [],
  courses: [],
  currentId: null,
  currentCourse: null,
  view: 'home',
  pendingBlob: null,
  pendingName: null, // original filename when the blob came from an upload
  renderedStatus: null,
};

/* ═════════════════════════════ helpers ═════════════════════════════ */

function toast(message, kind = '') {
  const el = $('toast');
  el.textContent = message;
  el.className = 'toast ' + kind;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add('hidden'), 4200);
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* not json */ }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

const pad = (n) => String(n).padStart(2, '0');

function clock(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return (h ? `${h}:${pad(m)}` : pad(m)) + ':' + pad(s % 60);
}

function humanDate(iso) {
  if (!iso) return '';
  const d = new Date(iso + 'T00:00:00');
  if (isNaN(d)) return iso;
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

/* ═════════════════════════════ recorder ═════════════════════════════ */

const rec = {
  mediaRecorder: null,
  streams: [],
  audioCtx: null,
  analyser: null,
  chunks: [],
  startedAt: 0,
  accumulated: 0,
  paused: false,
  raf: null,
  levels: new Array(140).fill(0),
};

function pickMimeType() {
  const candidates = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/ogg;codecs=opus',
    'audio/mp4',
  ];
  return candidates.find((t) => MediaRecorder.isTypeSupported(t)) || '';
}

async function buildStream(source) {
  const captured = [];
  const micConstraints = {
    audio: { echoCancellation: false, noiseSuppression: true, autoGainControl: true },
  };

  if (source === 'mic' || source === 'both') {
    captured.push(await navigator.mediaDevices.getUserMedia(micConstraints));
  }

  if (source === 'system' || source === 'both') {
    const display = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
    display.getVideoTracks().forEach((t) => t.stop()); // we only want the audio
    if (display.getAudioTracks().length === 0) {
      captured.forEach((s) => s.getTracks().forEach((t) => t.stop()));
      throw new Error(
        'No system audio was shared. Re-pick the tab or window and tick "Share tab audio".'
      );
    }
    captured.push(display);
  }

  rec.streams = captured;
  rec.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  const destination = rec.audioCtx.createMediaStreamDestination();
  rec.analyser = rec.audioCtx.createAnalyser();
  rec.analyser.fftSize = 1024;

  captured.forEach((stream) => {
    const node = rec.audioCtx.createMediaStreamSource(stream);
    node.connect(destination);
    node.connect(rec.analyser);
  });

  return destination.stream;
}

function drawWave() {
  const canvas = $('waveform');
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
    canvas.width = w * dpr; canvas.height = h * dpr;
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  if (rec.analyser && !rec.paused) {
    const buf = new Uint8Array(rec.analyser.fftSize);
    rec.analyser.getByteTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) {
      const v = (buf[i] - 128) / 128;
      sum += v * v;
    }
    rec.levels.push(Math.min(1, Math.sqrt(sum / buf.length) * 3.2));
    rec.levels.shift();
  } else if (rec.analyser && rec.paused) {
    rec.levels.push(rec.levels[rec.levels.length - 1] * 0.9);
    rec.levels.shift();
  }

  const n = rec.levels.length;
  const barW = w / n;
  const mid = h / 2;
  for (let i = 0; i < n; i++) {
    const level = rec.levels[i];
    const barH = Math.max(2, level * (h * 0.78));
    // ink strokes on paper: older bars fade like drying ink
    const alpha = 0.18 + 0.62 * (i / n);
    ctx.fillStyle = rec.paused
      ? `rgba(156,107,31,${alpha * 0.7})`
      : `rgba(28,27,24,${alpha})`;
    ctx.fillRect(i * barW + barW * 0.18, mid - barH / 2, barW * 0.64, barH);
  }

  rec.raf = requestAnimationFrame(drawWave);
}

function elapsed() {
  if (!rec.mediaRecorder) return rec.accumulated;
  return rec.accumulated + (rec.paused ? 0 : (Date.now() - rec.startedAt) / 1000);
}

function tickTimer() {
  $('timer').textContent = clock(elapsed());
}

function setRecStatus(text, cls = '') {
  $('recStatus').textContent = text;
  $('recStatus').className = 'rec-status ' + cls;
}

async function requireApiKey() {
  // Hard gate: nothing that leads to a Claude/Gemini call starts without a
  // key. Checked fresh each time — the user may have just saved one.
  try {
    const h = await api('/api/health');
    if (h.api_key) return true;
  } catch { return true; } // server unreachable: let the action surface that error
  toast('No API key set. Add one in Settings to generate notes.', 'err');
  openSettings();
  return false;
}

async function startRecording() {
  if (!(await requireApiKey())) return;
  const source = $('sourceSelect').value;
  $('recHint').textContent = '';
  try {
    const stream = await buildStream(source);
    const mimeType = pickMimeType();
    rec.mediaRecorder = new MediaRecorder(stream, mimeType ? { mimeType } : {});
    rec.chunks = [];
    rec.pendingExt = mimeType.includes('ogg') ? 'ogg' : mimeType.includes('mp4') ? 'm4a' : 'webm';

    rec.mediaRecorder.ondataavailable = (e) => { if (e.data.size) rec.chunks.push(e.data); };
    rec.mediaRecorder.onstop = finishRecording;
    rec.mediaRecorder.start(2000); // flush every 2s so a crash costs little

    rec.accumulated = 0;
    rec.startedAt = Date.now();
    rec.paused = false;
    rec.levels.fill(0);

    $('recordBtn').classList.add('recording');
    $('recordBtn').disabled = true;
    $('pauseBtn').disabled = false;
    $('stopBtn').disabled = false;
    $('sourceSelect').disabled = true;
    setRecStatus('Recording', 'live');
    $('recHint').textContent = 'Keep this tab open. You can pause and resume freely.';

    drawWave();
    rec.timerId = setInterval(tickTimer, 250);
  } catch (err) {
    const msg = err.name === 'NotAllowedError'
      ? 'Permission denied. Allow microphone (or screen-audio) access and try again.'
      : err.message;
    $('recHint').textContent = msg;
    $('recHint').classList.add('warn');
    toast(msg, 'err');
    cleanupStreams();
  }
}

function togglePause() {
  if (!rec.mediaRecorder) return;
  if (rec.paused) {
    rec.mediaRecorder.resume();
    rec.startedAt = Date.now();
    rec.paused = false;
    $('pauseBtn').textContent = 'Pause';
    setRecStatus('Recording', 'live');
  } else {
    rec.mediaRecorder.pause();
    rec.accumulated += (Date.now() - rec.startedAt) / 1000;
    rec.paused = true;
    $('pauseBtn').textContent = 'Resume';
    setRecStatus('Paused', 'paused');
  }
}

function stopRecording() {
  if (!rec.mediaRecorder) return;
  if (!rec.paused) rec.accumulated += (Date.now() - rec.startedAt) / 1000;
  rec.paused = true;
  setRecStatus('Finishing…');
  rec.mediaRecorder.stop();
}

function cleanupStreams() {
  rec.streams.forEach((s) => s.getTracks().forEach((t) => t.stop()));
  rec.streams = [];
  if (rec.audioCtx) { rec.audioCtx.close().catch(() => {}); rec.audioCtx = null; }
  rec.analyser = null;
  cancelAnimationFrame(rec.raf);
  clearInterval(rec.timerId);
  $('recordBtn').classList.remove('recording');
  $('recordBtn').disabled = false;
  $('pauseBtn').disabled = true;
  $('pauseBtn').textContent = 'Pause';
  $('stopBtn').disabled = true;
  $('sourceSelect').disabled = false;
}

function finishRecording() {
  const blob = new Blob(rec.chunks, { type: rec.mediaRecorder.mimeType || 'audio/webm' });
  const seconds = rec.accumulated;
  rec.mediaRecorder = null;
  cleanupStreams();
  setRecStatus(`Recorded ${clock(seconds)}`);

  if (blob.size < 2000 || seconds < 2) {
    $('recHint').textContent = 'That recording was too short to transcribe. Try again.';
    $('recHint').classList.add('warn');
    return;
  }

  state.pendingBlob = blob;
  state.pendingName = `recording.${rec.pendingExt || 'webm'}`;
  clearPickedFile();
  submitRecording();
}

/* ═════════════════════════════ file upload ═════════════════════════════ */

function clearPickedFile() {
  $('fileInput').value = '';
  $('fileChip').classList.add('hidden');
}

function pickFile(file) {
  if (!file) return;
  if (file.size < 2000) {
    toast('That file is too small to contain a lecture.', 'err');
    return;
  }
  state.pendingBlob = file;
  state.pendingName = file.name || 'upload.webm';
  const mb = (file.size / (1 << 20)).toFixed(1);
  $('fileChipName').textContent = `${state.pendingName} · ${mb} MB`;
  $('fileChip').classList.remove('hidden');
  submitRecording();
}

/* ═════════════════════════════ courses ═════════════════════════════ */

async function loadCourses() {
  try { state.courses = await api('/api/courses'); } catch { return; }
  const sel = $('fCourse');
  const previous = sel.value;
  sel.innerHTML = '';
  sel.append(new Option('Select a course…', ''));
  for (const c of state.courses) sel.append(new Option(c.name, c.name));
  sel.append(new Option('＋ New course…', '__new__'));
  // Keep the user's selection across refreshes if it still exists.
  sel.value = [...sel.options].some((o) => o.value === previous) ? previous : '';
  toggleNewCourseField();
}

function toggleNewCourseField() {
  const isNew = $('fCourse').value === '__new__';
  $('newCourseField').classList.toggle('hidden', !isNew);
  if (isNew) $('fNewCourse').focus();
}

function selectedCourse() {
  return $('fCourse').value === '__new__'
    ? $('fNewCourse').value.trim()
    : $('fCourse').value.trim();
}

/* ═════════════════════════════ upload ═════════════════════════════ */

function readForm() {
  return {
    course: selectedCourse(),
    lecture_date: $('fDate').value.trim(),
    topic: $('fTopic').value.trim(),
    instructor: $('fInstructor').value.trim(),
    extra_notes: $('fExtra').value.trim(),
  };
}

function showGenerateButton(message) {
  const box = $('uploadStatus');
  box.className = 'upload-status err';
  box.innerHTML = '';
  box.append(message + ' ');
  const btn = document.createElement('button');
  btn.className = 'btn btn-primary';
  btn.textContent = 'Generate notes';
  btn.style.marginLeft = '10px';
  btn.onclick = submitRecording;
  box.append(btn);
}

async function submitRecording() {
  if (!state.pendingBlob) return;
  if (!(await requireApiKey())) return;
  const meta = readForm();
  const missing = [];
  if (!meta.course) missing.push('course');
  if (!meta.lecture_date) missing.push('date');
  if (!meta.topic) missing.push('lecture topic');
  if (missing.length) {
    showGenerateButton(`Your recording is saved. Fill in the ${missing.join(', ')} above, then:`);
    return;
  }

  const box = $('uploadStatus');
  box.className = 'upload-status';
  box.textContent = 'Uploading recording…';

  const form = new FormData();
  form.append('audio', state.pendingBlob, state.pendingName || `recording.${rec.pendingExt || 'webm'}`);
  Object.entries(meta).forEach(([k, v]) => form.append(k, v));

  try {
    const { id } = await api('/api/lectures', { method: 'POST', body: form });
    state.pendingBlob = null;
    state.pendingName = null;
    clearPickedFile();
    box.textContent = '';
    ['fTopic', 'fInstructor', 'fExtra', 'fNewCourse'].forEach((f) => { $(f).value = ''; });
    $('fCourse').value = '';
    toggleNewCourseField();
    $('timer').textContent = '00:00';
    setRecStatus('Ready');
    await Promise.all([refreshLibrary(), loadCourses()]);
    openLecture(id);
    toast('Recording uploaded. Transcription started.', 'ok');
  } catch (err) {
    box.className = 'upload-status err';
    box.textContent = 'Upload failed: ' + err.message;
    showGenerateButton('Upload failed: ' + err.message);
  }
}

/* ═════════════════════════════ library ═════════════════════════════ */

function buildLecItem(lec, { showCourse = true } = {}) {
  const item = document.createElement('button');
  item.className = 'lec-item' + (lec.id === state.currentId ? ' active' : '');
  item.onclick = () => openLecture(lec.id);

  const dot = document.createElement('span');
  dot.className = 'sd ' + lec.status;

  const body = document.createElement('span');
  const title = document.createElement('span');
  title.className = 'lt';
  title.textContent = lec.topic || 'Untitled lecture';
  const sub = document.createElement('span');
  sub.className = 'ls';
  const bits = showCourse ? [lec.course, humanDate(lec.lecture_date)] : [humanDate(lec.lecture_date)];
  sub.textContent = bits.filter(Boolean).join(' · ');
  body.append(title, sub);

  item.append(dot, body);
  return item;
}

async function refreshLibrary() {
  try {
    state.lectures = await api('/api/lectures');
  } catch { return; }

  $('libCount').textContent = state.lectures.length;
  const list = $('lectureList');
  list.innerHTML = '';

  if (!state.lectures.length) {
    const empty = document.createElement('div');
    empty.className = 'empty-lib';
    empty.textContent = 'No lectures yet. Record one to get started.';
    list.append(empty);
    return;
  }

  for (const lec of state.lectures) list.append(buildLecItem(lec));
}

/* ═════════════════════════════ course browser ═════════════════════════════ */

function courseGroups() {
  // Map course name (or the UNCATEGORIZED sentinel) -> its lectures, most
  // recent first — state.lectures is already ordered that way by the API.
  const groups = new Map();
  for (const c of state.courses) groups.set(c.name, []);
  for (const lec of state.lectures) {
    const key = (lec.course || '').trim() || UNCATEGORIZED;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(lec);
  }
  return groups;
}

async function openCourses() {
  state.currentId = null;
  showView('courses');
  if (location.hash !== '#/courses') location.hash = '#/courses';
  await loadCourses();
  await refreshLibrary();
  renderCourseGrid();
}

function renderCourseGrid() {
  const grid = $('courseGrid');
  grid.innerHTML = '';
  const groups = courseGroups();

  if (!groups.size) {
    const empty = document.createElement('div');
    empty.className = 'empty-lib';
    empty.textContent = 'No courses yet. Set a course when you record a lecture.';
    grid.append(empty);
    return;
  }

  const names = [...groups.keys()].sort((a, b) =>
    a === UNCATEGORIZED ? 1 : b === UNCATEGORIZED ? -1 : a.localeCompare(b));

  for (const name of names) {
    const lectures = groups.get(name);
    const card = document.createElement('button');
    card.className = 'course-card' + (name === UNCATEGORIZED ? ' uncategorized' : '');
    card.onclick = () => openCourseDetail(name);

    const h3 = document.createElement('h3');
    h3.textContent = name === UNCATEGORIZED ? 'Uncategorized' : name;

    const count = document.createElement('span');
    count.className = 'course-count';
    count.textContent = lectures.length === 1 ? '1 lecture' : `${lectures.length} lectures`;

    card.append(h3, count);

    if (lectures.length) {
      const last = lectures[0];
      const line = document.createElement('span');
      line.className = 'course-last';
      const when = humanDate(last.lecture_date);
      line.textContent = `Last: ${last.topic || 'Untitled lecture'}${when ? ' · ' + when : ''}`;
      card.append(line);
    }

    grid.append(card);
  }
}

async function openCourseDetail(name) {
  state.currentId = null;
  state.currentCourse = name;
  showView('course-detail');
  const hashName = name === UNCATEGORIZED ? '_uncategorized' : encodeURIComponent(name);
  if (location.hash !== '#/course/' + hashName) location.hash = '#/course/' + hashName;
  await refreshLibrary();
  renderCourseDetail();
}

function renderCourseDetail() {
  const name = state.currentCourse;
  $('courseDetailName').textContent = name === UNCATEGORIZED ? 'Uncategorized' : name;

  const lectures = courseGroups().get(name) || [];
  $('courseDetailMeta').textContent =
    lectures.length === 1 ? '1 lecture' : `${lectures.length} lectures`;

  const list = $('courseDetailList');
  list.innerHTML = '';
  if (!lectures.length) {
    const empty = document.createElement('div');
    empty.className = 'empty-lib';
    empty.textContent = 'No lectures in this course yet.';
    list.append(empty);
    return;
  }
  for (const lec of lectures) list.append(buildLecItem(lec, { showCourse: false }));
}

/* ═════════════════════════════ lecture view ═════════════════════════════ */

const STEPS = [
  ['converting',   'Preparing audio'],
  ['transcribing', 'Transcribing'],
  ['writing',      'Claude writing notes'],
  ['compiling',    'Rendering PDF'],
];

function showView(name) {
  state.view = name;
  $('homeView').classList.toggle('hidden', name !== 'home');
  $('recordView').classList.toggle('hidden', name !== 'record');
  $('lectureView').classList.toggle('hidden', name !== 'lecture');
  $('coursesView').classList.toggle('hidden', name !== 'courses');
  $('courseDetailView').classList.toggle('hidden', name !== 'course-detail');
  $('navHome').classList.toggle('active', name === 'home');
  $('navCourses').classList.toggle('active', name === 'courses' || name === 'course-detail');
}

function openHome() {
  state.currentId = null;
  showView('home');
  if (location.hash && location.hash !== '#how') location.hash = '';
  refreshLibrary();
}

function openRecorder() {
  state.currentId = null;
  showView('record');
  if (location.hash !== '#/record') location.hash = '#/record';
  refreshLibrary();
  loadCourses();
}

async function openLecture(id) {
  state.currentId = id;
  showView('lecture');
  if (location.hash !== '#/l/' + id) location.hash = '#/l/' + id;
  await renderLecture();
  refreshLibrary();
}

function routeFromHash() {
  const lecMatch = location.hash.match(/^#\/l\/([A-Za-z0-9]+)$/);
  const courseMatch = location.hash.match(/^#\/course\/(.+)$/);
  if (lecMatch) {
    if (lecMatch[1] !== state.currentId) openLecture(lecMatch[1]);
  } else if (location.hash === '#/record') {
    if (state.view !== 'record') openRecorder();
  } else if (location.hash === '#/courses') {
    if (state.view !== 'courses') openCourses();
  } else if (courseMatch) {
    const name = courseMatch[1] === '_uncategorized'
      ? UNCATEGORIZED : decodeURIComponent(courseMatch[1]);
    if (name !== state.currentCourse) openCourseDetail(name);
  } else if (location.hash === '#how') {
    if (state.view !== 'home') showView('home');
  } else if (state.view !== 'home') {
    openHome();
  }
}

function renderSteps(stage) {
  const order = STEPS.map((s) => s[0]);
  const index = order.indexOf(stage);
  $('steps').innerHTML = '';
  STEPS.forEach(([key, label], i) => {
    const done = index > i || stage === 'done';
    const active = index === i;
    const el = document.createElement('div');
    el.className = 'step' + (done ? ' done' : active ? ' active' : '');
    const num = document.createElement('span');
    num.className = 'num';
    num.textContent = done ? '✓' : String(i + 1);
    el.append(num, document.createTextNode(label));
    $('steps').append(el);
  });
}

const STAGE_LABEL = {
  queued: 'Queued…',
  converting: 'Decoding the recording…',
  transcribing: 'Transcribing speech locally. This is the slow part on CPU.',
  writing: 'Claude is writing your notes…',
  compiling: 'Compiling the LaTeX into a PDF…',
  done: 'Done.',
};

async function renderLecture() {
  const id = state.currentId;
  if (!id) return;

  let lec;
  try { lec = await api('/api/lectures/' + id); }
  catch { openRecorder(); return; }

  $('lecTopic').textContent = lec.topic || 'Untitled lecture';
  $('lecMeta').textContent = [
    lec.course,
    humanDate(lec.lecture_date),
    lec.instructor,
    lec.duration_sec ? clock(lec.duration_sec) + ' recording' : '',
  ].filter(Boolean).join('  ·  ');

  state.renderedStatus = lec.status;
  const badge = $('lecStatus');
  badge.className = 'status-badge ' + lec.status;
  badge.textContent = { ready: 'Ready', processing: 'Working', queued: 'Queued', error: 'Failed' }[lec.status] || lec.status;

  const busy = lec.status === 'processing' || lec.status === 'queued';
  $('progressCard').classList.toggle('hidden', !busy);
  $('errorCard').classList.toggle('hidden', lec.status !== 'error');
  $('resultCard').classList.toggle('hidden', !(lec.status === 'ready' && lec.has_pdf));
  $('regenBtn').disabled = busy;

  if (busy) {
    renderSteps(lec.stage);
    $('barFill').style.width = Math.round((lec.progress || 0) * 100) + '%';
    $('progressLabel').textContent = STAGE_LABEL[lec.stage] || 'Working…';
  }

  if (lec.status === 'error') {
    $('errorText').textContent = lec.error || 'Unknown error.';
  }

  if (lec.status === 'ready' && lec.has_pdf) {
    const src = `/api/lectures/${id}/pdf?v=${Math.floor(lec.updated_at)}#view=FitH`;
    if ($('pdfFrame').dataset.src !== src) {
      $('pdfFrame').dataset.src = src;
      $('pdfFrame').src = src;
    }
    $('dlPdf').href = `/api/lectures/${id}/pdf?download=1`;
    $('dlTex').href = `/api/lectures/${id}/tex`;
    renderImpact(lec);
  }

  if (lec.transcript) {
    try {
      const res = await fetch('/api/lectures/' + id + '/transcript');
      $('transcriptText').textContent = await res.text();
    } catch { $('transcriptText').textContent = lec.transcript; }
    $('audioPlayer').src = '/api/lectures/' + id + '/audio';
  }
}

// Same rough constants as app/costs.py — kept in sync by hand since this is
// just formatting an equivalent, not a second source of truth for the number.
const CAR_G_PER_KM = 251;
const PHONE_G_PER_CHARGE = 8;
const TREE_G_PER_DAY = 21000 / 365;

function renderImpact(lec) {
  const strip = $('impactStrip');
  if (!lec.cost_usd && !lec.co2_g) { strip.classList.add('hidden'); return; }
  strip.classList.remove('hidden');

  $('impCost').textContent = '$' + (lec.cost_usd || 0).toFixed(lec.cost_usd < 0.01 ? 4 : 2);
  $('impEnergy').textContent = (lec.energy_wh || 0).toFixed(1) + ' Wh';
  $('impCo2').textContent = (lec.co2_g || 0).toFixed(1) + ' g CO₂e';

  const co2 = lec.co2_g || 0;
  const km = co2 / CAR_G_PER_KM;
  const charges = co2 / PHONE_G_PER_CHARGE;
  const treeHours = (co2 / TREE_G_PER_DAY) * 24;
  let equiv;
  if (km >= 0.05) equiv = `≈ driving ${km.toFixed(2)} km`;
  else if (charges >= 0.3) equiv = `≈ ${charges.toFixed(1)} phone charges`;
  else equiv = `≈ ${treeHours.toFixed(1)} tree-hours of CO₂ absorbed`;
  $('impEquiv').textContent = equiv;
}

function switchTab(name) {
  document.querySelectorAll('.tab').forEach((t) =>
    t.classList.toggle('active', t.dataset.tab === name));
  $('tabPdf').classList.toggle('hidden', name !== 'pdf');
  $('tabTranscript').classList.toggle('hidden', name !== 'transcript');
}

/* ═════════════════════════════ settings ═════════════════════════════ */

function toggleProviderFields() {
  const isGemini = $('sProvider').value === 'gemini';
  $('anthropicFields').classList.toggle('hidden', isGemini);
  $('geminiFields').classList.toggle('hidden', !isGemini);
}

async function openSettings() {
  try {
    const s = await api('/api/settings');
    $('sProvider').value = s.AI_PROVIDER || 'anthropic';
    toggleProviderFields();

    $('sKey').value = '';
    const chip = $('keyStatus');
    chip.className = 'key-chip ' + (s.anthropic_key_set ? 'ok' : 'none');
    chip.textContent = s.anthropic_key_set ? 'Key active' : 'No key set';
    $('keyHint').textContent = s.anthropic_key_set
      ? `A key is saved (ends ${s.ANTHROPIC_API_KEY}). Leave blank to keep it.`
      : 'Required. Get one at console.anthropic.com. Stored locally in .env.';

    $('sGeminiKey').value = '';
    const gchip = $('geminiKeyStatus');
    gchip.className = 'key-chip ' + (s.gemini_key_set ? 'ok' : 'none');
    gchip.textContent = s.gemini_key_set ? 'Key active' : 'No key set';
    $('geminiKeyHint').textContent = s.gemini_key_set
      ? `A key is saved (ends ${s.GEMINI_API_KEY}). Leave blank to keep it.`
      : 'Free keys at aistudio.google.com/apikey. Stored locally in .env.';

    $('sModel').value = s.CLAUDE_MODEL;
    $('sEffort').value = s.CLAUDE_EFFORT;
    $('sGeminiModel').value = s.GEMINI_MODEL || 'gemini-2.5-flash';
    $('sWhisper').value = s.WHISPER_MODEL;
    $('sWhisperThreads').value = s.WHISPER_CPU_THREADS || '0';
    $('sLang').value = s.WHISPER_LANGUAGE || '';
    $('sDataDir').textContent = s.data_dir || '';
    $('sStyle').value = s.NOTES_STYLE;
    $('settingsModal').classList.remove('hidden');
  } catch (err) {
    toast('Could not load settings: ' + err.message, 'err');
  }
}

async function saveSettings() {
  const payload = {
    AI_PROVIDER: $('sProvider').value,
    ANTHROPIC_API_KEY: $('sKey').value.trim(),
    CLAUDE_MODEL: $('sModel').value,
    CLAUDE_EFFORT: $('sEffort').value,
    GEMINI_API_KEY: $('sGeminiKey').value.trim(),
    GEMINI_MODEL: $('sGeminiModel').value,
    WHISPER_MODEL: $('sWhisper').value,
    WHISPER_CPU_THREADS: String(Math.max(0, parseInt($('sWhisperThreads').value, 10) || 0)),
    WHISPER_LANGUAGE: $('sLang').value,
    NOTES_STYLE: $('sStyle').value,
  };
  try {
    await api('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    $('settingsModal').classList.add('hidden');
    toast('Settings saved.', 'ok');
    refreshHealth();
  } catch (err) {
    toast('Could not save: ' + err.message, 'err');
  }
}

async function refreshHealth() {
  let h;
  try { h = await api('/api/health'); } catch { return; }
  const strip = $('healthStrip');
  strip.innerHTML = '';
  const badges = [
    ['API key', h.api_key],
    ['ffmpeg', h.ffmpeg],
    [h.latex_engine || 'latex', !!h.latex_engine],
    [h.whisper_model, true],
  ];
  for (const [label, ok] of badges) {
    const el = document.createElement('span');
    el.className = ok ? 'good' : 'bad';
    el.textContent = (ok ? '' : '⚠ ') + label;
    strip.append(el);
  }
  if (!h.api_key) $('healthStrip').title = 'Add your Anthropic API key in Settings.';
}

/* ═════════════════════════════ wiring ═════════════════════════════ */

$('recordBtn').onclick = startRecording;
$('pauseBtn').onclick = togglePause;
$('stopBtn').onclick = stopRecording;
$('newRecordingBtn').onclick = openRecorder;
$('navHome').onclick = openHome;
$('navCourses').onclick = openCourses;
$('courseBackBtn').onclick = openCourses;
$('heroStartBtn').onclick = openRecorder;
$('ctaStartBtn').onclick = openRecorder;
$('settingsBtn').onclick = openSettings;
$('fCourse').onchange = toggleNewCourseField;

// file upload
$('browseBtn').onclick = (e) => { e.stopPropagation(); $('fileInput').click(); };
$('dropZone').onclick = () => $('fileInput').click();
$('fileInput').onchange = () => pickFile($('fileInput').files[0]);
$('dropZone').ondragover = (e) => { e.preventDefault(); $('dropZone').classList.add('dragover'); };
$('dropZone').ondragleave = () => $('dropZone').classList.remove('dragover');
$('dropZone').ondrop = (e) => {
  e.preventDefault();
  $('dropZone').classList.remove('dragover');
  pickFile(e.dataTransfer.files[0]);
};
$('fileChipRemove').onclick = () => {
  state.pendingBlob = null;
  state.pendingName = null;
  clearPickedFile();
  $('uploadStatus').textContent = '';
  $('uploadStatus').className = 'upload-status';
};
$('sProvider').onchange = toggleProviderFields;
$('settingsCancel').onclick = () => $('settingsModal').classList.add('hidden');
$('openDataDirBtn').onclick = async () => {
  try { await api('/api/open-data-folder', { method: 'POST' }); }
  catch (err) { toast(err.message, 'err'); }
};
$('settingsSave').onclick = saveSettings;
$('settingsModal').onclick = (e) => {
  if (e.target === $('settingsModal')) $('settingsModal').classList.add('hidden');
};

document.querySelectorAll('.tab').forEach((t) => { t.onclick = () => switchTab(t.dataset.tab); });

$('regenBtn').onclick = async () => {
  if (!state.currentId) return;
  if (!(await requireApiKey())) return;
  try {
    await api(`/api/lectures/${state.currentId}/regenerate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    toast('Regenerating notes from the existing transcript…', 'ok');
    renderLecture();
  } catch (err) { toast(err.message, 'err'); }
};

$('retryBtn').onclick = () => $('regenBtn').onclick();

$('deleteBtn').onclick = async () => {
  if (!state.currentId) return;
  if (!confirm('Delete this lecture, its audio, transcript and PDF? This cannot be undone.')) return;
  try {
    await api('/api/lectures/' + state.currentId, { method: 'DELETE' });
    openRecorder();
    toast('Lecture deleted.');
  } catch (err) { toast(err.message, 'err'); }
};

window.addEventListener('beforeunload', (e) => {
  if (rec.mediaRecorder || state.pendingBlob) {
    e.preventDefault();
    e.returnValue = '';
  }
});

// Poll: keep the library fresh, and the open lecture's progress moving.
setInterval(() => {
  refreshLibrary();
  if (state.view === 'lecture' && state.currentId) {
    const lec = state.lectures.find((l) => l.id === state.currentId);
    const busy = !lec || lec.status === 'processing' || lec.status === 'queued';
    // Also re-render on the transition tick, so a finished job never sits stale.
    if (busy || lec.status !== state.renderedStatus) renderLecture();
  }
}, 2000);

window.addEventListener('hashchange', routeFromHash);

// Scroll-reveal for the home page: elements with .reveal fade up when they
// enter the viewport (or immediately, if the user prefers reduced motion).
(() => {
  const els = document.querySelectorAll('.reveal');
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches || !('IntersectionObserver' in window)) {
    els.forEach((el) => el.classList.add('in'));
    return;
  }
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); }
    });
  }, { threshold: 0.15 });
  els.forEach((el) => io.observe(el));
})();

$('fDate').value = new Date().toISOString().slice(0, 10);
drawWave();
refreshLibrary().then(routeFromHash);
loadCourses();
refreshHealth();
