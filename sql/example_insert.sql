-- How to load measurements. Safe to re-run: every statement is an upsert.

-- 1. Register the object (once).
insert into sources (name, kind, ra_deg, dec_deg, ref_epoch, pmra_masyr, pmdec_masyr, parallax_mas, note)
values ('WISE 0855-0714', 'brown_dwarf', 133.795129, -7.245147, 2016.0, -8118.0, 680.0, 438.9, 'challenge test target')
on conflict (name) do update
set ra_deg = excluded.ra_deg, dec_deg = excluded.dec_deg, ref_epoch = excluded.ref_epoch,
    pmra_masyr = excluded.pmra_masyr, pmdec_masyr = excluded.pmdec_masyr,
    parallax_mas = excluded.parallax_mas, note = excluded.note;

-- 2. One row per exposure where it was measured. obs_id and detector come
--    from GET /api/exposures?ra=..&dec=.. (fields "obs_id" and "detector").
insert into detections (source_id, obs_id, detector, mjd, ra_deg, dec_deg, pos_err_arcsec,
                        wave_um, flux_ujy, flux_err_ujy, flags, method)
select s.id, v.obs_id, v.detector, v.mjd, v.ra_deg, v.dec_deg, v.pos_err_arcsec,
       v.wave_um, v.flux_ujy, v.flux_err_ujy, v.flags, 'centroid'
from sources s,
     (values
        ('2025W23_2B_0123_1', 6, 60832.4512, 133.78361, -7.24492, 1.2, 4.62, 310.0, 45.0, '{}'::text[]),
        ('2025W49_1A_0457_2', 6, 61014.1034, 133.78248, -7.24483, 1.4, 4.71, 295.0, 52.0, '{SOURCE}'::text[])
     ) as v (obs_id, detector, mjd, ra_deg, dec_deg, pos_err_arcsec, wave_um, flux_ujy, flux_err_ujy, flags)
where s.name = 'WISE 0855-0714'
on conflict (source_id, obs_id, detector, method) do update
set mjd = excluded.mjd, ra_deg = excluded.ra_deg, dec_deg = excluded.dec_deg,
    pos_err_arcsec = excluded.pos_err_arcsec, wave_um = excluded.wave_um,
    flux_ujy = excluded.flux_ujy, flux_err_ujy = excluded.flux_err_ujy, flags = excluded.flags;

-- 3. The fitted motion (one row per object).
insert into tracks (source_id, epoch, ra0_deg, dec0_deg, pmra_masyr, pmdec_masyr,
                    pmra_err, pmdec_err, n_detections, rms_arcsec, mjd_min, mjd_max)
select id, 2025.9, 133.78300, -7.24488, -8105.0, 672.0, 60.0, 55.0, 2, 0.9, 60832.4512, 61014.1034
from sources where name = 'WISE 0855-0714'
on conflict (source_id) do update
set epoch = excluded.epoch, ra0_deg = excluded.ra0_deg, dec0_deg = excluded.dec0_deg,
    pmra_masyr = excluded.pmra_masyr, pmdec_masyr = excluded.pmdec_masyr,
    pmra_err = excluded.pmra_err, pmdec_err = excluded.pmdec_err,
    n_detections = excluded.n_detections, rms_arcsec = excluded.rms_arcsec,
    mjd_min = excluded.mjd_min, mjd_max = excluded.mjd_max, updated_at = now();
