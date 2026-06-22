-- Initialize databases for metaseed-hub
-- This script runs on first postgres startup

-- Create keycloak database if it doesn't exist
SELECT 'CREATE DATABASE keycloak'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'keycloak')\gexec

-- Create the test database used by the pytest suite if it doesn't exist
SELECT 'CREATE DATABASE metaseed_hub_test'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'metaseed_hub_test')\gexec
