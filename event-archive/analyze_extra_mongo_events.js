// Read-only analysis of MongoDB events not present in the filtered Testnet archive.
// Usage: docker exec -i influence-eventprocessor node -e "$(cat analyze_extra_mongo_events.js)" \
//   < ~/Influence-Mainnet/events-testnet/asteroid_60000_60999.jsonl
const mongoose=require('mongoose');
const readline=require('readline');
const canonical=h=>'0x'+BigInt(h).toString(16).padStart(64,'0');
const array=a=>JSON.stringify((a||[]).map(v=>BigInt(v).toString(16)));
const signature=e=>JSON.stringify([canonical(e.address||e.from_address),array(e.keys),array(e.data)]);
const increment=(o,k,n=1)=>{o[k]=(o[k]||0)+n;};
async function main(){
  const transactions=new Map();
  for await(const line of readline.createInterface({input:process.stdin,crlfDelay:Infinity})){
    if(!line.trim())continue;
    const e=JSON.parse(line),tx=canonical(e.transaction_hash);
    if(!transactions.has(tx))transactions.set(tx,[]);
    transactions.get(tx).push(e);
  }
  if(!transactions.size)throw Error('No input events');
  await mongoose.connect(process.env.MONGO_URL,{serverSelectionTimeoutMS:10000});
  const db=mongoose.connection.db;
  if(db.databaseName!=='pre-release')throw Error('Refusing to inspect non-Testnet DB '+db.databaseName);
  const collection=db.collection('events');
  const summary={transactionsExamined:0,transactionsWithExtraEvents:0,extraMongoEvents:0,
    extraByEvent:{},extraByContract:{},extraByPosition:{},
    matchedEvents:0,ordinalMismatches:0,mismatchesWithExtraBefore:0,
    mismatchesExplainedByExtraBefore:0,mismatchesNotExplained:0,
    transactionsWithMismatches:0,examples:[]};
  for(const [tx,raw] of transactions){
    const stored=await collection.find({__t:'Starknet',transactionHash:tx},
      {projection:{_id:0,event:1,logIndex:1,address:1,keys:1,data:1,blockNumber:1}})
      .sort({logIndex:1}).toArray();
    if(!stored.length)continue;
    summary.transactionsExamined++;
    raw.sort((a,b)=>Number(a.event_index)-Number(b.event_index));
    const available=new Map();
    stored.forEach((e,i)=>{
      const key=signature(e);
      if(!available.has(key))available.set(key,[]);
      available.get(key).push(i);
    });
    const matched=new Set(),pairs=[];
    for(let i=0;i<raw.length;i++){
      const candidates=available.get(signature(raw[i]))||[];
      const j=candidates.find(n=>!matched.has(n));
      if(j===undefined)throw Error('Unexpected unmatched archive payload at '+tx);
      matched.add(j);pairs.push({archiveOrdinal:i,storedPosition:j,event:stored[j],raw:raw[i]});
    }
    const extras=stored.map((e,i)=>({e,i})).filter(({i})=>!matched.has(i));
    if(extras.length){
      summary.transactionsWithExtraEvents++;
      for(const {e,i} of extras){
        summary.extraMongoEvents++;
        increment(summary.extraByEvent,e.event||'(unnamed)');
        increment(summary.extraByContract,e.address||'(no address)');
        increment(summary.extraByPosition,i===0?'first':i===stored.length-1?'last':'middle');
      }
    }
    let txMismatch=false;
    for(const pair of pairs){
      summary.matchedEvents++;
      const {archiveOrdinal,storedPosition,event}=pair;
      if(event.logIndex===archiveOrdinal)continue;
      txMismatch=true;summary.ordinalMismatches++;
      const precedingExtras=extras.filter(x=>x.i<storedPosition).length;
      if(precedingExtras)summary.mismatchesWithExtraBefore++;
      // If the stored array is contiguous and starts at zero, this reconstructs exact ordinal.
      if(event.logIndex===archiveOrdinal+precedingExtras)summary.mismatchesExplainedByExtraBefore++;
      else summary.mismatchesNotExplained++;
      if(summary.examples.length<14)summary.examples.push({
        block:event.blockNumber,tx,event:event.event,archiveOrdinal,
        mongoLogIndex:event.logIndex,extraEventsBefore:precedingExtras,
        extraEventsInTransaction:extras.map(({e})=>({event:e.event,logIndex:e.logIndex,address:e.address}))
      });
    }
    if(txMismatch)summary.transactionsWithMismatches++;
  }
  console.log(JSON.stringify({database:db.databaseName,summary,
    caveats:[
      'Payload matching uses normalized transaction hash, contract address, full keys and data.',
      'Identical repeated payloads are paired in stored log-index order; ambiguous duplicates remain possible.',
      'Extra-before explanation assumes existing MongoDB logIndex is a contiguous zero-based transaction order.',
      'Does not assert that the archive omitted events needed by the game or that MongoDB has complete canonical receipts.',
      'Read-only: does not modify MongoDB, checkpoints or the running retriever.'
    ]},null,2));
  await mongoose.disconnect();
}
main().catch(async e=>{console.error('ERROR:',e.stack||e.message);try{await mongoose.disconnect()}catch{}process.exitCode=1});
