// Generate preview-only MongoDB JSONL using Influence's original Mainnet handlers.
// Pure conversion: no MongoDB connections, no RPC access and no file writes.
// Consume events already enriched by enrich_mainnet_events.py.
// Records are not yet validated against a full Mongoose model or event processor.
require('module-alias/register');
const readline=require('readline');
const { Address }=require('@influenceth/sdk');
const handlers=require('/app/src/common/lib/events/handlers/starknet');
const Config=require('/app/src/common/lib/events/retrievers/starknet/config');

const contracts={
  asteroid:'0x603cf837055c64d026a3c5a9e3a83036cea6c4a3f68a9e19f7a687d726fe817',
  crew:'0x7280a807c8b79379bec87919433b7b836b93a92e6d71b24ee99f4ffe33dd337',
  crewmate:'0x241b9c4ce12c06f49fee2ec7c16337386fa5185168f538a7631aacecdf3df74',
  dispatcher:'0x422d33a3638dcc4c62e72e1d6942cd31eb643ef596ccac2351e0e21f6cd4bf4',
  ship:'0x04369e47e647ab5fc4d36cee26590276b99a89a83fc3306462d21d366611fde3',
  sway:'0x004878d1148318a31829523ee9c6a5ee563af6cd87f90a30809e5b0d27db8a9b',
  escrow:'0x0487a1c4d61c05d79e22ed90d4811b32277961707338c78d563a245749f57618'
};
const map={
  [contracts.asteroid]:handlers.Asteroid,
  [contracts.crew]:handlers.Crew,
  [contracts.crewmate]:handlers.Crewmate,
  [contracts.dispatcher]:handlers.Dispatcher,
  [contracts.ship]:handlers.Ship,
  [contracts.sway]:handlers.Sway,
  // Escrow is archived for completeness, but the original config has no Escrow handler.
  [contracts.escrow]:null
};
Config.buildEventsConfig(map);
const count=(obj,key)=>{obj[key]=(obj[key]||0)+1;};
const padded=h=>'0x'+BigInt(h).toString(16).padStart(64,'0');

async function main(){
  const summary={total:0,parsed:0,ignored:0,unhandled:0,failed:0};
  const seen=new Set();
  for await(const line of readline.createInterface({input:process.stdin,crlfDelay:Infinity})){
    if(!line.trim())continue;
    const raw=JSON.parse(line);summary.total++;
    if(!Number.isInteger(raw.timestamp)||!raw.status)throw Error('Missing verified block metadata');
    const event={
      address:Address.toStandard(raw.from_address,'starknet'),
      blockHash:raw.block_hash,blockNumber:Number(raw.block_number),
      data:raw.data,keys:raw.keys,logIndex:Number(raw.event_index),
      transactionHash:padded(raw.transaction_hash),
      transactionIndex:Number(raw.transaction_index),
      timestamp:raw.timestamp,status:raw.status
    };
    const id=event.transactionHash+':'+event.logIndex;
    if(seen.has(id))throw Error('Duplicate tx/logIndex '+id);
    seen.add(id);
    const handler=Config.getHandler(event);
    if(!handler){summary.unhandled++;continue;}
    if(handler.ignore){summary.ignored++;continue;}
    try{
      const parsed=handler.parseEvent(event);
      const output={
        __t:'Starknet',
        address:parsed.address,blockHash:parsed.blockHash,
        blockNumber:parsed.blockNumber,data:parsed.data,keys:parsed.keys,
        event:parsed.event,logIndex:parsed.logIndex,name:parsed.name,
        returnValues:parsed.returnValues,status:parsed.status,timestamp:parsed.timestamp,
        transactionIndex:parsed.transactionIndex,transactionHash:parsed.transactionHash,
        version:parsed.version,removed:false,lastProcessed:null
      };
      if(parsed.signature!==undefined)output.signature=parsed.signature;
      process.stdout.write(JSON.stringify(output,(_k,v)=>typeof v==='bigint'?v.toString():v)+'\n');
      summary.parsed++;
    }catch(err){
      summary.failed++;
      console.error('PARSE FAIL:',event.blockNumber,event.transactionHash,event.logIndex,err.stack||err.message);
    }
  }
  console.error('MAINNET_IMPORT_PREVIEW',JSON.stringify(summary));
  if(summary.total===0||summary.failed)process.exitCode=1;
}
main().then(()=>process.exit(process.exitCode||0)).catch(e=>{
  console.error('IMPORT PREVIEW ERROR',e.stack||e.message);process.exit(1);
});
