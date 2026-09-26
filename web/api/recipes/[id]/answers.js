'use strict';

const recipe = require('../../../lib/recipe');
const store = require('../../../lib/store');
const { handle } = require('../../../lib/http');

module.exports = handle({
  // body: { answers: [{ question, answer }] }
  async POST(req) {
    const [doc, dictionary] = await Promise.all([store.load(req.query.id), store.loadDictionary()]);
    return store.save(await recipe.answer(doc, (req.body && req.body.answers) || [], undefined, dictionary));
  },
});
