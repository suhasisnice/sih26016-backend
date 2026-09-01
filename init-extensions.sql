-- Runs once, on first boot of the local database volume.
--
-- PostGIS is required: Parcel.geom is a Geometry(POINT, 4326), so the
-- schema will not create without it. On Supabase this must be enabled in
-- the dashboard (Database -> Extensions -> postgis) before the API first
-- starts, since create_all cannot add an extension.
CREATE EXTENSION IF NOT EXISTS postgis;
