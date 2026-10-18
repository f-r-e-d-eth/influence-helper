#!/usr/bin/env node
/*
 * Mainnet historical event replay — phase 1: read-only preflight.
 *
 * Run in the ORIGINAL influence-server image, with the isolated lab MongoDB
 * network namespace. NO writes are made in this version.
 *
 * Example:
 *   docker run --rm --network container:influence-mainnet-lab-mongo \
 *     -v "$PWD:/bootstrap:ro" -e NODE_PATH=/app/node_modules \
 *     --entrypoint node influence-server /bootstrap/mainnet_replay.js
 */
'use strict';

require('module-alias/register');

const mongoose = require('mongoose');

// Imported Influence handlers initialize external Redis and Ethereum WS
// clients at module load time. Prevent these connections for the preflight.
// Never fake state-changing operations or blockchain answers.
function substitute(moduleName, exports) {
  const path = require.resolve(moduleName);
  require.cache[path] = {
    id: path, filename: path, loaded: true, exports
  };
}
substitute('@common/lib/sio/emitter', {
  emitTo: async () => { throw new Error('Socket emission not allowed during preflight'); },
  broadcast: async () => { throw new Error('Socket emission not allowed during preflight'); },
  isConnected: false
});
substitute('@common/lib/web3', new Proxy({}, {
  get(_target, key) {
    throw new Error('Web3 usage not allowed during preflight: ' + String(key));
  }
}));

const { Address } = require('@influenceth/sdk');
const handlers = require('/app/src/common/lib/events/handlers');
const EventConfig = require('/app/src/common/lib/events/processor/config');

// Mainnet contract addresses, as verified when generating the event archive.
const mainnetHandlers = {
  '0x603cf837055c64d026a3c5a9e3a83036cea6c4a3f68a9e19f7a687d726fe817': handlers.starknet.Asteroid,
  '0x7280a807c8b79379bec87919433b7b836b93a92e6d71b24ee99f4ffe33dd337': handlers.starknet.Crew,
  '0x241b9c4ce12c06f49fee2ec7c16337386fa5185168f538a7631aacecdf3df74': handlers.starknet.Crewmate,
  '0x422d33a3638dcc4c62e72e1d6942cd31eb643ef596ccac2351e0e21f6cd4bf4': handlers.starknet.Dispatcher,
  '0x04369e47e647ab5fc4d36cee26590276b99a89a83fc3306462d21d366611fde3': handlers.starknet.Ship,
  '0x004878d1148318a31829523ee9c6a5ee563af6cd87f90a30809e5b0d27db8a9b': handlers.starknet.Sway
};
EventConfig.buildHandlerConfig(mainnetHandlers);

const uri = 'mongodb://127.0.0.1:27017/influence_mainnet_lab';
const EXPECTED_EVENTS = 30;
const EXPECTED_BLOCK = 219564;
const byOrder = (a, b) =>
  a.blockNumber - b.blockNumber ||
  a.transactionIndex - b.transactionIndex ||
  a.logIndex - b.logIndex;

async function main() {
  if (process.argv.slice(2).some(arg => arg !== '--dry-run')) {
    throw new Error('Read-only preflight only. No replay/writes implemented yet.');
  }
  await mongoose.connect(uri, { serverSelectionTimeoutMS: 5000 });
  if (mongoose.connection.name !== 'influence_mainnet_lab') {
    throw new Error('Unexpected database: ' + mongoose.connection.name);
  }
  const db = mongoose.connection.db;
  const events = await db.collection('events').find({}).toArray();
  if (events.length !== EXPECTED_EVENTS) {
    throw new Error('Expected ' + EXPECTED_EVENTS + ' events; found ' + events.length);
  }
  if (events.some(e =>
    e.blockNumber !== EXPECTED_BLOCK ||
    e.removed === true ||
    e.lastProcessed != null ||
    !Number.isInteger(e.logIndex) ||
    !Number.isInteger(e.transactionIndex) ||
    !Number.isFinite(e.timestamp))) {
    throw new Error('Unexpected event block, checkpoint, or ordering fields');
  }
  events.sort(byOrder);
  const seen = new Set();
  console.log('READ-ONLY MAINNET PREFLIGHT —', events.length, 'events');
  console.log('Database:', mongoose.connection.name, '| Block:', EXPECTED_BLOCK);
  console.log('logIndex | event name | handler');
  for (const event of events) {
    const key = event.transactionHash + ':' + event.logIndex;
    if (seen.has(key)) throw new Error('Duplicate transaction/logIndex: ' + key);
    seen.add(key);
    const handler = EventConfig.getHandlerByAddressAndEvent({
      address: event.address,
      eventName: event.event
    });
    const valid = Boolean(handler);
    console.log(
      String(event.logIndex).padStart(2) + ' | ' +
      event.event + ' | ' + (valid ? 'MATCH' : 'MISSING')
    );
    if (!valid) {
      throw new Error('No Mainnet handler for ' +
        Address.toStandard(event.address) + ' / ' + event.event);
    }
  }
  console.log('PREFLIGHT PASS: all events matched original Influence handlers.');
  console.log('No component documents, checkpoints, or indexes were modified.');
}

main()
  .then(async () => { await mongoose.disconnect(); process.exit(0); })
  .catch(async e => {
    console.error('PREFLIGHT FAILED:', e.stack || e.message);
    try { await mongoose.disconnect(); } catch (_) { /* ignore */ }
    process.exit(1);
  });
