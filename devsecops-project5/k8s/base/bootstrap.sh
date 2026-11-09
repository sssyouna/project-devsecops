#!/bin/sh
# Sourced by the official entrypoint as UID 70, on a new database only.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv app_password INVENTORY_APP_PASSWORD
CREATE ROLE inventory_owner NOLOGIN;
CREATE ROLE inventory_app LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE TABLE public.inventory (
 id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 sku varchar(40) UNIQUE NOT NULL,
 name varchar(100) NOT NULL,
 quantity integer NOT NULL CHECK (quantity >= 0),
 location varchar(80) NOT NULL
);
CREATE TABLE public.app_metadata (key text PRIMARY KEY, value text NOT NULL);
INSERT INTO public.app_metadata VALUES ('seeded', 'true');
INSERT INTO public.inventory (sku,name,quantity,location) VALUES
 ('DEMO-001','Demo laptop',12,'Fictional office A'),
 ('DEMO-002','Demo monitor',24,'Fictional office B'),
 ('DEMO-003','Demo keyboard',36,'Fictional storeroom');
ALTER TABLE public.inventory OWNER TO inventory_owner;
ALTER TABLE public.app_metadata OWNER TO inventory_owner;
ALTER SCHEMA public OWNER TO inventory_owner;
ALTER DATABASE inventory OWNER TO inventory_owner;
REVOKE ALL ON DATABASE inventory FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE inventory TO inventory_app;
GRANT USAGE ON SCHEMA public TO inventory_app;
GRANT SELECT,INSERT,UPDATE,DELETE ON public.inventory TO inventory_app;
GRANT USAGE ON SEQUENCE public.inventory_id_seq TO inventory_app;
SQL
