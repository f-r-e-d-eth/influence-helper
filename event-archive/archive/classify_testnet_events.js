// Read-only diagnostic: classify Testnet archive events with the ORIGINAL
// Influence Starknet handler registry and inspect transactions absent in MongoDB.
// Usage:
// docker exec -i influence-eventprocessor node -e "$(cat classify_testnet_events.js)" \
//   < ~/Influence-Mainnet/events-testnet/asteroid_60000_60999.jsonl
// node -e does not execute the app's normal alias initialization.
require('module-alias/register');
const mongoose = require('mongoose');
const readline = require('readline');
const StarknetEventConfig = require('/app/src/common/lib/events/retrievers/starknet/config');
const { Address } = require('@influenceth/sdk');

const canonical = h => '0x'+BigInt(h).toString(16).padStart(64,'0');
const inc = (obj,key,n=1) => { obj[key]=(obj[key]||0)+n; };
function classify(e){
  const event={address:Address.toStandard(e.from_address,'starknet'),keys:e.keys,data:e.data};
  const handler=StarknetEventConfig.getHandler(event);
  if(!handler)return {status:'unhandled',name:null};
  if(handler.ignore)return {status:'ignored',name:handler.eventName||null};
  return {status:'handled',name:handler.eventName};
}
async function main(){
  const txs=new Map(); let rawEvents=0;
  const totals={handled:0,ignored:0,unhandled:0};
  for await(const line of readline.createInterface({input:process.stdin,crlfDelay:Infinity})){
    if(!line.trim())continue;
    const e=JSON.parse(line),tx=canonical(e.transaction_hash);
    if(!txs.has(tx))txs.set(tx,[]);
    const cls=classify(e);
    txs.get(tx).push({e,cls}); rawEvents++;inc(totals,cls.status);
  }
  if(!rawEvents)throw Error('No archive events supplied');
  await mongoose.connect(process.env.MONGO_URL,{serverSelectionTimeoutMS:10000});
  const db=mongoose.connection.db;
  if(db.databaseName!=='pre-release')throw Error('Refusing DB '+db.databaseName);
  const collection=db.collection('events');
  const missing={transactions:0,events:0,byClassification:{},byEvent:{},byContract:{},examples:[]};
  const present={transactions:0,events:0,byClassification:{}};
  for(const [tx,rows] of txs){
    const stored=await collection.findOne({__t:'Starknet',transactionHash:tx},{projection:{_id:1}});
    if(stored){
      present.transactions++;present.events+=rows.length;
      rows.forEach(({cls})=>inc(present.byClassification,cls.status));
      continue;
    }
    missing.transactions++;missing.events+=rows.length;
    for(const {e,cls} of rows){
      inc(missing.byClassification,cls.status);
      inc(missing.byEvent,cls.name||'(no registered handler)');
      inc(missing.byContract,Address.toStandard(e.from_address,'starknet'));
      if(missing.examples.length<15)missing.examples.push({
        block:e.block_number,tx,eventIndex:e.event_index,address:e.from_address,
        keys:e.keys,status:cls.status,handler:cls.name
      });
    }
  }
  console.log(JSON.stringify({
    database:db.databaseName,rawEvents,rawTransactions:txs.size,
    trackedContracts:StarknetEventConfig.toArray().map(x=>x.address),
    allArchiveClassifications:totals,present,missing,
    caveats:[
      'Uses handler registry from the installed eventprocessor image; deployment code may differ from retriever.',
      'A missing transaction is one with no Starknet record in MongoDB at all.',
      'Handled does not prove the event must already exist: historical versions, reorgs, or gaps can differ.',
      'Read-only. No parser execution, RPC calls, or MongoDB writes.'
    ]
  },null,2));
  await mongoose.disconnect();
}
main().catch(async err=>{
  console.error('ERROR:',err.stack||err.message);
  try{await mongoose.disconnect()}catch{}
  process.exitCode=1;
});
