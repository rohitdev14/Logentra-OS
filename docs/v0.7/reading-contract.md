# Reading contract

New readings carry the following metadata. Existing clients may continue to send the same payloads.

| Field | Meaning |
| --- | --- |
| `unit` | The unit recorded at write time. Omitting it snapshots the equipment parameter's current unit. A supplied unit must match the configured unit when one is set. A mismatch returns 422. The server performs no unit conversion. |
| `quality` | `good` (default), `uncertain`, `bad`, or `substituted`. |
| `source` | `manual`, `qr`, `import`, `api`, `mqtt`, or `modbus`. The last value is reserved for a future edge connector. |
| `idempotency_key` | Optional client-generated text unique to one equipment item, up to 1024 UTF-8 bytes. Reuse the same key and payload for a retry. A new reading needs a new key. |

Flat API reading rows expose these fields directly. Grouped log responses expose them in `readingMeta`, indexed by parameter key, alongside the existing numeric `readings` map. CSV exports preserve the original columns, including the existing `source` column, and append `unit,quality,source,idempotency_key`.

## Retrying an API write

In user-authenticated deployments, include a bearer session token or a user-bound API key. Run this command twice with the same equipment and payload.

```bash
curl -i 'http://127.0.0.1:8787/api/readings' \
  -H 'Content-Type: application/json' \
  -d '{"tag":"P-101","parameter":"flow","value":52,"quality":"good","idempotency_key":"gateway-42"}'
```

| Request | Outcome |
| --- | --- |
| New key on that equipment | Inserts the reading. The single-reading endpoint returns 201, `duplicate: false`, and the `reading`. |
| Same key and payload | Returns the existing reading with 200 and `duplicate: true`. |
| Same key and different payload | Returns 409. A conflicting bulk request rolls back the batch. |
| Same key on another equipment item | Inserts independently. |

Bulk results list keyed readings in request order. The first identical occurrence wins and subsequent copies are flagged as duplicates. Server-generated values such as a timestamp for a request that omitted it are outside retry identity. Requests without a key retain their existing insert and explicit reading-ID behavior.

The expand migration adds nullable columns and backfills legacy rows. It normalizes old sources and stores the parameter unit available at upgrade time. Run the SQLite upgrade with one migrator. Downgrade removes the added columns and index, while normalized legacy source values remain.
