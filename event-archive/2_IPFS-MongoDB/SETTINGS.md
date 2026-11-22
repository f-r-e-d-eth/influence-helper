# Adalaris v0.3 settings

`~/Adalaris/settings.json` is created automatically if absent. Edit it between builds; each new build pins its own settings inside `result.json`, and a paused build uses the pinned settings. `_comment` strings are informational, not JSON syntax comments. Unknown keys or invalid values are rejected. Existing user settings are never overwritten.

| Setting | Default | Meaning |
| --- | --- | --- |
| `checkpoint.enabled` | true | Create intermediate recovery archives. |
| `checkpoint.interval_blocks` | 200000 | Full scan and recovery archive every two 100k deltas; positive multiple of 100000. Change to 1000000 after testing if desired. |
| `checkpoint.automatic_storage_check` | true | Skip an intermediate archive when estimated free space is insufficient. If false, the user assumes responsibility for available space. |
| `checkpoint.minimum_free_gib` | 10 | Workspace reserve in GiB (positive integer). |
| `checkpoint.keep_checkpoints` | 1 | Retain 1–10 most recent intermediate recovery archives until final export. |
| `verification.verify_every_delta` | false | Full target scan after every delta instead of only scheduled intervals and final target. |
| `verification.verify_checkpoints` | true | Mandatory for trusted recovery archives; false is rejected. |
| `verification.verify_final_database` | true | Mandatory for verified final export; false is rejected. |
| `download.prefetch_deltas` | 1 | Waiting packages including the in-flight download (1–8), besides the package being applied. |
| `download.max_retries` | 3 | Total download attempts per package (1–10). |

The first source and the first source after pause/resume are also fingerprinted. Every delta is validated against the pinned manifest, package integrity, boundary, checkpoint continuity, metadata/index and operation counts. Deferred journals explicitly say `applied_verification_deferred`. The benchmark checkbox is separate and creates no verified recovery archives.

Recovery archives use native MongoDB gzip dumps, are gzip-checked and hashed, and are recorded in `result.json` before an older archive is removed. The free-space estimate reserves twice the current logical database size (at least 64 MiB), upcoming package space, and the configured reserve; this is an estimate on the build filesystem, not a guarantee or a check of a separately mounted Docker volume. Keep sufficient space on Docker's filesystem as well. Scheduled fingerprint scans still run if the archive is skipped. The final export replaces intermediate archives only after it is successfully saved. A failed build retains the last recovery archive and working database for inspection. Automatic restore/retry is not yet implemented. Resume old v0.2 jobs using the archived v0.2 app.
