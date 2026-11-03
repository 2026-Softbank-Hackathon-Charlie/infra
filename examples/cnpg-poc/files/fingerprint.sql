\set ON_ERROR_STOP on
-- Only counts/hashes of synthetic fixtures leave the Pod; never dump rows.
SELECT 'customers' AS relation, count(*) AS rows,
       md5(string_agg(md5(row_to_json(t)::text), '' ORDER BY id)) AS fingerprint
FROM app.customers AS t;
SELECT 'orders' AS relation, count(*) AS rows,
       md5(string_agg(md5(row_to_json(t)::text), '' ORDER BY id)) AS fingerprint
FROM app.orders AS t;
SELECT 'markers' AS relation, count(*) AS rows,
       md5(string_agg(md5(row_to_json(t)::text), '' ORDER BY id)) AS fingerprint
FROM app.poc_markers AS t;
SELECT id, label, recorded_at AS latest_marker_time
FROM app.poc_markers ORDER BY id DESC LIMIT 1;
