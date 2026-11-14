# Measurement database

The web service never stores images here. It cuts them on demand from the
NASA S3 mirror and keeps its own cache. This Postgres database holds only
what we measure on top of the images:

| table        | one row per                         | key columns                                         |
|--------------|-------------------------------------|-----------------------------------------------------|
| `sources`    | object we follow                    | `name`, `kind`, `ra_deg`, `dec_deg`, proper motion  |
| `detections` | object per SPHEREx exposure         | `source_id`, `obs_id`, `detector`, `mjd`, position, flux |
| `tracks`     | object (fitted linear motion)       | `source_id`, `epoch`, `ra0_deg`, `dec0_deg`, `pmra_masyr`, `pmdec_masyr` |

`sources_near(ra, dec, radius_deg)` returns the sources inside a cone.

Most rows are written by the web service itself: its measurement pipeline
(`sphereblink/measure.py`, `GET /api/measure`) measures an object in every
exposure, fits the track and upserts all three tables. It owns the rows
whose `method` is `psf`, `wings` or `stack` and replaces them when it
measures an object again. Measurements made another way can be loaded next
to them under a different `method`.

## Files

- `schema.sql`: tables, indexes, cone-search function, row level security.
- `settings.sql`: the table `settings` (below), with the values of production.
- `roles.sql`: `blink_loader` (read + write), `blink_web` (read only) and
  `blink_service` (the web service: read + write on the three tables, read
  only on `settings`).
- `example_insert.sql`: copy-paste upserts for the three tables.

## Settings

One more table is not a measurement: `settings` holds the limits of the web
service that are changed while it runs (how many views and measurements at a
time, memory for frames, allowances per caller). The service reads it every
30 seconds and only reads it; the owner of the database changes it:

    update settings set value = 6 where key = 'active_views';

| column | meaning |
|--------|---------|
| `key` | the setting (`active_views`, `active_mb`, `measure_procs`, ...) |
| `value` | a number; 0 or 1 for a switch |
| `min_value`, `max_value` | the range the service accepts; the table refuses a value outside it |
| `description` | what the setting limits |
| `updated_at` | set by the database at every change |

Deleting a row puts the setting back to the value of the service's
environment. What is in force: `GET /api/settings`. The rules (ranges, the
memory rule, what happens without the database) are in
[docs/deployment.md](../docs/deployment.md#settings-in-the-database).

## Loading data

Connect with the `blink_loader` role (ask the database owner for the
connection string) and run upserts like the ones in `example_insert.sql`:

    psql "$BLINK_LOADER_URL" -f sql/example_insert.sql

From Python:

    import psycopg
    with psycopg.connect(os.environ["BLINK_LOADER_URL"]) as conn:
        conn.execute(
            "insert into sources (name, kind, ra_deg, dec_deg) values (%s, %s, %s, %s) "
            "on conflict (name) do nothing",
            ("cand-0001", "candidate", 133.7951, -7.2451),
        )

`obs_id` and `detector` identify the exposure. Get them for any position from
the web service: `GET /api/exposures?ra=133.7951&dec=-7.2451`.

Units: degrees (ICRS) for positions, mas/yr for proper motion
(`pmra_masyr` already includes cos(dec)), MJD for times, microns for
wavelength, microjansky for flux.
