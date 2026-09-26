'use strict';

const recipe = require('../../lib/recipe');
const store = require('../../lib/store');
const { handle } = require('../../lib/http');

module.exports = handle({
  // 一覧。?all=1 で未確定も含める (既定は息子用の確定済みだけ、公開用の形)
  async GET(req) {
    const docs = await store.listAll();
    if (req.query.all) return docs;
    return docs.filter((d) => d.finalizedAt).map(recipe.toPublic);
  },
  // 文字起こしからレシピ案と質問を作る。body: { startedAt, segments: [{ text, at }] }
  async POST(req) {
    const { startedAt, segments } = req.body || {};
    if (!startedAt || !Array.isArray(segments)) {
      throw Object.assign(new Error('startedAt と segments が必要です'), { status: 400 });
    }
    const dictionary = await store.loadDictionary();
    return store.save(await recipe.create({ startedAt, segments }, undefined, dictionary));
  },
});
