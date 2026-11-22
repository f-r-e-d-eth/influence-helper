'use strict';

const fs = require('fs');
const path = require('path');
const { isDeepStrictEqual } = require('node:util');

// Explicitly approved non-lot UseLot events only. Never match an entire class of events.
async function suppressEvent({ Event, rule, from, to }) {
  const { blockNumber, transactionHash, logIndex, blockHash, transactionIndex,
    address, data, keys, expectedReturnValues } = rule;
  const felt = x => typeof x === 'string' && /^0x[0-9a-f]+$/.test(x);
  if (rule.event !== 'ComponentUpdated_Unique' || rule.version !== 0 ||
      !Number.isSafeInteger(blockNumber) || blockNumber < from || blockNumber > to ||
      !Number.isSafeInteger(logIndex) || logIndex < 0 ||
      !Number.isSafeInteger(transactionIndex) || transactionIndex < 0 ||
      ![transactionHash, blockHash, address].every(felt) ||
      !Array.isArray(data) || data.length !== 4 || !data.every(felt) ||
      data[0] !== '0x2' || data[1] !== '0x5573654c6f74' ||
      !isDeepStrictEqual(keys, ['0x297be67eb977068ccd2304c6440368d4a6114929aeb860c98b6a7e91f96e2ef', '0x556e69717565']) ||
      expectedReturnValues?.entity?.label !== 5 ||
      !Number.isSafeInteger(expectedReturnValues.entity.id) ||
      expectedReturnValues.tenant?.label !== 1 ||
      !Number.isSafeInteger(expectedReturnValues.tenant.id)) {
    throw new Error(`REFUSING: invalid suppression rule ${rule.id}`);
  }
  const packed = entity => (BigInt(entity.id) << 16n) | BigInt(entity.label);
  if (BigInt(data[2]) !== packed(expectedReturnValues.entity) ||
      BigInt(data[3]) !== packed(expectedReturnValues.tenant)) {
    throw new Error(`REFUSING: inconsistent packed entity in ${rule.id}`);
  }
  const query = { event: rule.event, version: rule.version, blockNumber, transactionHash, logIndex };
  const matches = await Event.find(query).lean();
  if (matches.length !== 1) throw new Error(`REFUSING: ${rule.id}: found ${matches.length} events`);
  const event = matches[0];
  if (event.blockHash !== blockHash || event.address !== address ||
      event.transactionIndex !== transactionIndex || event.removed !== false ||
      event.lastProcessed !== null || !isDeepStrictEqual(event.data, data) ||
      !isDeepStrictEqual(event.keys, keys)) {
    throw new Error(`REFUSING: unexpected event provenance or processing state in ${rule.id}`);
  }
  if (isDeepStrictEqual(event.returnValues, {})) {
    console.log(`ALREADY APPLIED: ${rule.id}`);
    return 0;
  }
  if (!isDeepStrictEqual(event.returnValues, expectedReturnValues)) {
    throw new Error(`REFUSING: unexpected decoded values in ${rule.id}`);
  }
  const result = await Event.collection.updateOne(
    { _id: event._id, ...query, blockHash, address, transactionIndex, removed: false,
      lastProcessed: null, data, keys, returnValues: event.returnValues },
    { $set: { returnValues: {} } }
  );
  if (result.matchedCount !== 1 || result.modifiedCount !== 1) {
    throw new Error(`REFUSING: suppression update failed for ${rule.id}`);
  }
  console.log(`SUPPRESSED: ${rule.id}, logIndex ${logIndex}`);
  return 1;
}

function isExpected(actual, expected) {
  if (expected === 'NaN') {
    return typeof actual === 'number' && Number.isNaN(actual);
  }

  if (expected === 'NaNEntity') {
    return actual !== null
      && typeof actual === 'object'
      && Number.isNaN(actual.label)
      && Number.isNaN(actual.id);
  }

  return Object.is(actual, expected);
}


