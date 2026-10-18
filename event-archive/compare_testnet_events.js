// Read-only Testnet archive ↔ Influence MongoDB comparison.
// Usage:
// docker exec -i influence-eventprocessor node -e "$(cat compare_testnet_events.js)" \
//   < /home/iu/Influence-Mainnet/events-testnet/asteroid_60000_60999.jsonl
// Never writes to MongoDB. Uses container's existing MONGO_URL.
const mongoose = require('mongoose');
const readline = require('readline');

const canonical = (h) => {
  if (!h || !/^0x[0-9a-f]+$/i.test(h)) throw new Error('Invalid transaction hash: ' + h);
  return '0x' + BigInt(h).toString(16).padStart(64, '0');
};
async function main() {
  const txs = new Map();
  let total = 0;
  for await (const line of readline.createInterface({input:process.stdin,crlfDelay:Infinity})) {
    if (!line.trim()) continue;
    const e = JSON.parse(line);
    const hash = canonical(e.transaction_hash);
    if (!txs.has(hash)) txs.set(hash, []);
    txs.get(hash).push(e);
    total++;
  }
  if (!total) throw new Error('Archive file is empty');
  await mongoose.connect(process.env.MONGO_URL, {serverSelectionTimeoutMS:10000});
  const db = mongoose.connection.db;
  if (db.databaseName !== 'pre-release') throw new Error('Refusing non-Testnet database: ' + db.databaseName);
  const events = db.collection('events');
  const stats = {rawEvents:total,transactions:txs.size,mongoTransactions:0,missingTransactions:0,
    compared:0,indexMatches:0,indexMismatches:0,unmatchedRaw:0,unmatchedMongo:0};
  const examples=[];
  for (const [hash, raw] of txs) {
    const stored = await events.find({__t:'Starknet',transactionHash:hash},
      {projection:{_id:0,event:1,logIndex:1,transactionHash:1,keys:1,blockNumber:1}})
      .sort({logIndex:1}).toArray();
    if (!stored.length) {stats.missingTransactions++;continue;}
    stats.mongoTransactions++;
    raw.sort((a,b)=>a.event_index-b.event_index);
    // Compare events by complete keys where available; no assumption about indexes for unhandled events.
    const unused=new Set(stored.map((_,i)=>i));
    for (let i=0;i<raw.length;i++) {
      const e=raw[i];
      const keys=JSON.stringify(e.keys.map(x=>'0x'+BigInt(x).toString(16)));
      let matches=[...unused].filter(j=>JSON.stringify((stored[j].keys||[]).map(x=>'0x'+BigInt(x).toString(16)))===keys);
      // If multiple emitted events have same keys, pair in log-index order.
      if (!matches.length) {stats.unmatchedRaw++;continue;}
      const j=matches[0];unused.delete(j);stats.compared++;
      // Expected index is ordinal among the archived events of this transaction.
      // This diagnostic is not claiming ordinal is always the provider's logIndex.
      if (stored[j].logIndex===i) stats.indexMatches++;
      else {
        stats.indexMismatches++;
        if (examples.length<8) examples.push({block:e.block_number,tx:hash,rawEventIndex:e.event_index,
          archiveTransactionOrdinal:i,storedLogIndex:stored[j].logIndex,storedEvent:stored[j].event});
      }
    }
    stats.unmatchedMongo+=unused.size;
  }
  console.log(JSON.stringify({database:db.databaseName,stats,examples,
    caveats:['Comparison matches by transaction hash and encoded event keys; duplicate keys use order.',
    'Missing raw events may be ignored, or absent from MongoDB.',
    'Archive file must include all relevant events for each transaction to evaluate logIndex ordinals.',
    'This does not test block timestamps, finality, parser output or DB writes.']},null,2));
  await mongoose.disconnect();
}
main().catch(async e=>{console.error('ERROR:',e.message);try{await mongoose.disconnect()}catch{}process.exitCode=1});
