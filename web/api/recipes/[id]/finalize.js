'use strict';

const recipe = require('../../../lib/recipe');
const store = require('../../../lib/store');
const { handle } = require('../../../lib/http');

module.exports = handle({
  async POST(req) {
    return store.save(recipe.finalize(await store.load(req.query.id)));
  },
});
