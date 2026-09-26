'use strict';

const recipe = require('../../../lib/recipe');
const store = require('../../../lib/store');
const { handle } = require('../../../lib/http');

module.exports = handle({
  // 息子からの質問。body: { question }
  async POST(req) {
    const question = ((req.body && req.body.question) || '').trim();
    if (!question) throw Object.assign(new Error('質問を入力してください'), { status: 400 });
    if (question.length > 500) throw Object.assign(new Error('質問が長すぎます'), { status: 400 });
    const doc = await store.load(req.query.id);
    if (!doc.finalizedAt) throw Object.assign(new Error('recipe not found'), { status: 404 });
    const item = await recipe.askFamily(doc, question);
    await store.save(doc);
    return item;
  },
});
