\set ON_ERROR_STOP on
-- A collision fails: never misreport an old marker's timestamp as a new commit.
INSERT INTO app.poc_markers(label) VALUES (:'marker_label')
RETURNING id, label, recorded_at;
