-- Proves the policies in supabase_schema.sql on a real Postgres. Run: db/test_rls.sh
-- Supabase provides auth.uid() and the authenticated role; plain Postgres gets small equivalents here.
\set ON_ERROR_STOP on
create schema auth;
create function auth.uid() returns uuid language sql stable as $$ select nullif(current_setting('test.uid', true), '')::uuid $$;
create role authenticated nologin;
\i /sql/supabase_schema.sql
grant usage on schema public, auth to authenticated;
grant select on all tables in schema public to authenticated;
grant update on claims to authenticated;
grant execute on all functions in schema public, auth to authenticated;

insert into projects (name, as_of, last_day) overriding system value select 'P', '2026-10-30', '2026-11-15';
insert into projects (name, as_of, last_day) overriding system value select 'Other', '2026-10-30', '2026-11-15';
insert into members (project_id, user_id, name, role, clearances) values
  (1, '00000000-0000-0000-0000-000000000001', 'Meera', 'successor', '{public,tech-team}'),
  (1, '00000000-0000-0000-0000-000000000002', 'Sana',  'reviewer',  '{public,tech-team,core-committee}'),
  (1, '00000000-0000-0000-0000-000000000003', 'New',   'teammate',  '{public}'),
  (2, '00000000-0000-0000-0000-000000000004', 'Stranger', 'reviewer', '{public,tech-team,core-committee}');
insert into sources (project_id, code, type, title, audience, authorised_by) values
  (1, 'GH', 'github', 'Repo', 'public', 'x'), (1, 'WA', 'whatsapp', 'Chat', 'tech-team', 'x'), (1, 'N3', 'notes', 'Minutes', 'core-committee', 'x');
insert into evidence_units (project_id, source_id, audience, uid, seq, author, body, sensitivity) values
  (1, 1, 'public', 'GH-1', 0, 'a', 'public record', '{}'),
  (1, 2, 'tech-team', 'WA-1', 0, 'a', 'team record', '{}'),
  (1, 2, 'tech-team', 'WA-2', 1, 'a', 'knee surgery', '{health}'),
  (1, 3, 'core-committee', 'N3-1', 0, 'a', 'committee record', '{}');
insert into claims (project_id, code, type, statement, audience, sensitive) values
  (1, 'C1', 'event', 'public fact', 'public', false),
  (1, 'C2', 'risk', 'team fact', 'tech-team', false),
  (1, 'C3', 'event', 'team sensitive fact', 'tech-team', true),
  (1, 'C4', 'event', 'committee fact', 'core-committee', false);
insert into claim_evidence (claim_id, unit_id, quote, match) values (1, 1, 'q1', 'exact'), (2, 2, 'q2', 'exact'), (3, 3, 'q3', 'exact'), (4, 4, 'q4', 'exact');
insert into versions (project_id, number, approved_by, snapshot, content_hash) values (1, 1, 'Sana', '{"claims": [
  {"code": "C1", "audience": "public", "sensitive": false}, {"code": "C2", "audience": "tech-team", "sensitive": false},
  {"code": "C3", "audience": "tech-team", "sensitive": true}, {"code": "C4", "audience": "core-committee", "sensitive": false}]}', 'h');
insert into audit_events (project_id, actor, action) values (1, 'Sana', 'approved');

create function check_eq(what text, actual text, expected text) returns void language plpgsql as $$
  begin
    if actual is distinct from expected then raise exception 'FAILED %: got %, expected %', what, actual, expected; end if;
    raise notice 'ok: %', what;
  end $$;
grant execute on function check_eq to authenticated;

set role authenticated;

-- The successor: tech team and public, nothing sensitive, no committee.
set test.uid = '00000000-0000-0000-0000-000000000001';
select check_eq('successor claims', (select string_agg(code, ',' order by code) from claims), 'C1,C2');
select check_eq('successor records', (select string_agg(uid, ',' order by uid) from evidence_units), 'GH-1,WA-1');
select check_eq('successor receipts', (select string_agg(quote, ',' order by quote) from claim_evidence), 'q1,q2');
select check_eq('successor sources', (select string_agg(code, ',' order by code) from sources), 'GH,WA');
select check_eq('successor cannot read the raw snapshot', (select count(*)::text from versions), '0');
select check_eq('successor cannot read the audit log', (select count(*)::text from audit_events), '0');
select check_eq('successor handover', (select string_agg(i ->> 'code', ',' order by i ->> 'code') from jsonb_array_elements(handover_for(1, 1) -> 'claims') i), 'C1,C2');
update claims set label = 'verified' where code = 'C2';
select check_eq('successor cannot edit claims', (select label from claims where code = 'C2'), 'needs_review');

-- A new member: public only.
set test.uid = '00000000-0000-0000-0000-000000000003';
select check_eq('new member claims', (select string_agg(code, ',' order by code) from claims), 'C1');
select check_eq('new member handover', (select string_agg(i ->> 'code', ',' order by i ->> 'code') from jsonb_array_elements(handover_for(1, 1) -> 'claims') i), 'C1');

-- The reviewer: everything, and may edit.
set test.uid = '00000000-0000-0000-0000-000000000002';
select check_eq('reviewer claims', (select string_agg(code, ',' order by code) from claims), 'C1,C2,C3,C4');
select check_eq('reviewer records', (select count(*)::text from evidence_units), '4');
select check_eq('reviewer versions', (select count(*)::text from versions), '1');
update claims set label = 'verified' where code = 'C2';
select check_eq('reviewer can edit claims', (select label from claims where code = 'C2'), 'verified');

-- A reviewer of another project sees nothing here.
set test.uid = '00000000-0000-0000-0000-000000000004';
select check_eq('other project claims', (select count(*)::text from claims), '0');
select check_eq('other project versions', (select count(*)::text from versions), '0');
select check_eq('other project handover', (select handover_for(1, 1)::text), null);

-- Nobody signed in: default deny.
set test.uid = '';
select check_eq('anonymous claims', (select count(*)::text from claims), '0');
select check_eq('anonymous records', (select count(*)::text from evidence_units), '0');

reset role;
-- Immutable even without RLS in the way.
do $$ begin
  begin update versions set approved_by = 'x'; raise exception 'FAILED: version was changed';
  exception when raise_exception then if sqlerrm like 'FAILED%' then raise; end if; raise notice 'ok: versions are immutable'; end;
  begin delete from audit_events; raise exception 'FAILED: audit event was deleted';
  exception when raise_exception then if sqlerrm like 'FAILED%' then raise; end if; raise notice 'ok: audit events are immutable'; end;
end $$;
\echo ALL RLS CHECKS PASSED
