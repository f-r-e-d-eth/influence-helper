#!/usr/bin/env node
/* One-off Mainnet event replay; no modifications to Influence source.
 * Requires --db=influence_mainnet_replay_vN (N>=2).
 * V2 seeds 30 events from lab.events; V3 seeds 1081 staged events.
 * First run --seed-only; then --max-events=N. Stop immediately on errors.
 * Run ONLY inside isolated MongoDB's network namespace.
 */
'use strict';
// Validate target before ANY application modules can initialize clients/caches.
const argv = process.argv.slice(2);
const dbArgs = argv.filter(x => x.startsWith('--db='));
const modeArgs = argv.filter(x => !x.startsWith('--db='));
const dbName = dbArgs.length === 1 ? dbArgs[0].slice(5) : '';
if (!/^influence_mainnet_replay_v(?:[2-9]|[1-9][0-9]+)$/.test(dbName)) {
  console.error('Require safe target: --db=influence_mainnet_replay_v2 (or v3, etc.)');
  process.exit(2);
}
const DB_SOURCE='influence_mainnet_lab';
const DB_TARGET=dbName;
const MONGO='mongodb://127.0.0.1:27017/' + DB_TARGET;
// Keyv's Mongo cache uses config.get('MongoDb.uri'), not Mongoose's connection.
// Enforce the same validated loopback URI for both clients before loading handlers.
process.env.NODE_CONFIG=JSON.stringify({MongoDb:{uri:MONGO}});
require('module-alias/register');
const mongoose = require('mongoose');
function isolate(name, value) {
  const file = require.resolve(name);
  require.cache[file] = { id:file, filename:file, loaded:true, exports:value };
}
// Notification transport is deliberately unavailable. If a handler attempts
// explicit socket emission it fails, rather than sending anything.
isolate('@common/lib/sio/emitter', {
  emitTo: async () => { throw Error('Socket emission blocked during bootstrap'); },
  broadcast: async () => { throw Error('Socket emission blocked during bootstrap'); },
  isConnected: false
});
// Prevent Web3 initialization from connecting to the configured Ethereum node.
isolate('@common/lib/web3', new Proxy({}, { get(_, key) {
  throw Error('Live Web3 access blocked: ' + String(key));
}}));
const { entitiesPlugin } = require('/app/src/common/storage/db/plugins');
mongoose.plugin(entitiesPlugin, { tags:['useEntitiesPlugin'] });
mongoose.set('autoIndex', false);
require('/app/src/common/storage/db/models');

const handlers = require('/app/src/common/lib/events/handlers');
const EventConfig = require('/app/src/common/lib/events/processor/config');
EventConfig.buildHandlerConfig({
  '0x603cf837055c64d026a3c5a9e3a83036cea6c4a3f68a9e19f7a687d726fe817': handlers.starknet.Asteroid,
  '0x7280a807c8b79379bec87919433b7b836b93a92e6d71b24ee99f4ffe33dd337': handlers.starknet.Crew,
  '0x241b9c4ce12c06f49fee2ec7c16337386fa5185168f538a7631aacecdf3df74': handlers.starknet.Crewmate,
  '0x422d33a3638dcc4c62e72e1d6942cd31eb643ef596ccac2351e0e21f6cd4bf4': handlers.starknet.Dispatcher,
  '0x04369e47e647ab5fc4d36cee26590276b99a89a83fc3306462d21d366611fde3': handlers.starknet.Ship,
  '0x004878d1148318a31829523ee9c6a5ee563af6cd87f90a30809e5b0d27db8a9b': handlers.starknet.Sway
});

const ORDER={blockNumber:1,transactionIndex:1,logIndex:1};
const IS_V3=DB_TARGET==='influence_mainnet_replay_v3';
const SOURCE_COLLECTION=IS_V3?'mainnet_v3_staging_events':'events';
const EXPECTED=IS_V3?1081:30;
const SEED=modeArgs.length===1 && modeArgs[0]==='--seed-only';
const replayMatch=modeArgs.length===1 && /^--max-events=[1-9][0-9]*$/.test(modeArgs[0]) &&
  Number.isSafeInteger(Number(modeArgs[0].split('=')[1])) &&
  Number(modeArgs[0].split('=')[1])<=EXPECTED;
