-- Profile the official load-harness paths on PostgreSQL.
-- Enable pg_stat_statements (compose sets shared_preload_libraries) then:
--   psql "$LOGENTRA_DATABASE_URL" -f scripts/explain_hot_paths.sql
--   SELECT query, calls, mean_exec_time, total_exec_time
--     FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 20;

CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

EXPLAIN (ANALYZE, BUFFERS)
SELECT equipment.id
  FROM equipment
 WHERE equipment.tag ILIKE '%E-02500%'
    OR equipment.id ILIKE '%E-02500%'
    OR EXISTS (
        SELECT 1 FROM equipment_field_values
         WHERE equipment_field_values.equipment_id = equipment.id
           AND equipment_field_values.value ILIKE '%E-02500%'
    )
 ORDER BY equipment.tag, equipment.id
 LIMIT 50;

EXPLAIN (ANALYZE, BUFFERS)
SELECT count(equipment.id)
  FROM equipment;

EXPLAIN (ANALYZE, BUFFERS)
SELECT readings.id
  FROM readings
 WHERE readings.equipment_id = 'eq_lt_02500'
   AND readings.parameter_key IN ('vibration')
   AND readings.ts >= '2026-10-01T00:00:00'
   AND readings.ts <= '2026-10-31T23:59:59'
 ORDER BY readings.ts, readings.id
 LIMIT 200;

EXPLAIN (ANALYZE, BUFFERS)
SELECT readings.id
  FROM readings
 WHERE readings.out_of_range = 1
 ORDER BY readings.ts DESC, readings.id DESC
 LIMIT 50;

EXPLAIN (ANALYZE, BUFFERS)
SELECT work_orders.id
  FROM work_orders
 ORDER BY work_orders.created_at DESC, work_orders.id DESC
 LIMIT 50;
