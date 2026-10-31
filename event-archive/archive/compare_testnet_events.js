// Read-only Testnet raw archive vs original Influence MongoDB.
// Compares normalized transaction hash + address + keys + full data.
// Run inside influence-eventprocessor with JSONL piped through stdin.
// No database writes; refuses any DB other than 'pre-release'.
const mongoose = require('mongoose');
const readline = require('readline');

const hex64 = v => {
  if (typeof v !== 'string' || !/^0x[\da-f]+$/i.test(v)) throw Error('Invalid hex: '+v);
  return '0x'+BigInt(v).toString(16).padStart(64,'0');
};
const normalizedArray = a => JSON.stringify((a||[]).map(x=>BigInt(x).toString(16)));
const fingerprint = e => JSON.stringify([
  hex64(e.address||e.from_address),
  normalizedArray(e.keys),
  normalizedArray(e.data)
]);
async function main() {
  const txs=new Map(); let rawCount=0;
  for await(const line of readline.createInterface({input:process.stdin,crlfDelay:Infinity})) {
    if(!line.trim())continue;
    const e=JSON.parse(line), hash=hex64(e.transaction_hash);
    if(!txs.has(hash))txs.set(hash,[]);
    txs.get(hash).push(e);rawCount++;
  }
  if(!rawCount)throw Error('Archive empty');
  await mongoose.connect(process.env.MONGO_URL,{serverSelectionTimeoutMS:10000});
  const db=mongoose.connection.db;
  if(db.databaseName!=='pre-release')throw Error('Refusing database '+db.databaseName);
  const collection=db.collection('events');
  const stats={rawEvents:rawCount,transactions:txs.size,mongoTransactions:0,
    missingTransactions:0,missingTransactionRawEvents:0,matchedPayloads:0,
    ordinalMatches:0,ordinalMismatches:0,directIndexMatches:0,directIndexMismatches:0,
    rawWithoutPayloadMatch:0,mongoWithoutPayloadMatch:0,
    transactionsWithNoMismatch:0,transactionsWithMismatch:0};
  const examples=[];
  for(const [hash,raw] of txs){
    const stored=await collection.find({__t:'Starknet',transactionHash:hash},
      {projection:{_id:0,address:1,keys:1,data:1,event:1,logIndex:1,blockNumber:1}})
      .sort({logIndex:1}).toArray();
    if(!stored.length){
      stats.missingTransactions++;stats.missingTransactionRawEvents+=raw.length;continue;
    }
    stats.mongoTransactions++;
    raw.sort((a,b)=>a.block_number-b.block_number || a.transaction_index-b.transaction_index || a.event_index-b.event_index);
    const bins=new Map();
    stored.forEach((e,i)=>{
      const key=fingerprint(e);
      if(!bins.has(key))bins.set(key,[]);
      bins.get(key).push(i);
    });
    const used=new Set();let anyMismatch=false;
    for(let i=0;i<raw.length;i++){
      const e=raw[i],key=fingerprint(e);
      const candidates=bins.get(key)||[];
      const j=candidates.find(index=>!used.has(index));
      if(j===undefined){
        stats.rawWithoutPayloadMatch++;anyMismatch=true;
        if(examples.length<12)examples.push({reason:'raw_missing_from_mongo',block:e.block_number,tx:hash,rawIndex:e.event_index,keys:e.keys});
        continue;
      }
      used.add(j);stats.matchedPayloads++;
      if(stored[j].logIndex===i)stats.ordinalMatches++;
      else {stats.ordinalMismatches++;anyMismatch=true;
        if(examples.length<12)examples.push({reason:'ordinal_mismatch',block:e.block_number,tx:hash,archiveOrdinal:i,
          rawIndex:e.event_index,storedLogIndex:stored[j].logIndex,event:stored[j].event});
      }
      if(stored[j].logIndex===Number(e.event_index))stats.directIndexMatches++;
      else stats.directIndexMismatches++;
    }
    stats.mongoWithoutPayloadMatch+=stored.length-used.size;
    if(stored.length-used.size)anyMismatch=true;
    if(anyMismatch)stats.transactionsWithMismatch++;else stats.transactionsWithNoMismatch++;
  }
  console.log(JSON.stringify({database:db.databaseName,stats,examples,
    notes:[
      'Payload identity uses normalized address, keys and complete data within a padded transaction hash.',
      'Repeated identical payloads are paired in MongoDB logIndex order; their identity remains ambiguous.',
      'Ordinal means sequence within THIS archive, not necessarily the original retriever sequence.',
      'Missing transactions are counted separately; unhandled or unarchived contracts may explain differences.',
      'This is read-only; does not test timestamps, statuses, parser decoding or import checkpoints.'
    ]},null,2));
  await mongoose.disconnect();
}
main().catch(async e=>{console.error('ERROR:',e.message);try{await mongoose.disconnect()}catch{}process.exitCode=1});
