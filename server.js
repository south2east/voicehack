'use strict';

require('dotenv').config();

const path = require('path');
const fs = require('fs');
const http = require('http');
const crypto = require('crypto');
const express = require('express');
const { WebSocketServer } = require('ws');
const speech = require('@google-cloud/speech').v2;

const PORT = process.env.PORT || 3000;
const PROJECT_ID = process.env.GOOGLE_CLOUD_PROJECT;
const LOCATION = process.env.GOOGLE_CLOUD_LOCATION || 'us-central1';
const MODEL = process.env.SPEECH_MODEL || 'chirp_3';
const DEFAULT_LANGUAGE = process.env.SPEECH_LANGUAGE || 'ja-JP';
const TRANSCRIPTS_DIR = path.join(__dirname, 'transcripts');

if (!fs.existsSync(TRANSCRIPTS_DIR)) {
  fs.mkdirSync(TRANSCRIPTS_DIR, { recursive: true });
}

if (!PROJECT_ID) {
  console.warn(
    '[warn] GOOGLE_CLOUD_PROJECT が .env に設定されていません。Speech-to-Text の呼び出しは失敗します。'
  );
}

const app = express();
app.use(express.static(path.join(__dirname, 'public')));

const server = http.createServer(app);
const wss = new WebSocketServer({ server, path: '/ws' });

// Google の streamingRecognize は 1 ストリームあたり最大 ~5分の制約があるため、
// 上限に達する前にストリームを作り直して継続させる
const STREAM_RESTART_MS = 4 * 60 * 1000;

function buildRecognizerPath() {
  return `projects/${PROJECT_ID}/locations/${LOCATION}/recognizers/_`;
}

function buildStreamingConfig(sampleRateHertz, languageCode) {
  return {
    config: {
      explicitDecodingConfig: {
        encoding: 'LINEAR16',
        sampleRateHertz,
        audioChannelCount: 1,
      },
      model: MODEL,
      languageCodes: [languageCode || DEFAULT_LANGUAGE],
      features: {
        enableAutomaticPunctuation: true,
      },
    },
    streamingFeatures: {
      interimResults: true,
    },
  };
}

class TranscriptionSession {
  constructor(ws, { sampleRateHertz, languageCode }) {
    this.ws = ws;
    this.sampleRateHertz = sampleRateHertz;
    this.languageCode = languageCode || DEFAULT_LANGUAGE;
    this.id = crypto.randomUUID();
    this.startedAt = new Date().toISOString();
    this.endedAt = null;
    this.segments = []; // { text, isFinal, confidence, receivedAt }
    this.client = new speech.SpeechClient(
      LOCATION === 'global' ? {} : { apiEndpoint: `${LOCATION}-speech.googleapis.com` }
    );
    this.geminiStream = null;
    this.restartTimer = null;
    this.closed = false;
    this.pendingAudio = [];
  }

  start() {
    this._openStream();
  }

  _openStream() {
    if (this.closed) return;

    const recognizeStream = this.client._streamingRecognize();
    this.geminiStream = recognizeStream;

    recognizeStream.on('error', (err) => {
      console.error(`[session ${this.id}] streaming error:`, err.message);
      this._safeSend({ type: 'error', message: err.message });
    });

    recognizeStream.on('data', (response) => {
      this._handleResponse(response);
    });

    recognizeStream.on('end', () => {
      // ストリーム終了。closed でなければ再接続を試みる想定だが、
      // 通常は _restartStream / stop 経由で明示的にハンドルされる
    });

    recognizeStream.write({
      recognizer: buildRecognizerPath(),
      streamingConfig: buildStreamingConfig(this.sampleRateHertz, this.languageCode),
    });

    this.restartTimer = setTimeout(() => this._restartStream(), STREAM_RESTART_MS);
  }

  _restartStream() {
    if (this.closed) return;
    const old = this.geminiStream;
    this.geminiStream = null;
    this._openStream();
    if (old) {
      try {
        old.end();
      } catch (_) {
        /* noop */
      }
    }
  }

  _handleResponse(response) {
    const results = response.results || [];
    for (const result of results) {
      const alt = result.alternatives && result.alternatives[0];
      if (!alt) continue;
      const transcript = alt.transcript || '';
      const isFinal = !!result.isFinal;
      const payload = {
        type: isFinal ? 'final' : 'partial',
        transcript,
        confidence: alt.confidence ?? null,
      };
      if (isFinal) {
        this.segments.push({
          text: transcript,
          confidence: alt.confidence ?? null,
          receivedAt: new Date().toISOString(),
        });
      }
      this._safeSend(payload);
    }
  }

  writeAudio(chunk) {
    if (this.closed || !this.geminiStream) return;
    try {
      this.geminiStream.write({ audio: chunk });
    } catch (err) {
      console.error(`[session ${this.id}] write error:`, err.message);
    }
  }

  _safeSend(obj) {
    if (this.ws.readyState === this.ws.OPEN) {
      this.ws.send(JSON.stringify(obj));
    }
  }

  finish() {
    if (this.closed) return this.toJSON();
    this.closed = true;
    if (this.restartTimer) clearTimeout(this.restartTimer);
    if (this.geminiStream) {
      try {
        this.geminiStream.end();
      } catch (_) {
        /* noop */
      }
    }
    this.endedAt = new Date().toISOString();
    const record = this.toJSON();
    this._persist(record);
    return record;
  }

  toJSON() {
    return {
      sessionId: this.id,
      languageCode: this.languageCode,
      model: MODEL,
      startedAt: this.startedAt,
      endedAt: this.endedAt,
      segments: this.segments,
      fullText: this.segments.map((s) => s.text).join(''),
    };
  }

  _persist(record) {
    const filename = `${this.startedAt.replace(/[:.]/g, '-')}_${this.id}.json`;
    const filePath = path.join(TRANSCRIPTS_DIR, filename);
    fs.writeFile(filePath, JSON.stringify(record, null, 2), (err) => {
      if (err) {
        console.error(`[session ${this.id}] failed to persist transcript:`, err.message);
      } else {
        console.log(`[session ${this.id}] transcript saved -> ${filePath}`);
      }
    });
  }
}

wss.on('connection', (ws) => {
  let session = null;

  ws.on('message', (data, isBinary) => {
    if (isBinary) {
      if (session) session.writeAudio(data);
      return;
    }

    let msg;
    try {
      msg = JSON.parse(data.toString());
    } catch (_) {
      return;
    }

    if (msg.type === 'start') {
      if (session) session.finish();
      session = new TranscriptionSession(ws, {
        sampleRateHertz: msg.sampleRate || 48000,
        languageCode: msg.languageCode,
      });
      session.start();
      ws.send(JSON.stringify({ type: 'started', sessionId: session.id }));
    } else if (msg.type === 'stop') {
      if (session) {
        const record = session.finish();
        ws.send(JSON.stringify({ type: 'stopped', session: record }));
        session = null;
      }
    }
  });

  ws.on('close', () => {
    if (session) session.finish();
    session = null;
  });
});

server.listen(PORT, () => {
  console.log(`voicehack server listening on http://localhost:${PORT}`);
});
