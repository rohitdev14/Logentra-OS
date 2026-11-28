# Reading contract

New readings carry the following metadata. Existing clients may continue to send the same payloads.

| Field | Meaning |
| --- | --- |
| `unit` | The unit recorded at write time. Omitting it snapshots the equipment parameter's current unit. A supplied unit must match the configured unit when one is set. A mismatch returns 422. The server performs no unit conversion. |
| `quality` | `good` (default), `uncertain`, `bad`, or `substituted`. |
| `source` | `manual`, `qr`, `import`, `api`, `mqtt`, `modbus`, or `demo` (sample data). The `modbus` value is reserved for a future edge connector. Webhook ingest defaults to `api`. |
| `idempotency_key` | Optional client-generated text unique to one equipment item, up to 1024 UTF-8 bytes. Reuse the same key and payload for a retry. A new reading needs a new key. |

Flat API reading rows expose these fields directly. Grouped log responses expose them in `readingMeta`, indexed by parameter key, alongside the existing numeric `readings` map. The server reading CSV preserves the original columns, including `source`, and appends `unit,quality,idempotency_key`. The browser fallback log CSV retains its existing `unit` column and appends `quality,source,idempotency_key`.

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

The expand migration adds nullable columns and backfills legacy rows. It stores the parameter unit available at upgrade time and trims and lowercases source values that match the allowed set after normalization. Older readings may carry legacy sources such as `webhook` and `load`, which remain unchanged in storage, reads, exports, and backups. CSV still applies its standard spreadsheet-safety escaping. Source validation applies to new writes only. Run the SQLite upgrade with one migrator. Downgrade removes the added columns and index and keeps the stored source values.
