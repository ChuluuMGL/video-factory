#!/bin/sh
set -eu
# All values are read from secret files. Nothing uses shell-expanded SQL.
psql -v ON_ERROR_STOP=1 --username postgres --dbname postgres <<'SQL'
SELECT format('CREATE ROLE vf_runtime LOGIN PASSWORD %L', trim(pg_read_file('/run/secrets/product_db_password'))) \gexec
SELECT format('CREATE ROLE vf_n8n LOGIN PASSWORD %L', trim(pg_read_file('/run/secrets/n8n_db_password'))) \gexec
CREATE DATABASE vf_runtime OWNER vf_runtime;
CREATE DATABASE vf_n8n OWNER vf_n8n;
REVOKE CONNECT ON DATABASE vf_runtime FROM PUBLIC;
REVOKE CONNECT ON DATABASE vf_n8n FROM PUBLIC;
GRANT CONNECT ON DATABASE vf_runtime TO vf_runtime;
GRANT CONNECT ON DATABASE vf_n8n TO vf_n8n;
SQL
