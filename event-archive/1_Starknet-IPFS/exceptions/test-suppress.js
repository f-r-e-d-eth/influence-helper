const test = require('node:test');
const assert = require('node:assert/strict');
const apply = require('./apply');
const rule = require('./blocks_1900000_1999999.json').exceptions[0];

function fixture(change = {}, count = 1) {
  const event = { ...rule, _id: 'event', removed: false, lastProcessed: null,
    returnValues: structuredClone(rule.expectedReturnValues), ...change };
  let writes = 0;
  const Event = {
    find(query) {
      assert.equal(query.transactionHash, rule.transactionHash);
      assert.equal(query.logIndex, 3);
      return { lean: async () => Array(count).fill(event) };
    },
    collection: { async updateOne(filter, update) {
      assert.deepEqual(filter.data, rule.data);
      assert.deepEqual(filter.returnValues, rule.expectedReturnValues);
      assert.deepEqual(update, { $set: { returnValues: {} } });
      writes++;
      event.returnValues = {};
      return { matchedCount: 1, modifiedCount: 1 };
    } }
  };
  return { event, writes: () => writes, run: () => apply({ mongoose: { model: () => Event }, from: 1900000, to: 1999999 }) };
}

test('suppresses exactly one event and remains idempotent', async () => {
  const f = fixture(); await f.run(); await f.run();
  assert.equal(f.writes(), 1); assert.deepEqual(f.event.data, rule.data);
});
for (const [name, change, count] of [
  ['not found', {}, 0], ['duplicates', {}, 2], ['wrong raw data', { data: ['0x0'] }, 1],
  ['wrong decoded values', { returnValues: { entity: { label: 4, id: 11110 } } }, 1],
  ['processed event', { lastProcessed: new Date() }, 1], ['wrong block hash', { blockHash: '0x0' }, 1]
]) {
  test(`refuses ${name}`, async () => { const f = fixture(change, count); await assert.rejects(f.run(), /REFUSING/); assert.equal(f.writes(), 0); });
}
