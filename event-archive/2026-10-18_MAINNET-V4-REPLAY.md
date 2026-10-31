# Mainnet V4 Replay — 2026-10-18

V4 reconstruction completed in isolated MongoDB using original Influence handlers.

- Archive: 31 chunks covering blocks 218425–248999; 14,420 raw events.
- Parser: 14,365 parsed; 50 ignored; 5 unhandled; 0 failures.
- Replay: 14,365 processed; 0 pending.
- Asteroid ownership: 11,469 distinct minted IDs, 11,469 owners match latest processed on-chain transfer, 0 missing, 0 mismatches.
- Final database: 11,475 NFT components; 1,430 Name components; 12,001 entities; 11,481 activities; 12,925 indexitems; 2 constants; 1 Celestial and 1 Orbit component.
- Original 1,081 events reprocessed in V4; 546 component documents match V3 after excluding event references and database IDs.
- New event classes include 11,469 asteroid mint Transfers, 907 asteroid Name updates, 907 Unique events in namespace 0x3, and ASTEROID_MERKLE_ROOT registration.
- Archived asteroid mints represent sold NFTs, not all 250,000 possible physical asteroids.

The initial V4 JSONL export was truncated. The successful retry `mainnet-v4-import-retry.jsonl` was validated to contain all 14,365 valid, unique records and used for staging. Preserve the original V3 reconstruction and live Testnet database unchanged.

Open work: full physical asteroid state, optional handler side effects, historic crewmate pricing constant mismatch, restart-safe event processing and reproducible bootstrap from an empty isolated database.
