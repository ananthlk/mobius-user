-- Create mobius_user database
-- Run as superuser: psql -U postgres -f scripts/create_db.sql

SELECT 'CREATE DATABASE mobius_user'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'mobius_user')\gexec
