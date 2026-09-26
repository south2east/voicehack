'use strict';

// レシピ化の中身 (手元のサーバー server.js と Vercel の api/ で共通)。
// 文字起こしから LLM (OpenAI API) でレシピを組み立て、足りない情報を質問として返す。
// システムプロンプトは web/prompts/recipe_system.md (チューニングはこのファイルを編集する)。

const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const OpenAI = require('openai');
const { describeVoice } = require('../public/prosody');

const MODEL = process.env.OPENAI_MODEL || 'gpt-6-luna';
const PROMPT_PATH = path.join(__dirname, '..', 'prompts', 'recipe_system.md');
// これ以上話していない時間があれば、LLM に「無音」として伝える (工程の区切り・加熱時間の手がかり)
const GAP_MARK_SEC = 60;

const nullable = (type) => ({ type: [type, 'null'] });

const RECIPE_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['title', 'servings', 'summary', 'ingredients', 'steps', 'tips', 'questions', 'status'],
  properties: {
    title: { type: 'string', description: '料理名' },
    servings: { ...nullable('string'), description: '何人分か。不明なら null' },
    summary: { type: 'string', description: 'どんな料理か 1〜2 文' },
    ingredients: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['name', 'original', 'estimate', 'confidence', 'note'],
        properties: {
          name: { type: 'string' },
          original: { ...nullable('string'), description: 'お母さんが言った分量の言葉そのまま' },
          estimate: { ...nullable('string'), description: '計量できる量の推定 (大さじ1, 200g など)' },
          confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
          note: { ...nullable('string'), description: '切り方・下ごしらえなど' },
        },
      },
    },
    steps: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['text', 'heat', 'time', 'tip'],
        properties: {
          text: { type: 'string' },
          heat: { ...nullable('string'), description: '火加減' },
          time: { ...nullable('string'), description: '時間の目安' },
          tip: { ...nullable('string'), description: 'この工程のコツ' },
        },
      },
    },
    tips: { type: 'array', items: { type: 'string' }, description: '全体のコツ・隠し味' },
    questions: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['question', 'about', 'suggestion'],
        properties: {
          question: { type: 'string', description: 'お母さんへの短い質問' },
          about: { type: 'string', description: '何についての質問か (例: 材料:油)' },
          suggestion: { ...nullable('string'), description: '推定の答えを短い名詞句で (例: 大さじ1くらい / 2人分)。疑問文にしない' },
        },
      },
    },
    status: { type: 'string', enum: ['needs_answers', 'complete'] },
  },
};

let client = null;
function getClient(apiKey) {
  const key = apiKey || process.env.OPENAI_API_KEY;
  if (!key) throw new Error('OPENAI_API_KEY が設定されていません');
  if (!client || client.apiKey !== key) client = new OpenAI({ apiKey: key });
  return client;
}

