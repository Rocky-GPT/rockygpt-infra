\set ON_ERROR_STOP on

DO $$
BEGIN
  IF NOT has_schema_privilege('rockygpt_data', 'rockygpt_v2', 'USAGE') THEN
    RAISE EXCEPTION 'data role cannot use its schema';
  END IF;
  IF has_schema_privilege('rockygpt_data', 'rockygpt_brain', 'USAGE') THEN
    RAISE EXCEPTION 'data role can use brain schema';
  END IF;
  IF NOT has_schema_privilege('rockygpt_brain', 'rockygpt_brain', 'USAGE') THEN
    RAISE EXCEPTION 'brain role cannot use its schema';
  END IF;
  IF has_schema_privilege('rockygpt_brain', 'rockygpt_v2', 'USAGE') THEN
    RAISE EXCEPTION 'brain role can use data schema';
  END IF;
END
$$;

SELECT 'database role isolation: passed' AS result;
