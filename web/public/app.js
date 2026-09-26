'use strict';

// 料理しながら話した声を、発話ごとに区切って /api/transcribe に送り、文字起こしを積み上げる。
// 無音の間は何も送らないので、工程の間が長く空いても大丈夫。
// 「作り終わった」でレシピにまとめ (/api/recipes)、質問に答えて仕上げる。

const startBtn = document.getElementById('startBtn');
const stopBtn = document.getElementById('stopBtn');
const statusEl = document.getElementById('status');
const transcriptEl = document.getElementById('transcript');
const pendingEl = document.getElementById('pending');
const recipeBtn = document.getElementById('recipeBtn');
const recipeStatusEl = document.getElementById('recipeStatus');
const recipeViewEl = document.getElementById('recipeView');
const sampleScriptEl = document.getElementById('sampleScript');
const sampleBtn = document.getElementById('sampleBtn');
const familyPanelEl = document.getElementById('familyPanel');
const familyListEl = document.getElementById('familyList');

// ---- 発話の区切り方 ----
const TICK_MS = 50;
const SILENCE_END_MS = 900; // この長さ黙ったら 1 発話の終わり
const MIN_SPEECH_MS = 300; // これより短い音 (物音) だけの区間は送らない
const MAX_CHUNK_MS = 25000; // 話し続けていてもこの長さで区切る
const MIN_THRESHOLD = 0.012; // 声とみなす最小の音量 (RMS)

const STORAGE_KEY = 'voicehack-session';

let mediaStream = null;
let audioContext = null;
let analyser = null;
let recorder = null;
let tickTimer = null;
let wakeLock = null;
let chunk = null; // { startedAt, firstVoiceAt, speechMs, lastVoiceAt }
let noiseFloor = null;
let prosody = null; // ProsodyMeter (prosody.js): 発話ごとの声の大きさ・伸ばし
let freqBuf = null;
let pendingCount = 0;
let pendingDone = null;

let session = null; // { startedAt, segments: [{ text, at }], recipeId }
let currentRecipe = null;

// ---- 共通 ----

function el(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}

function setStatus(text, recording) {
  statusEl.textContent = text;
  statusEl.classList.toggle('recording', !!recording);
}

function saveSession() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  } catch (_) {
    /* 保存できなくても動作は続ける */
  }
}

function loadSession() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY));
  } catch (_) {
    return null;
  }
}

