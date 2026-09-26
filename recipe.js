'use strict';

// 手元のサーバー (server.js) 用のレシピ保存。レシピ化の中身は web/lib/recipe.js (Vercel 版と共通)。
// 保存先は recipes/<id>.json。

const path = require('path');
const fs = require('fs');
const dotenv = require('dotenv');
const core = require('./web/lib/recipe');

const RECIPES_DIR = path.join(__dirname, 'recipes');
if (!fs.existsSync(RECIPES_DIR)) fs.mkdirSync(RECIPES_DIR, { recursive: true });

// シェルに別の OPENAI_API_KEY が残っていても、.env に書いたキーを優先する
function apiKey() {
  let fromFile = '';
  try {
    fromFile = dotenv.parse(fs.readFileSync(path.join(__dirname, '.env'))).OPENAI_API_KEY || '';
  } catch (_) {
    /* .env なし */
  }
  return fromFile || process.env.OPENAI_API_KEY || '';
}

function recipePath(id) {
  if (!core.isValidId(id)) throw Object.assign(new Error('invalid recipe id'), { status: 400 });
  return path.join(RECIPES_DIR, `${id}.json`);
}

function save(doc) {
  fs.writeFileSync(recipePath(doc.id), JSON.stringify(doc, null, 2));
  return doc;
}

function load(id) {
  return JSON.parse(fs.readFileSync(recipePath(id), 'utf8'));
}

function list() {
  return fs
    .readdirSync(RECIPES_DIR)
    .filter((f) => f.endsWith('.json'))
    .map((f) => JSON.parse(fs.readFileSync(path.join(RECIPES_DIR, f), 'utf8')))
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

// session: TranscriptionSession#toJSON()
async function createFromSession(session) {
  const segments = session.segments.map((s) => ({ text: s.text, at: s.receivedAt }));
  const doc = await core.create(
    { startedAt: session.startedAt, segments, audioUrl: session.audioUrl },
    apiKey()
  );
  doc.sessionId = session.sessionId;
  return save(doc);
}

async function answer(id, answers) {
  return save(await core.answer(load(id), answers, apiKey()));
}

function finalize(id) {
  return save(core.finalize(load(id)));
}

module.exports = { createFromSession, answer, finalize, load, list, MODEL: core.MODEL };
