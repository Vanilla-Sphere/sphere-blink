-- Vanilla Sphere: limits that are changed while the server runs.
--
-- How many jobs the pipeline works on at a time, how much memory their
-- frames may take and how much one caller may ask for. The web service reads
-- this table twice a minute (sphereblink/settings.py), so a changed value is
-- in force within the minute, without a deploy:
--
--   update settings set value = 6 where key = 'active_views';
--
-- or change the cell `value` in the table editor. `GET /api/settings` shows
-- what is in force. To go back to the value of the service's environment,
-- delete the row.
--
-- min_value and max_value are the range the service accepts (the same
-- numbers as in settings.py, which is what counts: a value outside the range
-- written there is moved to its nearest end). The check below only makes the
-- database refuse such a value at once, where the person typing it sees it.
-- The values are those of production (render.yaml). Run after schema.sql;
-- running it again keeps the values and renews ranges and descriptions.

create table if not exists settings (
    key         text primary key,
    value       double precision not null,       -- a number; 0 or 1 for a switch
    min_value   double precision not null,
    max_value   double precision not null,
    description text not null default '',
    updated_at  timestamptz not null default now(),
    constraint settings_value_in_range check (value >= min_value and value <= max_value)
);

alter table settings enable row level security;

create or replace function settings_touch() returns trigger
language plpgsql
set search_path = ''
as $$
begin
    new.updated_at := now();
    return new;
end
$$;

create or replace trigger settings_touch before update on settings
    for each row execute function settings_touch();

insert into settings (key, value, min_value, max_value, description) values
    -- the pipeline queue
    ('active_views',                2,    1,      8, 'Views whose exposures are cut at the same time; the next ones wait in the queue'),
    ('active_mb',                 300,  300,   4000, 'Memory (MB) the views being cut may fill with frames together; a wide field that does not fit waits'),
    ('measure_procs',               1,    1,      4, 'Measurements running at the same time; the next ones wait in the queue'),
    ('queue_preview',               1,    0,      1, '1: a view in the queue gets its first four exposures at once; 0: nothing before its turn'),
    ('ram_budget_mb',             300,  300,   4000, 'Memory (MB) for the frames of views already gathered; above it the views opened longest ago are reloaded from disk'),
    -- how much may wait
    ('max_jobs',                   96,    8,    400, 'Views gathered or waiting at once, all callers together; more are refused (503)'),
    ('max_jobs_per_client',        24,    4,     96, 'Views gathered or waiting at once for one caller; more are refused (429)'),
    ('max_fields_per_client',       8,    1,     32, 'Wide fields loading at once for one caller'),
    ('max_measure_queue',          12,    2,     48, 'Measurements waiting or running, all callers together; more are refused (503)'),
    ('max_open_requests',          32,    8,    128, 'API calls being answered at once for one caller'),
    -- how much one caller may ask for
    ('rate_requests_per_s',        40,    5,    400, 'API calls per second for one caller, sustained'),
    ('rate_requests_burst',      1200,  100,  12000, 'API calls one caller may make at once'),
    ('rate_names_per_min',         40,    5,    600, 'Names per minute one caller may send to the name services'),
    ('rate_catalogs_per_min',      60,    5,    600, 'Catalogue and archive queries per minute that go out for one caller'),
    ('rate_tiles_per_s',          100,   10,   1000, 'Tiles of the sky photograph per second for one caller, sustained'),
    ('rate_tiles_burst',         3000,  200,  30000, 'Tiles of the sky photograph one caller may ask for at once'),
    ('rate_atlas_per_min',       1200,   60,   6000, 'Sky tiles per minute the server fetches from the archive for one caller'),
    ('rate_measures_per_hour',     30,    1,    600, 'New measurements per hour for one caller, sustained'),
    ('rate_measures_burst',         6,    1,     60, 'New measurements one caller may start at once')
on conflict (key) do update
    set min_value = excluded.min_value, max_value = excluded.max_value, description = excluded.description;
