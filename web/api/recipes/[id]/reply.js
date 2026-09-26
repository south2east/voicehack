'use strict';

const recipe = require('../../../lib/recipe');
const store = require('../../../lib/store');
const { handle } = require('../../../lib/http');

module.exports = handle({
  // お母さんが家族の質問に答える。body: { questionId, answer }
  async POST(req) {
    const { questionId, answer } = req.body || {};
    if (!questionId || !answer || !answer.trim()) {
      throw Object.assign(new Error('questionId と answer が必要です'), { status: 400 });
    }
    const doc = await store.load(req.query.id);
    return store.save(await recipe.replyFamily(doc, questionId, answer.trim()));
  },
});