async function applyBatch({ Event, rule, from, to }) {
  if (rule.type !== 'batch' ||
      rule.event !== 'ComponentUpdated_Crew' ||
      rule.version !== 0 ||
      !Number.isSafeInteger(rule.expectedCount) ||
      rule.expectedCount < 1) {
    throw new Error(`REFUSING: unsupported batch rule ${rule.id}`);
  }

  const query = {
    event: rule.event,
    version: rule.version,
    blockNumber: { $gte: from, $lte: to }
  };

  const events = await Event.find(query).lean();

  const mixed = Number.isSafeInteger(rule.expectedShortCount)
    && Number.isSafeInteger(rule.expectedExtendedCount);

  if (events.length !== rule.expectedCount) {
    throw new Error(
      `REFUSING: ${rule.id}: expected ${rule.expectedCount}, found ${events.length}`
    );
  }

  const short = events.filter(e => {
    const roster = Number(e.data?.[3]);
    return e.data?.length === 8 + roster;
  });

  const extended = events.filter(e => {
    const roster = Number(e.data?.[3]);
    return e.data?.length === 11 + roster;
  });

  if (mixed && (
    short.length !== rule.expectedShortCount ||
    extended.length !== rule.expectedExtendedCount
  )) {
    throw new Error(`REFUSING: unexpected Crew format counts in ${rule.id}`);
  }

  const updates = [];

  // Validate ALL events before changing anything.
  for (const event of events) {
    const rosterLength = Number(event.data?.[3]);

    if (!Number.isSafeInteger(rosterLength) ||
        rosterLength < 0 ||
        (event.data.length !== 8 + rosterLength &&
         !(mixed && event.data.length === 11 + rosterLength))) {
      throw new Error(`REFUSING: unexpected Crew format at ${event._id}`);
    }

    const fields = ['actionRound', 'actionWeight'];
    const changes = {};

    const isShort = event.data.length === 8 + rosterLength;

    if (mixed) {
      const round = event.returnValues?.actionRound;
      const weight = event.returnValues?.actionWeight;

      if (!isShort && (round !== 0 || weight !== 0)) {
        throw new Error(
          `REFUSING: unexpected extended Crew values at ${event._id}`
        );
      }
    }

    for (const field of fields) {
      const value = event.returnValues?.[field];

      if (Number.isNaN(value)) {
        changes[`returnValues.${field}`] = 0;
      } else if (value !== 0) {
        throw new Error(
          `REFUSING: unexpected ${field} at ${event._id}`
        );
      }
    }

    if (Object.keys(changes).length) {
      updates.push({
        updateOne: {
          filter: { _id: event._id },
          update: { $set: changes }
        }
      });
    }
  }

  console.log(
    `V12 BATCH VERIFIED: ${rule.id}, ${events.length} events`
  );

  if (updates.length) {
    const result = await Event.collection.bulkWrite(updates, {
      ordered: true
    });

    if (result.matchedCount !== updates.length) {
      throw new Error(`REFUSING: incomplete batch ${rule.id}`);
    }
  }

  console.log(
    `V12 BATCH COMPLETE: ${rule.id}, ${updates.length} events updated`
  );

  return updates.length;
}



