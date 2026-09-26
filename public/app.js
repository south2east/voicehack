'use strict';

const startBtn = document.getElementById('startBtn');
const stopBtn = document.getElementById('stopBtn');
const statusEl = document.getElementById('status');
const finalTextEl = document.getElementById('finalText');
const partialTextEl = document.getElementById('partialText');
const textOutputEl = document.getElementById('textOutput');
const copyBtn = document.getElementById('copyBtn');
const downloadBtn = document.getElementById('downloadBtn');
const audioDownloadEl = document.getElementById('audioDownload');
const analysisStatusEl = document.getElementById('analysisStatus');
const analysisMetricsEl = document.getElementById('analysisMetrics');
const analysisImagesEl = document.getElementById('analysisImages');

const recipeBtn = document.getElementById('recipeBtn');
const recipeStatusEl = document.getElementById('recipeStatus');
const recipeViewEl = document.getElementById('recipeView');

const ANALYSIS_POLL_MS = 2000;

let ws = null;
let audioContext = null;
let sourceNode = null;
let processorNode = null;
let mediaStream = null;
let lastSession = null;
let lastText = '';
let analysisTimer = null;
let currentRecipe = null;
let wakeLock = null;

function setStatus(text, recording) {
  statusEl.textContent = text;
  statusEl.classList.toggle('recording', !!recording);
}

// Float32 [-1, 1] のPCMをLINEAR16 (Int16)に変換
function floatTo16BitPCM(float32Array) {
  const buffer = new ArrayBuffer(float32Array.length * 2);
  const view = new DataView(buffer);
  let offset = 0;
  for (let i = 0; i < float32Array.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, float32Array[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buffer;
}

async function start() {
  startBtn.disabled = true;
  setStatus('マイクにアクセス中…');

  try {
    // ブラウザ既定のノイズ抑制・自動音量調整・エコー除去は認識精度を下げる
    // (音が歪む / AGC で音割れする) ので切り、マイクの生音をそのまま送る
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false,
      },
    });
  } catch (err) {
    setStatus('マイクへのアクセスが拒否されました');
    startBtn.disabled = false;
    return;
  }

  audioContext = new (window.AudioContext || window.webkitAudioContext)();
  const sampleRate = audioContext.sampleRate;

  const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${protocol}://${location.host}/ws`);
  ws.binaryType = 'arraybuffer';

  ws.onopen = () => {
    ws.send(
      JSON.stringify({
        type: 'start',
        sampleRate,
        languageCode: 'ja-JP',
      })
    );
  };

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    handleServerMessage(msg);
  };

  ws.onerror = () => {
    setStatus('接続エラー');
  };

  ws.onclose = () => {
    if (statusEl.textContent !== '停止しました') {
      setStatus('接続が切断されました');
    }
  };

  sourceNode = audioContext.createMediaStreamSource(mediaStream);
  // ScriptProcessorNode は非推奨だが、シンプルさのためにここでは使用
  processorNode = audioContext.createScriptProcessor(4096, 1, 1);

  processorNode.onaudioprocess = (event) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const input = event.inputBuffer.getChannelData(0);
    const pcm16 = floatTo16BitPCM(input);
    ws.send(pcm16);
  };

  sourceNode.connect(processorNode);
  processorNode.connect(audioContext.destination);

  finalTextEl.textContent = '';
  partialTextEl.textContent = '';
  textOutputEl.textContent = '記録中…';
  copyBtn.disabled = true;
  downloadBtn.disabled = true;
  audioDownloadEl.classList.add('is-disabled');
  audioDownloadEl.removeAttribute('download');
  audioDownloadEl.href = '#';
  resetAnalysis();
  resetRecipe();
  requestWakeLock();

  setStatus('録音中… (話しかけてください)', true);
  stopBtn.disabled = false;
}

function stop() {
  stopBtn.disabled = true;
  setStatus('停止処理中…');

  if (processorNode) {
    processorNode.disconnect();
    processorNode.onaudioprocess = null;
    processorNode = null;
  }
  if (sourceNode) {
    sourceNode.disconnect();
    sourceNode = null;
  }
  if (mediaStream) {
    mediaStream.getTracks().forEach((track) => track.stop());
    mediaStream = null;
  }
  if (audioContext) {
    audioContext.close();
    audioContext = null;
  }

  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'stop' }));
  } else {
    finishUI();
  }
}

function handleServerMessage(msg) {
  switch (msg.type) {
    case 'started':
      break;
    case 'partial':
      partialTextEl.textContent = msg.transcript;
      break;
    case 'final':
      partialTextEl.textContent = '';
      finalTextEl.textContent += msg.transcript;
      break;
    case 'stopped':
      showSession(msg.session, msg.text);
      copyBtn.disabled = false;
      downloadBtn.disabled = false;
      recipeBtn.disabled = false;
      if (lastSession.audioUrl) {
        audioDownloadEl.href = lastSession.audioUrl;
        audioDownloadEl.download = `recording_${lastSession.sessionId}.mp4`;
        audioDownloadEl.classList.remove('is-disabled');
      }
      renderAnalysis(lastSession.analysis);
      if (lastSession.analysis && lastSession.analysis.status === 'running') {
        pollAnalysis(lastSession.sessionId);
      }
      finishUI();
      if (ws) ws.close();
      break;
    case 'error':
      setStatus(`エラー: ${msg.message}`);
      break;
    default:
      break;
  }
}

