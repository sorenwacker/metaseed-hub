-- Initialize databases for metaseed-hub
-- This script runs on first postgres startup

-- Create keycloak database if it doesn't exist
SELECT 'CREATE DATABASE keycloak'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'keycloak')\gexec
