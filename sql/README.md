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

## Files

- `schema.sql`: tables, indexes, cone-search function, row level security.
- `roles.sql`: `blink_loader` (read + write) and `blink_web` (read only).
- `example_insert.sql`: copy-paste upserts for the three tables.

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
