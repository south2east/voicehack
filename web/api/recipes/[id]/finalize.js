'use strict';

const recipe = require('../../../lib/recipe');
const store = require('../../../lib/store');
const { handle } = require('../../../lib/http');

module.exports = handle({
  // 確定したら、お母さんが確認した分量を「この家の分量の辞書」に取り込む
  async POST(req) {
    const doc = await store.load(req.query.id);
    if (doc.finalizedAt) return doc;
    recipe.finalize(doc);
    await store.saveDictionary(recipe.learnDictionary(await store.loadDictionary(), doc));
    return store.save(doc);
  },
});
