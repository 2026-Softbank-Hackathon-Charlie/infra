\set ON_ERROR_STOP on
-- Executed as the owner of this database only. No superuser/admin secret.
SELECT current_user = :'expected_user' AS correct_identity \gset
\if :correct_identity
\else
  SELECT 1 / 0 AS poc_assertion_failed;
\endif
BEGIN;
REVOKE ALL ON DATABASE :"DBNAME" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"DBNAME" TO :"runtime_role";
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS app AUTHORIZATION :"expected_user";
REVOKE ALL ON SCHEMA app FROM PUBLIC;
GRANT USAGE ON SCHEMA app TO :"runtime_role";

CREATE TABLE IF NOT EXISTS app.customers (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    external_id text NOT NULL UNIQUE,
    email text NOT NULL,
    display_name text NOT NULL
);
CREATE TABLE IF NOT EXISTS app.orders (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id bigint NOT NULL REFERENCES app.customers(id),
    amount_cents integer NOT NULL CHECK (amount_cents >= 0),
    status text NOT NULL CHECK (status IN ('new', 'paid', 'cancelled'))
);
CREATE TABLE IF NOT EXISTS app.poc_markers (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    label text NOT NULL UNIQUE,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app TO :"runtime_role";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA app TO :"runtime_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"expected_user" IN SCHEMA app
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"runtime_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"expected_user" IN SCHEMA app
    GRANT USAGE, SELECT ON SEQUENCES TO :"runtime_role";

-- Synthetic fixture only; .invalid is intentionally not a real email domain.
INSERT INTO app.customers (external_id, email, display_name)
SELECT 'fixture-' || n, 'customer-' || n || '@example.invalid', 'Synthetic customer ' || n
FROM generate_series(1, 1000) AS n ON CONFLICT (external_id) DO NOTHING;
INSERT INTO app.orders (customer_id, amount_cents, status)
SELECT id, 1000 + (id % 100) * 10, 'paid' FROM app.customers
WHERE external_id LIKE 'fixture-%'
AND NOT EXISTS (SELECT FROM app.orders);
INSERT INTO app.poc_markers(label) VALUES ('seed-complete') ON CONFLICT (label) DO NOTHING;
COMMIT;
SELECT pg_database_size(current_database()) <= 536870912 AS bounded_fixture \gset
\if :bounded_fixture
\else
  SELECT 1 / 0 AS poc_assertion_failed;
\endif
SELECT 'migration-and-synthetic-seed-passed' AS result;
