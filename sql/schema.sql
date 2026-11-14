-- Vanilla Sphere: shared tables for measured results (not images).
-- Images are cut on demand from the NASA S3 mirror and cached by the web
-- service; this database only stores what people measure on top of them:
-- which objects we follow, where they were seen in each exposure, and the
-- fitted motion. Run once on a fresh Postgres 15+ database.

create table if not exists sources (
    id              bigint generated always as identity primary key,
    name            text not null unique,          -- "WISE 0855-0714", "cand-0007", ...
    kind            text not null default 'candidate'
                    check (kind in ('star', 'brown_dwarf', 'variable', 'moving', 'candidate', 'artifact')),
    ra_deg          double precision not null check (ra_deg >= 0 and ra_deg < 360),
    dec_deg         double precision not null check (dec_deg between -90 and 90),
    ref_epoch       double precision not null default 2016.0,   -- Julian year of ra/dec
    pmra_masyr      double precision,              -- mu_alpha * cos(dec)
    pmdec_masyr     double precision,
    parallax_mas    double precision,
    gaia_source_id  bigint,
    catwise_id      text,
    note            text,
    created_by      text not null default current_user,
    created_at      timestamptz not null default now()
);
create index if not exists sources_dec_ra on sources (dec_deg, ra_deg);

-- One row per object per SPHEREx exposure (obs_id + detector identify the file).
create table if not exists detections (
    id              bigint generated always as identity primary key,
    source_id       bigint not null references sources (id) on delete cascade,
    collection      text not null default 'spherex_dr1',
    obs_id          text not null,
    detector        smallint not null check (detector between 1 and 6),
    mjd             double precision not null,
    ra_deg          double precision not null,
    dec_deg         double precision not null,
    pos_err_arcsec  double precision,
    wave_um         double precision,              -- wavelength at the object's pixel
    flux_ujy        double precision,
    flux_err_ujy    double precision,
    flags           text[] not null default '{}',  -- SPHEREx flag names at the position
    method          text not null default 'centroid',
    created_by      text not null default current_user,
    created_at      timestamptz not null default now(),
    unique (source_id, obs_id, detector, method)
);
create index if not exists detections_source_mjd on detections (source_id, mjd);
create index if not exists detections_dec_ra on detections (dec_deg, ra_deg);

-- Linear motion fitted to a source's detections:
--   ra(t)  = ra0_deg  + pmra_masyr  * (t - epoch) / 3.6e6 / cos(dec0)
--   dec(t) = dec0_deg + pmdec_masyr * (t - epoch) / 3.6e6      (t in Julian years)
create table if not exists tracks (
    source_id       bigint primary key references sources (id) on delete cascade,
    epoch           double precision not null,
    ra0_deg         double precision not null,
    dec0_deg        double precision not null,
    pmra_masyr      double precision not null,
    pmdec_masyr     double precision not null,
    pmra_err        double precision,
    pmdec_err       double precision,
    n_detections    integer not null,
    rms_arcsec      double precision,
    mjd_min         double precision,
    mjd_max         double precision,
    updated_at      timestamptz not null default now()
);

-- Everything within radius_deg of a position (box prefilter on the index,
-- exact great-circle test after).
create or replace function sources_near(p_ra double precision, p_dec double precision, p_radius_deg double precision)
returns setof sources
language sql stable
set search_path = public
as $$
    select s.*
    from sources s
    where s.dec_deg between p_dec - p_radius_deg and p_dec + p_radius_deg
      and 2 * degrees(asin(least(1, sqrt(
            power(sin(radians(s.dec_deg - p_dec) / 2), 2)
            + cos(radians(p_dec)) * cos(radians(s.dec_deg))
              * power(sin(radians(s.ra_deg - p_ra) / 2), 2))))) <= p_radius_deg;
$$;

-- Closed to the public REST API: only database roles that log in directly
-- (the web service and the people loading measurements) can touch the data.
alter table sources    enable row level security;
alter table detections enable row level security;
alter table tracks     enable row level security;
revoke execute on function sources_near(double precision, double precision, double precision) from public;
