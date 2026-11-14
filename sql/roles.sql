-- Two login roles, created without a password (so nobody can log in yet).
-- The database owner sets each password afterwards and shares it privately,
-- never in a chat group or a commit:
--   alter role blink_loader password '...';
--   alter role blink_web    password '...';

create role blink_loader login;   -- loads measurements (read + write on the three tables)
create role blink_web    login;   -- web service, read only

grant usage on schema public to blink_loader, blink_web;
grant select, insert, update, delete on sources, detections, tracks to blink_loader;
grant select on sources, detections, tracks to blink_web;
grant execute on function sources_near(double precision, double precision, double precision) to blink_loader, blink_web;

create policy loader_all on sources    for all to blink_loader using (true) with check (true);
create policy loader_all on detections for all to blink_loader using (true) with check (true);
create policy loader_all on tracks     for all to blink_loader using (true) with check (true);
create policy web_read on sources    for select to blink_web using (true);
create policy web_read on detections for select to blink_web using (true);
create policy web_read on tracks     for select to blink_web using (true);
