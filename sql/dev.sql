-- The dev environment's tables: a schema "dev" next to "public" in the same
-- database, and a role that can reach nothing else. The dev service
-- (render.dev.yaml) logs in as blink_dev, so whatever it measures lands in
-- dev.sources / dev.detections / dev.tracks and never in the tables the live
-- site reads. Run once, as the database owner, after schema.sql and
-- roles.sql.
--
-- The application names its tables without a schema. blink_dev's search
-- path is "dev", so the same queries find the dev tables; and since the role
-- has no right on public.sources, public.detections or public.tracks, a
-- wrong search path gives "permission denied", not a write to production.

create schema if not exists dev;

-- Same columns, defaults, checks, indexes and identity as production.
create table if not exists dev.sources    (like public.sources    including all);
create table if not exists dev.detections (like public.detections including all);
create table if not exists dev.tracks     (like public.tracks     including all);

-- "like" does not copy foreign keys.
alter table dev.detections drop constraint if exists detections_source_id_fkey;
alter table dev.detections add constraint detections_source_id_fkey
    foreign key (source_id) references dev.sources (id) on delete cascade;
alter table dev.tracks drop constraint if exists tracks_source_id_fkey;
alter table dev.tracks add constraint tracks_source_id_fkey
    foreign key (source_id) references dev.sources (id) on delete cascade;

create or replace function dev.sources_near(p_ra double precision, p_dec double precision, p_radius_deg double precision)
returns setof dev.sources
language sql stable
set search_path = dev
as $$
    select s.*
    from sources s
    where s.dec_deg between p_dec - p_radius_deg and p_dec + p_radius_deg
      and 2 * degrees(asin(least(1, sqrt(
            power(sin(radians(s.dec_deg - p_dec) / 2), 2)
            + cos(radians(p_dec)) * cos(radians(s.dec_deg))
              * power(sin(radians(s.ra_deg - p_ra) / 2), 2))))) <= p_radius_deg;
$$;

alter table dev.sources    enable row level security;
alter table dev.detections enable row level security;
alter table dev.tracks     enable row level security;
revoke execute on function dev.sources_near(double precision, double precision, double precision) from public;

-- The dev service's role. Created without a password; the owner sets one
-- afterwards (alter role blink_dev password '...') and it lives only in the
-- dev service's DATABASE_URL.
do $$
begin
    if not exists (select from pg_roles where rolname = 'blink_dev') then
        create role blink_dev login;
    end if;
end $$;
alter role blink_dev set search_path = dev;

grant usage on schema dev to blink_dev;
grant select, insert, update, delete on dev.sources, dev.detections, dev.tracks to blink_dev;
grant execute on function dev.sources_near(double precision, double precision, double precision) to blink_dev;

drop policy if exists dev_all on dev.sources;
drop policy if exists dev_all on dev.detections;
drop policy if exists dev_all on dev.tracks;
create policy dev_all on dev.sources    for all to blink_dev using (true) with check (true);
create policy dev_all on dev.detections for all to blink_dev using (true) with check (true);
create policy dev_all on dev.tracks     for all to blink_dev using (true) with check (true);

-- A change to the tables (a new column, an index) goes to dev first:
--   set search_path = dev;
--   alter table detections add column if not exists ...;
-- and to public with the promotion to main (docs/environments.md).