if (!SEED && !replayMatch) {
  console.error('Usage: node mainnet_replay_run.js --db=influence_mainnet_replay_v2|v3 (--seed-only | --max-events=1..'+EXPECTED+')');
  process.exit(2);
}
const MAX=replayMatch ? Number(modeArgs[0].split('=')[1]) : 0;

async function main() {
  await mongoose.connect(MONGO,{serverSelectionTimeoutMS:5000});
  if(mongoose.connection.name!==DB_TARGET) throw Error('Unexpected target database');
  const client=mongoose.connection.getClient();
  const source=client.db(DB_SOURCE);
  const target=mongoose.connection.db;
  if (SEED) {
    const names=(await target.listCollections({}, {nameOnly:true}).toArray()).map(x=>x.name);
    if(names.length) throw Error('Target database is NOT empty: '+names.join(','));
    const docs=await source.collection(SOURCE_COLLECTION).find({}).sort(ORDER).toArray();
    if(docs.length!==EXPECTED ||
       docs.some(e=>e.removed===true || e.lastProcessed!=null ||
         !Number.isInteger(e.logIndex) || !Number.isInteger(e.transactionIndex) ||
         !Number.isInteger(e.blockNumber) ||
         (IS_V3 ? e.blockNumber<219564 || e.blockNumber>238471 : e.blockNumber!==219564)))
      throw Error('Unexpected source event data; refusing to seed');
    const identities=docs.map(e=>e.transactionHash+':'+e.logIndex+':'+e.event);
    if(new Set(identities).size!==EXPECTED ||
       new Set(docs.map(e=>String(e._id))).size!==EXPECTED)
      throw Error('Duplicate event identities or IDs');
    if(IS_V3) {
      const original=await source.collection('events').find({}).toArray();
      const first=docs.filter(e=>e.blockNumber===219564);
      const reference=new Set(original.map(e=>e.transactionHash+':'+e.logIndex+':'+e.event));
      if(original.length!==30 || first.length!==30 ||
         first.some(e=>!reference.has(e.transactionHash+':'+e.logIndex+':'+e.event)) ||
         docs[docs.length-1].blockNumber!==238471)
        throw Error('V3 seed does not match the verified 30-event reference or endpoint');
    }
    for(const event of docs) {
      if (!EventConfig.getHandlerByAddressAndEvent({
        address:event.address,eventName:event.event
      })) throw Error('Missing handler for '+event.event);
    }
    await target.collection('events').insertMany(docs,{ordered:true});
    await target.collection('events').createIndex(
      {event:1,logIndex:1,transactionHash:1},
      {unique:true,name:'starknet_unique'}
    );
    console.log('SEEDED',await target.collection('events').countDocuments({}),
      'events with original ObjectIds; no handlers executed.');
    console.log('Source lab was not modified.');
    return;
  }
  const Event=mongoose.model('Event');
  const total=await Event.countDocuments({});
  if(total!==EXPECTED) throw Error('Expected '+EXPECTED+' seeded events, found '+total);
  const pending=await Event.find({lastProcessed:null,removed:{$ne:true}})
    .sort(ORDER).limit(MAX);
  console.log('REPLAY',pending.length,'events; target:',DB_TARGET);
  for(const event of pending) {
    const Handler=EventConfig.getHandlerByAddressAndEvent({
      address:event.address,eventName:event.event
    });
    if(!Handler) throw Error('Missing handler: '+event.event);
    console.log('START',event.blockNumber,event.logIndex,event.event);
    const handler=new Handler(event);
    // Original handler logic; live socket notifications intentionally omitted.
    await handler.processEvent();
    await handler.finalizeEvent();
    console.log('DONE',event.logIndex,event.event);
  }
  console.log('Processed total:',await Event.countDocuments({lastProcessed:{$ne:null}}));
  console.log('Pending:',await Event.countDocuments({lastProcessed:null,removed:{$ne:true}}));
  console.log('Component collections:',(await target.listCollections({name:/^Component_/}).toArray())
    .map(x=>x.name).join(', ') || '(none)');
}

main().then(async()=>{
  await mongoose.disconnect();
  process.exit(0);
}).catch(async err=>{
  console.error('REPLAY STOPPED:',err.stack||err.message);
  try{await mongoose.disconnect();}catch(_){}
  process.exit(1);
});
