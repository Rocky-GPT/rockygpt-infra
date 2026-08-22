-- Local-only least-privilege roles. Production should use distinct managed
-- database credentials with the same ownership boundaries.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

REVOKE ALL ON SCHEMA public FROM PUBLIC;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rockygpt_data') THEN
    CREATE ROLE rockygpt_data LOGIN PASSWORD 'rockygpt_data';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rockygpt_brain') THEN
    CREATE ROLE rockygpt_brain LOGIN PASSWORD 'rockygpt_brain';
  END IF;
END
$$;

GRANT CONNECT ON DATABASE rockygpt TO rockygpt_data, rockygpt_brain;

CREATE SCHEMA IF NOT EXISTS rockygpt_v2 AUTHORIZATION rockygpt_data;
CREATE SCHEMA IF NOT EXISTS rockygpt_brain AUTHORIZATION rockygpt_brain;

REVOKE ALL ON SCHEMA rockygpt_v2 FROM PUBLIC;
REVOKE ALL ON SCHEMA rockygpt_brain FROM PUBLIC;

GRANT USAGE, CREATE ON SCHEMA rockygpt_v2 TO rockygpt_data;
GRANT USAGE, CREATE ON SCHEMA rockygpt_brain TO rockygpt_brain;