function mmss(sec) {
  const s = Math.max(0, Math.round(sec));
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

// 文字起こしを、経過時間・無音の区切り・声の出し方の注記付きのテキストにする。
// segments: [{ text, at (ISO), voice? (prosody.js の ProsodyMeter#summary) }]
function formatTranscript(startedAt, segments) {
  const t0 = Date.parse(startedAt);
  const lines = [];
  let prev = t0;
  const segs = [...segments]
    .filter((s) => s.text && s.text.trim())
    .sort((a, b) => a.at.localeCompare(b.at));
  const voices = describeVoice(segs);
  segs.forEach((seg, i) => {
    const t = Date.parse(seg.at);
    const gap = (t - prev) / 1000;
    if (gap >= GAP_MARK_SEC) lines.push(`(—— ${Math.round(gap / 60)}分 無音 ——)`);
    const tags = voices[i] && voices[i].tags.length ? `  〈声: ${voices[i].tags.join(' / ')}〉` : '';
    lines.push(`[${mmss((t - t0) / 1000)}] ${seg.text.trim()}${tags}`);
    prev = t;
  });
  return lines.join('\n') || '(認識されたテキストはありません)';
}

function buildInput(doc, dictionary) {
  const parts = [];
  const dict = formatDictionary(dictionary);
  if (dict) parts.push(dict);
  parts.push(`# 文字起こし\n\n${doc.transcript}`);
  if (doc.recipe) {
    parts.push(`# 現在のレシピ\n\n${JSON.stringify(doc.recipe, null, 2)}`);
  }
  const answered = doc.history.flatMap((h) => h.answers).filter((a) => a.answer.trim());
  if (answered.length) {
    parts.push(
      '# 質問への回答\n\n' +
        answered.map((a) => `- Q: ${a.question}\n  A: ${a.answer.trim()}`).join('\n')
    );
  }
  return parts.join('\n\n');
}

async function callModel(doc, apiKey, dictionary) {
  const instructions = fs.readFileSync(PROMPT_PATH, 'utf8'); // 毎回読むので再起動なしでチューニングできる
  const res = await getClient(apiKey).responses.create({
    model: MODEL,
    instructions,
    input: buildInput(doc, dictionary),
    text: {
      format: { type: 'json_schema', name: 'recipe', schema: RECIPE_SCHEMA, strict: true },
    },
  });
  return JSON.parse(res.output_text);
}

// 文字起こしから最初のレシピ案と質問を作る (保存は呼び出し側)
async function create({ startedAt, segments, audioUrl = null }, apiKey, dictionary) {
  const doc = {
    id: crypto.randomUUID(),
    createdAt: new Date().toISOString(),
    startedAt,
    audioUrl,
    transcript: formatTranscript(startedAt, segments),
    recipe: null,
    history: [],
  };
  doc.recipe = await callModel(doc, apiKey, dictionary);
  doc.updatedAt = new Date().toISOString();
  return doc;
}

// 質問への回答を反映してレシピを更新する。answers: [{ question, answer }]
async function answer(doc, answers, apiKey, dictionary) {
  doc.history.push({ at: new Date().toISOString(), answers });
  doc.recipe = await callModel(doc, apiKey, dictionary);
  doc.updatedAt = new Date().toISOString();
  return doc;
}

// 質問が残っていても、お母さんが「これで完成」としたときに確定する
function finalize(doc) {
  doc.finalizedAt = new Date().toISOString();
  doc.updatedAt = doc.finalizedAt;
  return doc;
}

// ---- この家の分量の辞書 ----
// お母さんが確認・訂正した「感覚の言葉 → 実際の量」を貯めて、次のレシピから推定に使う。
// dictionary: { entries: [{ key, ingredient, expression, amount, sustainSec, recipeTitle, recipeId, confirmedAt, count }] }

// 伸ばし棒の数や声の注記の括弧を除いた、言葉の見出し (「さーーっと (3秒伸ばして)」→「さーっと」)
function normalizeExpression(original) {
  return original.replace(/[（(].*?[)）]/g, '').replace(/ー+/g, 'ー').replace(/[、。,.\s]/g, '').trim();
}

// 数字や計量単位で言ったもの (卵3つ・200g・大さじ1) は辞書に入れない
function isSensory(original) {
  const expr = original.replace(/[（(].*?[)）]/g, '');
  return !/[0-9０-９一二三四五六七八九十半]|大さじ|小さじ|カップ|グラム|cc|ml|g\b/i.test(expr);
}

// 確定したレシピから、確信度が高い感覚的な分量を辞書に取り込む
function learnDictionary(dictionary, doc) {
  const entries = [...((dictionary && dictionary.entries) || [])];
  for (const ing of doc.recipe.ingredients) {
    if (!ing.original || !ing.estimate || ing.confidence !== 'high' || !isSensory(ing.original)) continue;
    const expression = normalizeExpression(ing.original);
    if (!expression) continue;
    const key = `${ing.name}:${expression}`;
    const sustain = ing.original.match(/(\d+(?:\.\d+)?)\s*秒/);
    const entry = {
      key,
      ingredient: ing.name,
      expression,
      original: ing.original,
      amount: ing.estimate,
      sustainSec: sustain ? Number(sustain[1]) : null,
      recipeTitle: doc.recipe.title,
      recipeId: doc.id,
      confirmedAt: doc.finalizedAt,
    };
    const i = entries.findIndex((e) => e.key === key);
    if (i >= 0) {
      const sameRecipe = entries[i].recipeId === doc.id; // 同じレシピの確定し直しは数えない
      entries[i] = { ...entry, count: (entries[i].count || 1) + (sameRecipe ? 0 : 1) };
    }
    else entries.push({ ...entry, count: 1 });
  }
  return { entries: entries.slice(-200), updatedAt: new Date().toISOString() };
}

function formatDictionary(dictionary) {
  const entries = (dictionary && dictionary.entries) || [];
  if (!entries.length) return '';
  const lines = entries.map((e) => {
    const voice = e.sustainSec ? ` / 声を${e.sustainSec}秒伸ばしていた` : '';
    return `- ${e.ingredient}「${e.expression}」→ ${e.amount}  (「${e.recipeTitle}」で確認${voice}${e.count > 1 ? ` / ${e.count}回確認` : ''})`;
  });
  return `# この家の分量の辞書 (過去にお母さんが確認した量)\n\n${lines.join('\n')}`;
}

// ---- 家族 (息子) からの質問 ----

// 息子の質問をためる。答えるのはお母さん (replyFamily)
function askFamily(doc, question) {
  const item = {
    id: crypto.randomUUID(),
    question,
    askedAt: new Date().toISOString(),
    momAnswer: null,
    momAnsweredAt: null,
  };
  doc.familyQuestions = [...(doc.familyQuestions || []), item];
  doc.updatedAt = item.askedAt;
  return item;
}

// お母さんが家族の質問に答える。答えはレシピ本体にも反映する
async function replyFamily(doc, questionId, momAnswer, apiKey, dictionary) {
  const item = (doc.familyQuestions || []).find((q) => q.id === questionId);
  if (!item) throw Object.assign(new Error('question not found'), { status: 404 });
  item.momAnswer = momAnswer;
  item.momAnsweredAt = new Date().toISOString();
  await answer(doc, [{ question: `(息子からの質問) ${item.question}`, answer: momAnswer }], apiKey, dictionary);
  return doc;
}

// 息子用の閲覧ページに出す形 (文字起こしの原文や回答履歴は出さない)
function toPublic(doc) {
  const { questions, status, ...recipe } = doc.recipe;
  const familyQuestions = (doc.familyQuestions || []).map(
    ({ id, question, askedAt, momAnswer, momAnsweredAt }) => ({ id, question, askedAt, momAnswer, momAnsweredAt })
  );
  return { id: doc.id, createdAt: doc.createdAt, finalizedAt: doc.finalizedAt, ...recipe, familyQuestions };
}

const isValidId = (id) => typeof id === 'string' && /^[0-9a-f-]{36}$/.test(id);

module.exports = {
  create, answer, finalize, askFamily, replyFamily, toPublic, isValidId, formatTranscript,
  learnDictionary, formatDictionary, MODEL,
};