function showSession(session, text) {
  lastSession = session;
  lastText = text;
  textOutputEl.textContent = text;
}

// 解析はサーバー側で録音終了後に走るので、終わるまで定期的に問い合わせる
function pollAnalysis(sessionId) {
  clearTimeout(analysisTimer);
  analysisTimer = setTimeout(async () => {
    try {
      const res = await fetch(`/api/sessions/${sessionId}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const { session, text } = await res.json();
      if (!lastSession || lastSession.sessionId !== sessionId) return;
      showSession(session, text);
      renderAnalysis(session.analysis);
      if (session.analysis && session.analysis.status === 'running') pollAnalysis(sessionId);
    } catch (err) {
      analysisStatusEl.textContent = `解析状況を取得できません: ${err.message}`;
    }
  }, ANALYSIS_POLL_MS);
}

function resetAnalysis() {
  clearTimeout(analysisTimer);
  analysisStatusEl.textContent = '終了すると録音を解析します';
  analysisMetricsEl.replaceChildren();
  analysisImagesEl.replaceChildren();
}

function fmt(v, digits = 1, unit = '') {
  return v === null || v === undefined ? '—' : `${Number(v).toFixed(digits)}${unit}`;
}

function renderAnalysis(analysis) {
  analysisMetricsEl.replaceChildren();
  analysisImagesEl.replaceChildren();
  if (!analysis) return;

  switch (analysis.status) {
    case 'running':
      analysisStatusEl.textContent = '解析中… (録音の長さと同じくらいかかります)';
      return;
    case 'failed':
      analysisStatusEl.textContent = `解析に失敗しました: ${analysis.error}`;
      return;
    case 'skipped':
      analysisStatusEl.textContent = `解析をスキップしました: ${analysis.error}`;
      return;
    case 'disabled':
      analysisStatusEl.textContent = '解析は無効です (ANALYSIS_ENABLED=false)';
      return;
    case 'done':
      break;
    default:
      return;
  }

  const r = analysis.report;
  analysisStatusEl.textContent = `解析完了 (${fmt(r.file.duration_s, 1, ' 秒')})`;
  const p = r.pitch;
  const rows = [
    ['大きさ', `${fmt(r.loudness.integrated_lufs, 1, ' LUFS')} / ピーク ${fmt(r.level.peak_dbfs, 1, ' dBFS')}`],
    ['トーン', p.median_f0_hz
      ? `F0 中央値 ${fmt(p.median_f0_hz, 0, ' Hz')} (${p.note}) / 抑揚 ${fmt(p.f0_range_semitones, 1, ' 半音')}`
      : '有声区間なし'],
    ['スピード', `発話速度 ${fmt(r.rate.speech_rate_syll_per_s, 2, ' 音節/s')} / 調音速度 ${fmt(r.rate.articulation_rate_syll_per_s, 2, ' 音節/s')} / ポーズ ${r.rate.n_pauses} 回`],
  ];
  if (r.separation) {
    rows.push(['環境音', `${fmt(r.separation.environment.integrated_lufs, 1, ' LUFS')} (RMS ${fmt(r.separation.environment.rms_dbfs, 1, ' dBFS')})`]);
  }
  for (const [label, value] of rows) {
    const dt = document.createElement('dt');
    dt.textContent = label;
    const dd = document.createElement('dd');
    dd.textContent = value;
    analysisMetricsEl.append(dt, dd);
  }

  for (const src of analysis.images || []) {
    const a = document.createElement('a');
    a.href = src;
    a.target = '_blank';
    const img = document.createElement('img');
    img.src = src;
    img.alt = src.split('/').pop();
    img.loading = 'lazy';
    a.append(img);
    analysisImagesEl.append(a);
  }
  const link = document.createElement('a');
  link.href = analysis.reportUrl;
  link.target = '_blank';
  link.textContent = 'report.json を開く';
  analysisImagesEl.append(link);
}

// 調理中に画面がスリープするとマイクが止まるので、録音中はスリープさせない
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

// ---- レシピ ----

function el(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}

function resetRecipe() {
  currentRecipe = null;
  recipeBtn.disabled = true;
  recipeStatusEl.textContent = '話し終えて「終了」を押すと、レシピにまとめられます';
  recipeViewEl.replaceChildren();
}

async function postJson(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function runRecipeAction(label, action) {
  recipeStatusEl.textContent = label;
  recipeViewEl.querySelectorAll('button, input').forEach((e) => {
    e.disabled = true;
  });
  try {
    currentRecipe = await action();
    renderRecipe(currentRecipe);
  } catch (err) {
    recipeStatusEl.textContent = `エラー: ${err.message}`;
    recipeViewEl.querySelectorAll('button, input').forEach((e) => {
      e.disabled = false;
    });
  }
}

const CONFIDENCE_LABEL = { high: '', medium: '推定', low: '要確認' };

function renderRecipe(doc) {
  const r = doc.recipe;
  recipeViewEl.replaceChildren();
  if (doc.finalizedAt) {
    recipeStatusEl.textContent = 'レシピを確定しました';
  } else if (r.questions.length) {
    recipeStatusEl.textContent = `確認したいことが ${r.questions.length} つあります`;
  } else {
    recipeStatusEl.textContent = '確認することはありません。よければ「これで完成」を押してください';
  }

  // 質問 (上に出して、すぐ答えられるように)
  if (!doc.finalizedAt && r.questions.length) {
    const qBox = el('div', 'questions');
    const inputs = [];
    for (const q of r.questions) {
      const item = el('div', 'question');
      item.append(el('p', 'question-text', q.question));
      const row = el('div', 'question-row');
      const input = el('input', 'question-input');
      input.type = 'text';
      input.placeholder = q.suggestion ? `例: ${q.suggestion}` : '回答';
      row.append(input);
      if (q.suggestion) {
        const yes = el('button', 'btn btn-small', 'それでOK');
        yes.addEventListener('click', () => {
          input.value = `はい、${q.suggestion}で合っています`;
        });
        row.append(yes);
      }
      item.append(row);
      qBox.append(item);
      inputs.push({ q, input });
    }
    const send = el('button', 'btn btn-secondary', '回答を送る');
    send.addEventListener('click', () => {
      const answers = inputs
        .map(({ q, input }) => ({ question: q.question, answer: input.value }))
        .filter((a) => a.answer.trim());
      if (!answers.length) return;
      runRecipeAction('回答をレシピに反映しています…', () =>
        postJson(`/api/recipes/${doc.id}/answers`, { answers })
      );
    });
    qBox.append(send);
    recipeViewEl.append(qBox);
  }

  // レシピ本体
  const card = el('div', 'recipe-card');
  card.append(el('h3', 'recipe-title', r.title));
  if (r.servings) card.append(el('p', 'recipe-meta', `${r.servings}`));
  if (r.summary) card.append(el('p', 'recipe-summary', r.summary));

  card.append(el('h4', null, '材料'));
  const ul = el('ul', 'ingredients');
  for (const ing of r.ingredients) {
    const li = el('li');
    li.append(el('span', 'ing-name', ing.name));
    const amount = el('span', 'ing-amount');
    if (ing.original) amount.append(el('span', 'ing-original', `「${ing.original}」`));
    if (ing.estimate) amount.append(el('span', null, ` ${ing.estimate}`));
    const label = CONFIDENCE_LABEL[ing.confidence];
    if (label) amount.append(el('span', `badge badge-${ing.confidence}`, label));
    li.append(amount);
    if (ing.note) li.append(el('span', 'ing-note', ing.note));
    ul.append(li);
  }
  card.append(ul);

  card.append(el('h4', null, '作り方'));
  const ol = el('ol', 'steps');
  for (const st of r.steps) {
    const li = el('li');
    li.append(el('span', null, st.text));
    const meta = [st.heat, st.time].filter(Boolean).join(' / ');
    if (meta) li.append(el('span', 'step-meta', meta));
    if (st.tip) li.append(el('span', 'step-tip', `コツ: ${st.tip}`));
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
    const fin = el('button', 'btn btn-start', 'これで完成');
    fin.addEventListener('click', () =>
      runRecipeAction('確定しています…', () => postJson(`/api/recipes/${doc.id}/finalize`))
    );
    recipeViewEl.append(fin);
  }
}

recipeBtn.addEventListener('click', () => {
  if (!lastSession) return;
  recipeBtn.disabled = true;
  runRecipeAction('レシピにまとめています… (数十秒かかることがあります)', () =>
    postJson(`/api/sessions/${lastSession.sessionId}/recipe`)
  ).finally(() => {
    recipeBtn.disabled = !!currentRecipe;
  });
});

function finishUI() {
  releaseWakeLock();
  setStatus('停止しました', false);
  startBtn.disabled = false;
  stopBtn.disabled = true;
}

copyBtn.addEventListener('click', async () => {
  if (!lastText) return;
  try {
    await navigator.clipboard.writeText(lastText);
    copyBtn.textContent = 'コピーしました';
  } catch (_) {
    copyBtn.textContent = 'コピーできませんでした';
  }
  setTimeout(() => {
    copyBtn.textContent = 'テキストをコピー';
  }, 1500);
});

downloadBtn.addEventListener('click', () => {
  if (!lastSession) return;
  const blob = new Blob([lastText], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `transcript_${lastSession.sessionId}.txt`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
});

startBtn.addEventListener('click', start);
stopBtn.addEventListener('click', stop);
