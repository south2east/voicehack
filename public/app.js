'use strict';

const startBtn = document.getElementById('startBtn');
const stopBtn = document.getElementById('stopBtn');
const statusEl = document.getElementById('status');
const finalTextEl = document.getElementById('finalText');
const partialTextEl = document.getElementById('partialText');
const jsonOutputEl = document.getElementById('jsonOutput');
const downloadBtn = document.getElementById('downloadBtn');

let ws = null;
let audioContext = null;
let sourceNode = null;
let processorNode = null;
let mediaStream = null;
let lastSession = null;

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
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
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
  jsonOutputEl.textContent = '記録中…';
  downloadBtn.disabled = true;

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
      lastSession = msg.session;
      jsonOutputEl.textContent = JSON.stringify(lastSession, null, 2);
      downloadBtn.disabled = false;
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

function finishUI() {
  setStatus('停止しました', false);
  startBtn.disabled = false;
  stopBtn.disabled = true;
}

downloadBtn.addEventListener('click', () => {
  if (!lastSession) return;
  const blob = new Blob([JSON.stringify(lastSession, null, 2)], {
    type: 'application/json',
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `transcript_${lastSession.sessionId}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
});

startBtn.addEventListener('click', start);
stopBtn.addEventListener('click', stop);
