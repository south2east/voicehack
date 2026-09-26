'use strict';

// ブラウザが発話ごとに区切って送ってくる音声 (webm / mp4) を文字起こしする
const OpenAI = require('openai');
const { toFile } = require('openai');
const { handle, readRaw } = require('../lib/http');

const MODEL = process.env.OPENAI_TRANSCRIBE_MODEL || 'gpt-4o-transcribe';
// 文脈を伝えると料理用語が正しく出やすく、擬音も正規化されにくくなる
const PROMPT =
  'お母さんが料理をしながらレシピを話しています。材料名・調味料・調理用語が多く出てきます。' +
  '「さーっと」「ひとまわし」「ちょろっと」などの擬音や分量の言い方は、伸ばし棒も含めて聞こえた通りに書いてください。';

let client = null;

module.exports = handle({
  async POST(req) {
    const audio = await readRaw(req);
    if (!audio.length) throw Object.assign(new Error('empty audio'), { status: 400 });
    const type = (req.headers['content-type'] || 'audio/webm').split(';')[0];
    const ext = type.includes('mp4') ? 'mp4' : type.includes('ogg') ? 'ogg' : 'webm';
    client = client || new OpenAI();
    const result = await client.audio.transcriptions.create({
      model: MODEL,
      file: await toFile(audio, `chunk.${ext}`, { type }),
      language: 'ja',
      prompt: PROMPT,
    });
    return { text: result.text || '' };
  },
});