async function api(url, body) {
  const res = await fetch(url, body === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function pickMimeType() {
  const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'];
  return candidates.find((t) => window.MediaRecorder && MediaRecorder.isTypeSupported(t)) || '';
}

async function transcribe(blob) {
  const res = await fetch('/api/transcribe', {
    method: 'POST',
    headers: { 'Content-Type': blob.type || 'audio/webm' },
    body: blob,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data.text;
}

// ---- 聞き取り ----

function renderTranscript() {
  transcriptEl.replaceChildren();
  const segs = [...session.segments].sort((a, b) => a.at.localeCompare(b.at));
  const voices = describeVoice(segs); // prosody.js
  segs.forEach((s, i) => {
    const line = el('p', 'line');
    const t = new Date(s.at).toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit' });
    line.append(el('span', 'line-time', t), el('span', null, s.text));
    for (const tag of (voices[i] && voices[i].tags) || []) line.append(el('span', 'voice-tag', tag));
    transcriptEl.append(line);
  });
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

function setPending(delta) {
  pendingCount += delta;
  pendingEl.hidden = pendingCount === 0;
  if (pendingCount === 0 && pendingDone) {
    pendingDone();
    pendingDone = null;
  }
}

function waitPending() {
  if (pendingCount === 0) return Promise.resolve();
  return new Promise((resolve) => {
    pendingDone = resolve;
  });
}

function sendChunk(blob, at, voice) {
  setPending(1);
  transcribe(blob)
    .then((text) => {
      if (text && text.trim()) {
        session.segments.push({ text: text.trim(), at, voice });
        saveSession();
        renderTranscript();
      }
    })
    .catch((err) => setStatus(`文字起こしエラー: ${err.message}`, !!recorder))
    .finally(() => setPending(-1));
}

function startChunk() {
  const mimeType = pickMimeType();
  const rec = new MediaRecorder(mediaStream, mimeType ? { mimeType } : undefined);
  const parts = [];
  const info = { startedAt: Date.now(), firstVoiceAt: null, speechMs: 0, lastVoiceAt: 0 };
  rec.ondataavailable = (e) => {
    if (e.data && e.data.size) parts.push(e.data);
  };
  rec.onstop = () => {
    if (info.speechMs < MIN_SPEECH_MS || !parts.length) return;
    const blob = new Blob(parts, { type: rec.mimeType || mimeType || 'audio/webm' });
    sendChunk(blob, new Date(info.firstVoiceAt || info.startedAt).toISOString(), info.voice);
  };
  rec.start();
  recorder = rec;
  chunk = info;
}

function cutChunk(restart) {
  // この発話の声の出し方を記録して、次の発話に向けてリセット
  if (chunk && prosody) {
    chunk.voice = prosody.summary();
    prosody.reset();
  }
  const rec = recorder;
  recorder = null;
  if (rec && rec.state !== 'inactive') rec.stop();
  if (restart) startChunk();
}

function tick() {
  const buf = new Float32Array(analyser.fftSize);
  analyser.getFloatTimeDomainData(buf);
  let sum = 0;
  for (const v of buf) sum += v * v;
  const rms = Math.sqrt(sum / buf.length);

  // 周りの音の大きさを追いかけ、それより十分大きい音を声とみなす
  if (noiseFloor === null) noiseFloor = rms;
  const threshold = Math.max(MIN_THRESHOLD, noiseFloor * 2.5);
  const voiced = rms > threshold;
  if (!voiced) noiseFloor = noiseFloor * 0.95 + rms * 0.05;

  analyser.getFloatFrequencyData(freqBuf);
  prosody.push(20 * Math.log10(rms + 1e-9), freqBuf, voiced);

  const now = Date.now();
  if (voiced) {
    chunk.speechMs += TICK_MS;
    chunk.lastVoiceAt = now;
    if (!chunk.firstVoiceAt) chunk.firstVoiceAt = now;
  }
  const silentFor = now - chunk.lastVoiceAt;
  const endOfUtterance = chunk.speechMs >= MIN_SPEECH_MS && silentFor >= SILENCE_END_MS;
  const tooLong = now - chunk.startedAt >= MAX_CHUNK_MS;
  if (endOfUtterance || tooLong) cutChunk(true);

  setStatus(voiced ? '聞いています…(話し中)' : '聞いています…', true);
}

async function start() {
  startBtn.disabled = true;
  setStatus('マイクを準備しています…');
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false },
    });
  } catch (err) {
    setStatus('マイクが使えません。ブラウザの設定でマイクを許可してください');
    startBtn.disabled = false;
    return;
  }
  audioContext = new (window.AudioContext || window.webkitAudioContext)();
  await audioContext.resume();
  analyser = audioContext.createAnalyser();
  analyser.fftSize = 2048;
  analyser.smoothingTimeConstant = 0.3; // 伸ばしの検出のため、スペクトルをなめらかにしすぎない
  audioContext.createMediaStreamSource(mediaStream).connect(analyser);
  noiseFloor = null;
  freqBuf = new Float32Array(analyser.frequencyBinCount);
  prosody = new ProsodyMeter(TICK_MS);

  session = { startedAt: new Date().toISOString(), segments: [], recipeId: null };
  saveSession();
  renderTranscript();
  resetRecipe();

  startChunk();
  tickTimer = setInterval(tick, TICK_MS);
  requestWakeLock();
  stopBtn.disabled = false;
  setStatus('聞いています…', true);
}

async function stop() {
  stopBtn.disabled = true;
  clearInterval(tickTimer);
  cutChunk(false);
  if (mediaStream) mediaStream.getTracks().forEach((t) => t.stop());
  if (audioContext) audioContext.close();
  mediaStream = null;
  audioContext = null;
  releaseWakeLock();

  setStatus('最後の言葉を文字にしています…');
  // MediaRecorder の onstop が発火して送信が始まるのを少し待ってから、残りを待つ
  await new Promise((r) => setTimeout(r, 300));
  await waitPending();
  setStatus('記録を終えました');
  startBtn.disabled = false;
  startBtn.textContent = '● 新しく話しはじめる';
  recipeBtn.disabled = false;
  if (session.segments.length) makeRecipe();
  else recipeStatusEl.textContent = '聞き取れた言葉がありませんでした';
}

async function requestWakeLock() {
  try {
    if ('wakeLock' in navigator) wakeLock = await navigator.wakeLock.request('screen');
  } catch (_) {
    /* 非対応・拒否時はそのまま */
  }
}

function releaseWakeLock() {
  if (wakeLock) wakeLock.release().catch(() => {});
  wakeLock = null;
}

// ---- 声で答える (質問への回答用) ----

async function recordAnswer(button, input) {
  if (button.recorder) {
    button.recorder.stop();
    return;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (_) {
    button.textContent = 'マイク不可';
    return;
  }
  const mimeType = pickMimeType();
  const rec = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  const parts = [];
  rec.ondataavailable = (e) => e.data.size && parts.push(e.data);
  rec.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    button.recorder = null;
    button.textContent = '…';
    try {
      const text = await transcribe(new Blob(parts, { type: rec.mimeType || 'audio/webm' }));
      input.value = [input.value, text].filter(Boolean).join(' ');
    } catch (err) {
      input.placeholder = `聞き取れませんでした: ${err.message}`;
    }
    button.textContent = '🎤 声で';
  };
  rec.start();
  button.recorder = rec;
  button.textContent = '■ 止める';
}

// ---- レシピ ----

function resetRecipe() {
  currentRecipe = null;
  recipeBtn.disabled = true;
  recipeStatusEl.textContent = '作り終わったら、ここにレシピがまとまります';
  recipeViewEl.replaceChildren();
}

async function runRecipeAction(label, action) {
  recipeStatusEl.textContent = label;
  recipeBtn.disabled = true;
  recipeViewEl.querySelectorAll('button, input').forEach((e) => {
    e.disabled = true;
  });
  try {
    currentRecipe = await action();
    session.recipeId = currentRecipe.id;
    saveSession();
    renderRecipe(currentRecipe);
  } catch (err) {
    recipeStatusEl.textContent = `エラー: ${err.message}`;
    recipeViewEl.querySelectorAll('button, input').forEach((e) => {
      e.disabled = false;
    });
  } finally {
    recipeBtn.disabled = false;
  }
}

function makeRecipe() {
  runRecipeAction('レシピにまとめています…(30秒〜1分ほどかかります)', () =>
    api('/api/recipes', { startedAt: session.startedAt, segments: session.segments })
  );
}

const CONFIDENCE_LABEL = { high: '', medium: '推定', low: '要確認' };

function renderRecipe(doc) {
  const r = doc.recipe;
  recipeViewEl.replaceChildren();
  recipeBtn.textContent = 'まとめ直す';
  if (doc.finalizedAt) {
    recipeStatusEl.textContent = 'レシピが完成しました。レシピ一覧から見られます';
  } else if (r.questions.length) {
    recipeStatusEl.textContent = `確認したいことが ${r.questions.length} つあります`;
  } else {
    recipeStatusEl.textContent = '確認することはありません。よければ「これで完成」を押してください';
  }

  if (!doc.finalizedAt && r.questions.length) {
    const box = el('div', 'questions');
    const inputs = [];
    for (const q of r.questions) {
      const item = el('div', 'question');
      item.append(el('p', 'question-text', q.question));
      const input = el('input', 'question-input');
      input.type = 'text';
      input.placeholder = q.suggestion ? `例: ${q.suggestion}` : '答えを入力';
      const row = el('div', 'question-row');
      row.append(input);
      const actions = el('div', 'question-actions');
      if (q.suggestion) {
        const ok = el('button', 'btn btn-small', `「${q.suggestion}」でOK`);
        ok.addEventListener('click', () => {
          input.value = `はい、${q.suggestion}で合っています`;
        });
        actions.append(ok);
      }
      const voice = el('button', 'btn btn-small', '🎤 声で');
      voice.addEventListener('click', () => recordAnswer(voice, input));
      actions.append(voice);
      item.append(row, actions);
      box.append(item);
      inputs.push({ q, input });
    }
    const send = el('button', 'btn btn-secondary', '答えを送る');
    send.addEventListener('click', () => {
      const answers = inputs
        .map(({ q, input }) => ({ question: q.question, answer: input.value }))
        .filter((a) => a.answer.trim());
      if (!answers.length) return;
      runRecipeAction('答えをレシピに反映しています…', () =>
        api(`/api/recipes/${doc.id}/answers`, { answers })
      );
    });
    box.append(send);
    recipeViewEl.append(box);
  }

  const card = el('div', 'recipe-card');
  card.append(el('h3', 'recipe-title', r.title));
  if (r.servings) card.append(el('p', 'muted', r.servings));
  if (r.summary) card.append(el('p', 'muted', r.summary));

  card.append(el('h4', null, '材料'));
  const ul = el('ul', 'ingredients');
  for (const ing of r.ingredients) {
    const li = el('li');
    li.append(el('span', 'ing-name', ing.name));
    if (ing.original) li.append(el('span', 'ing-original', `「${ing.original}」`));
    if (ing.estimate) li.append(el('span', null, ` ${ing.estimate}`));
    const label = CONFIDENCE_LABEL[ing.confidence];
    if (label) li.append(el('span', `badge badge-${ing.confidence}`, label));
    if (ing.note) li.append(el('span', 'sub', ing.note));
    ul.append(li);
  }
  card.append(ul);

  card.append(el('h4', null, '作り方'));
  const ol = el('ol', 'steps');
  for (const st of r.steps) {
    const li = el('li');
    li.append(el('span', null, st.text));
    const meta = [st.heat, st.time].filter(Boolean).join(' / ');
    if (meta) li.append(el('span', 'sub', meta));
    if (st.tip) li.append(el('span', 'sub', `コツ: ${st.tip}`));
    ol.append(li);
  }
  card.append(ol);

  if (r.tips.length) {
    card.append(el('h4', null, 'コツ・隠し味'));
    const tl = el('ul', 'tips');
    for (const t of r.tips) tl.append(el('li', null, t));
    card.append(tl);
  }
  recipeViewEl.append(card);

  if (!doc.finalizedAt) {
    const fin = el('button', 'btn btn-start btn-wide', 'これで完成');
    fin.addEventListener('click', () =>
      runRecipeAction('保存しています…', () => api(`/api/recipes/${doc.id}/finalize`, {}))
    );
    recipeViewEl.append(fin);
  } else {
    const link = el('a', 'btn btn-secondary btn-wide', 'レシピ一覧で見る');
    link.href = `book/?id=${encodeURIComponent(doc.id)}`;
    recipeViewEl.append(link);
  }
}

// ---- お試し用の台本 ----
// [[...]] は感覚的な分量の言葉 (画面で色を付ける)。pause は読み上げの合間に黙る秒数の目安。
// 何人分か・フライパンの大きさ・焼き時間はわざと言わず、LLM が質問してくるようにしてある。
// voice は「声を出さずに試す」ときに使う、読み上げたと仮定した声の特徴 (ProsodyMeter#summary と同じ形)。
const v = (speechSec, meanDb, peakDb, sustainSec) => ({ speechSec, meanDb, peakDb, sustainSec });
const SAMPLE_SCRIPT = [
  { say: '今日は卵焼きを作ります。', voice: v(1.6, -30, -22, 0.2) },
  { say: '卵を3つ、ボウルに割って、', voice: v(1.7, -31, -23, 0.3) },
  { say: '砂糖をスプーンに[[こんもり]]入れます。', voice: v(2.6, -26, -12, 0.4) },
  { say: 'お醤油を[[ちょろっと]]、', voice: v(1.0, -36, -29, 0.2) },
  { say: 'お塩も[[ぱらぱらっと]]入れて、よーく混ぜます。', voice: v(2.4, -30, -21, 0.3) },
  { note: 'フライパンを温めるつもりで、15秒ほど黙る', pause: 15 },
  { say: 'フライパンに油を[[さーーーっと]]ひいて、', voice: v(3.0, -29, -21, 1.4) },
  { say: '卵を[[おたまに1杯ぐらい]]流して、火は弱めでね。', voice: v(2.9, -30, -22, 0.3) },
  { say: '端っこが[[ぷくぷく]]してきたら、くるくる巻きます。', voice: v(3.2, -31, -23, 0.3) },
  { say: 'これを3回くり返したら、できあがり。', voice: v(2.2, -30, -22, 0.2) },
];

function renderSampleScript() {
  for (const line of SAMPLE_SCRIPT) {
    const li = el('li', line.note ? 'sample-note' : null);
    if (line.note) {
      li.textContent = `(${line.note})`;
    } else {
      line.say.split(/(\[\[.+?\]\])/).forEach((part) => {
        const m = part.match(/^\[\[(.+)\]\]$/);
        li.append(m ? el('mark', null, m[1]) : document.createTextNode(part));
      });
    }
    sampleScriptEl.append(li);
  }
}

// 声を出せない場所でも試せるように、台本を「読み上げた」ことにしてレシピ化まで進める
function useSampleScript() {
  if (recorder) return;
  const t0 = Date.now() - 3 * 60 * 1000;
  let t = t0;
  const segments = [];
  for (const line of SAMPLE_SCRIPT) {
    // 黙る箇所は 2 分空いたことにして、LLM に「無音」の区切りとして渡す
    t += (line.note ? 120 : 5) * 1000;
    if (line.say) {
      segments.push({ text: line.say.replace(/\[\[|\]\]/g, ''), at: new Date(t).toISOString(), voice: line.voice });
    }
  }
  session = { startedAt: new Date(t0).toISOString(), segments, recipeId: null };
  saveSession();
  resetRecipe();
  renderTranscript();
  recipeBtn.disabled = false;
  setStatus('台本を読み込みました');
  makeRecipe();
}

renderSampleScript();
sampleBtn.addEventListener('click', useSampleScript);

// ---- 家族からの質問 (息子がレシピ一覧から聞いて、記録になかったもの) ----

async function loadFamilyQuestions() {
  let docs;
  try {
    docs = await api('/api/recipes?all=1');
  } catch (_) {
    return;
  }
  familyListEl.replaceChildren();
  let count = 0;
  for (const doc of docs) {
    for (const q of doc.familyQuestions || []) {
      if (!q.needsMom || q.momAnswer) continue;
      count++;
      const item = el('div', 'question');
      item.append(el('p', 'muted', `「${doc.recipe.title}」について`));
      item.append(el('p', 'question-text', q.question));
      const input = el('input', 'question-input');
      input.type = 'text';
      input.placeholder = '答えを入力';
      const actions = el('div', 'question-actions');
      const voice = el('button', 'btn btn-small', '🎤 声で');
      voice.addEventListener('click', () => recordAnswer(voice, input));
      const send = el('button', 'btn btn-secondary btn-small-send', '答える');
      send.addEventListener('click', async () => {
        if (!input.value.trim()) return;
        send.disabled = true;
        send.textContent = '送っています…';
        try {
          await api(`/api/recipes/${doc.id}/reply`, { questionId: q.id, answer: input.value });
          item.replaceChildren(el('p', 'muted', `「${q.question}」に答えました。レシピにも反映しました`));
        } catch (err) {
          send.disabled = false;
          send.textContent = `もう一度 (${err.message})`;
        }
      });
      actions.append(voice, send);
      item.append(input, actions);
      familyListEl.append(item);
    }
  }
  familyPanelEl.hidden = count === 0;
}

// ---- 起動 ----

startBtn.addEventListener('click', start);
stopBtn.addEventListener('click', stop);
recipeBtn.addEventListener('click', () => {
  if (session && session.segments.length) makeRecipe();
});

if (!navigator.mediaDevices || !window.MediaRecorder) {
  startBtn.disabled = true;
  setStatus('このブラウザは録音に対応していません (Safari / Chrome の最新版で開いてください)');
}

loadFamilyQuestions();

// 再読み込みしても、直前の記録とレシピを復元する
(async () => {
  const saved = loadSession();
  if (!saved || !saved.segments) return;
  session = saved;
  renderTranscript();
  if (session.segments.length) recipeBtn.disabled = false;
  if (session.recipeId) {
    try {
      currentRecipe = await api(`/api/recipes/${session.recipeId}`);
      renderRecipe(currentRecipe);
    } catch (_) {
      /* 見つからなければそのまま */
    }
  }
})();
