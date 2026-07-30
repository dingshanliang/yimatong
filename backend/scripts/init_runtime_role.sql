-- Development and CI only.
-- Production must provision a restricted runtime role with managed secrets;
-- never execute this file against a production database.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'yimatong_app') THEN
        CREATE ROLE yimatong_app
            LOGIN
            PASSWORD 'yimatong_app'
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOINHERIT
            NOBYPASSRLS;
    ELSE
        ALTER ROLE yimatong_app
            WITH LOGIN
            PASSWORD 'yimatong_app'
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOINHERIT
            NOBYPASSRLS;
    END IF;
END
$$;

GRANT CONNECT ON DATABASE yimatong_dev TO yimatong_app;
GRANT USAGE ON SCHEMA public TO yimatong_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO yimatong_app;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO yimatong_app;

ALTER DEFAULT PRIVILEGES FOR ROLE yimatong IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO yimatong_app;
ALTER DEFAULT PRIVILEGES FOR ROLE yimatong IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO yimatong_app;
