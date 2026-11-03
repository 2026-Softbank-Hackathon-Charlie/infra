\set ON_ERROR_STOP on
SELECT current_user = :'expected_user' AS correct_identity \gset
\if :correct_identity
\else
  SELECT 1 / 0 AS poc_assertion_failed;
\endif
SELECT NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
       AND NOT pg_has_role(current_user, :'owner_role', 'MEMBER') AS restricted
FROM pg_roles WHERE rolname = current_user \gset
\if :restricted
\else
  SELECT 1 / 0 AS poc_assertion_failed;
\endif
SELECT NOT has_database_privilege(current_user, :'other_db', 'CONNECT')
       AND NOT has_database_privilege(current_user, current_database(), 'CREATE')
       AND NOT has_database_privilege(current_user, current_database(), 'TEMP')
       AND NOT has_schema_privilege(current_user, 'app', 'CREATE') AS isolated \gset
\if :isolated
\else
  SELECT 1 / 0 AS poc_assertion_failed;
\endif
SELECT count(*) >= 1000 AS seeded FROM app.customers \gset
\if :seeded
\else
  SELECT 1 / 0 AS poc_assertion_failed;
\endif

BEGIN;
INSERT INTO app.customers(external_id, email, display_name)
VALUES ('runtime-transaction-probe', 'probe@example.invalid', 'Synthetic transaction probe')
RETURNING id AS first_id \gset
INSERT INTO app.orders(customer_id, amount_cents, status)
VALUES (:first_id, 1234, 'new');
UPDATE app.orders SET status = 'cancelled' WHERE customer_id = :first_id;
DELETE FROM app.orders WHERE customer_id = :first_id;
DELETE FROM app.customers WHERE id = :first_id;
INSERT INTO app.customers(external_id, email, display_name)
VALUES ('runtime-transaction-probe', 'probe@example.invalid', 'Synthetic sequence probe')
RETURNING id AS second_id \gset
SELECT :second_id > :first_id AS sequence_advanced \gset
\if :sequence_advanced
\else
  SELECT 1 / 0 AS poc_assertion_failed;
\endif
ROLLBACK;
SELECT NOT EXISTS (SELECT FROM app.customers WHERE external_id = 'runtime-transaction-probe') AS rolled_back \gset
\if :rolled_back
\else
  SELECT 1 / 0 AS poc_assertion_failed;
\endif
SELECT 'runtime-crud-rollback-sequence-and-privileges-passed' AS result;
