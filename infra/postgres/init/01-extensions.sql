-- Extensions required by KRB ERP. Runs once on first cluster initialisation.
-- Alembic also guards these with CREATE EXTENSION IF NOT EXISTS so that a
-- pre-existing database (managed Postgres, restored backup) is handled too.

CREATE EXTENSION IF NOT EXISTS postgis;          -- site geofencing, delivery points
CREATE EXTENSION IF NOT EXISTS pg_trgm;          -- trigram search on names, codes, plates
CREATE EXTENSION IF NOT EXISTS btree_gist;       -- exclusion constraints on effective-dated rates
CREATE EXTENSION IF NOT EXISTS citext;           -- case-insensitive emails and codes
CREATE EXTENSION IF NOT EXISTS ltree;            -- account and category trees
CREATE EXTENSION IF NOT EXISTS pgcrypto;         -- digest() for checksums
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