async function applyShipBatch({ Event, rule, from, to }) {
  if (rule.type !== 'shipBatch' ||
      rule.event !== 'ComponentUpdated_Ship' ||
      rule.version !== 0 ||
      rule.expectedCount !== 1205 ||
      rule.expectedTotal !== 1327) {
    throw new Error(`REFUSING: invalid Ship batch rule ${rule.id}`);
  }

  const events = await Event.find({
    event: rule.event,
    version: rule.version,
    blockNumber: { $gte: from, $lte: to }
  }).lean();

  if (events.length !== rule.expectedTotal) {
    throw new Error(`REFUSING: unexpected Ship event count: ${events.length}`);
  }

  const short = events.filter(e => e.data?.length === 6);

  if (short.length !== rule.expectedCount) {
    throw new Error(`REFUSING: unexpected short Ship count: ${short.length}`);
  }

  const updates = [];
  const numericFields = ['emergencyAt', 'transitDeparture', 'transitArrival'];
  const entityFields = ['transitOrigin', 'transitDestination'];

  // Validate every event before writing.
  for (const event of short) {
    const changes = {};

    for (const field of numericFields) {
      const value = event.returnValues?.[field];

      if (Number.isNaN(value)) {
        changes[`returnValues.${field}`] = 0;
      } else if (value !== 0) {
        throw new Error(`REFUSING: unexpected ${field} at ${event._id}`);
      }
    }

    for (const field of entityFields) {
      const value = event.returnValues?.[field];

      if (isExpected(value, 'NaNEntity')) {
        changes[`returnValues.${field}`] = null;
      } else if (value !== null) {
        throw new Error(`REFUSING: unexpected ${field} at ${event._id}`);
      }
    }

    if (Object.keys(changes).length) {
      updates.push({
        updateOne: {
          filter: { _id: event._id },
          update: { $set: changes }
        }
      });
    }
  }


  // Historical 12-value Ship events omit only transitArrival.
  const extended = events.filter(e => e.data?.length === 12);

  if (extended.length !== rule.expectedExtendedCount) {
    throw new Error(
      `REFUSING: expected ${rule.expectedExtendedCount} extended Ship events, found ${extended.length}`
    );
  }

  if (short.length + extended.length !== events.length) {
    throw new Error('REFUSING: unexpected Ship event format');
  }

  for (const event of extended) {
    const values = event.returnValues;

    if (
      values.emergencyAt !== 0 ||
      values.transitDeparture !== 0 ||
      values.transitOrigin !== null ||
      values.transitDestination !== null ||
      !event.data.slice(6).every(v => v === '0x0')
    ) {
      throw new Error(`REFUSING: unexpected extended Ship at ${event._id}`);
    }

    if (Number.isNaN(values.transitArrival)) {
      updates.push({
        updateOne: {
          filter: { _id: event._id },
          update: { $set: { 'returnValues.transitArrival': 0 } }
        }
      });
    } else if (values.transitArrival !== 0) {
      throw new Error(`REFUSING: unexpected transitArrival at ${event._id}`);
    }
  }



  console.log(`V12 SHIP BATCH VERIFIED: ${short.length} events`);

  if (updates.length) {
    const result = await Event.collection.bulkWrite(updates, { ordered: true });

    if (result.matchedCount !== updates.length) {
      throw new Error('REFUSING: incomplete Ship batch');
    }
  }

  console.log(`V12 SHIP BATCH COMPLETE: ${updates.length} events updated`);
  return updates.length;
}


async function applyShipMixedBatch({ Event, rule, from, to }) {
  if (
    rule.type !== 'shipMixedBatch' ||
    rule.event !== 'ComponentUpdated_Ship' ||
    rule.version !== 0 ||
    rule.expectedTotal !== 560 ||
    rule.expectedShortCount !== 72 ||
    rule.expectedExtendedCount !== 488
  ) {
    throw new Error(`REFUSING: invalid Ship mixed batch ${rule.id}`);
  }

  const events = await Event.find({
    event: rule.event,
    version: rule.version,
    blockNumber: { $gte: from, $lte: to }
  }).lean();

  if (events.length !== rule.expectedTotal) {
    throw new Error(`REFUSING: unexpected Ship count: ${events.length}`);
  }

  const short = events.filter(e => e.data?.length === 12);
  const extended = events.filter(e => e.data?.length === 13);

  if (
    short.length !== rule.expectedShortCount ||
    extended.length !== rule.expectedExtendedCount
  ) {
    throw new Error(`REFUSING: unexpected Ship format counts`);
  }

  const updates = [];

  // Validate all short events before writing.
  for (const event of short) {
    const v = event.returnValues || {};

    if (
      ![0, 1].includes(v.emergencyAt) ||
      v.transitDeparture !== 0 ||
      v.transitOrigin !== null ||
      v.transitDestination !== null ||
      !event.data.slice(7).every(x => x === '0x0')
    ) {
      throw new Error(`REFUSING: unexpected short Ship at ${event._id}`);
    }

    if (Number.isNaN(v.transitArrival)) {
      updates.push({
        updateOne: {
          filter: { _id: event._id },
          update: { $set: { 'returnValues.transitArrival': 0 } }
        }
      });
    } else if (v.transitArrival !== 0) {
      throw new Error(`REFUSING: unexpected transitArrival at ${event._id}`);
    }
  }

  // Validate complete events without modifying them.
  for (const event of extended) {
    const v = event.returnValues || {};

    if (
      !['emergencyAt', 'transitDeparture', 'transitArrival']
        .every(field => Number.isFinite(v[field])) ||
      v.transitOrigin === undefined ||
      v.transitDestination === undefined
    ) {
      throw new Error(`REFUSING: invalid complete Ship at ${event._id}`);
    }
  }

  console.log(`V12 SHIP MIXED VERIFIED: ${events.length} events`);

  if (updates.length) {
    const result = await Event.collection.bulkWrite(updates, {
      ordered: true
    });

    if (result.matchedCount !== updates.length) {
      throw new Error('REFUSING: incomplete Ship mixed batch');
    }
  }

  console.log(`V12 SHIP MIXED COMPLETE: ${updates.length} events updated`);
  return updates.length;
}


