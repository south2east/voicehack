'use strict';

const recipe = require('../../lib/recipe');
const store = require('../../lib/store');
const { handle } = require('../../lib/http');

module.exports = handle({
  // ?public=1 なら息子用の形 (確定済みのみ)
  async GET(req) {
    const doc = await store.load(req.query.id);
    if (!req.query.public) return doc;
    if (!doc.finalizedAt) throw Object.assign(new Error('recipe not found'), { status: 404 });
    return recipe.toPublic(doc);
  },
});