module.exports = async function applyExceptions({ mongoose, from, to }) {
  const filename = `blocks_${from}_${to}.json`;
  const filepath = path.join(__dirname, filename);

  if (!fs.existsSync(filepath)) {
    console.log(`V12 EXCEPTIONS: No file for ${from}-${to}`);
    return;
  }

  const config = JSON.parse(fs.readFileSync(filepath, 'utf8'));
  const Event = mongoose.model('Event');
  let applied = 0;

  for (const rule of config.exceptions) {
    if (rule.type === 'suppressEvent') {
      applied += await suppressEvent({ Event, rule, from, to });
      continue;
    }
    if (rule.type === 'shipMixedBatch') {
      applied += await applyShipMixedBatch({ Event, rule, from, to });
      continue;
    }
    if (rule.type === 'shipBatch') {
      applied += await applyShipBatch({ Event, rule, from, to });
      continue;
    }
    if (rule.type === 'batch') {
      applied += await applyBatch({ Event, rule, from, to });
      continue;
    }
    if (rule.blockNumber < from || rule.blockNumber > to) {
      throw new Error(`Exception outside interval: ${rule.id}`);
    }

    for (const logIndex of rule.logIndexes) {
      const query = {
        event: rule.event,
        version: rule.version,
        blockNumber: rule.blockNumber,
        transactionHash: rule.transactionHash,
        logIndex
      };

      const matches = await Event.find(query).lean();

      if (matches.length !== 1) {
        throw new Error(
          `REFUSING: ${rule.id}, logIndex ${logIndex}: found ${matches.length} events`
        );
      }

      const event = matches[0];
      const updates = {};

      for (const [field, expected] of Object.entries(rule.expected)) {
        const key = field.replace(/^returnValues\./, '');
        const actual = event.returnValues?.[key];
        const replacement = rule.set[field];

        if (isExpected(actual, replacement)) {
          console.log(`ALREADY APPLIED: ${rule.id}, ${logIndex}, ${key}`);
          continue;
        }

        if (!isExpected(actual, expected)) {
          throw new Error(
            `REFUSING: unexpected ${key} in ${rule.id}, logIndex ${logIndex}`
          );
        }

        updates[field] = replacement;
      }

      if (Object.keys(updates).length) {
        const result = await Event.collection.updateOne(
          { _id: event._id, ...query },
          { $set: updates }
        );

        if (result.matchedCount !== 1) {
          throw new Error(`REFUSING: update failed for ${rule.id}`);
        }

        applied++;
        console.log(`APPLIED: ${rule.id}, logIndex ${logIndex}`);
      }
    }
  }

  console.log(`V12 EXCEPTIONS COMPLETE: ${applied} events updated`);
};
