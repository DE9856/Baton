-- Row-Level Security for Baton on Postgres (Supabase, or plain Postgres with the small auth shim in db.py).
-- Safe to run repeatedly: applied at startup, over the tables SQLAlchemy creates.
--
-- visible(claim, viewer) = audience is among the viewer's clearances AND (viewer is the reviewer OR claim is not sensitive).
-- Default deny: with RLS on and no matching policy, a row can't be read. The app's own connection is the table owner,
-- used for writes and background jobs; reads made for a viewer switch to the "authenticated" role first (permissions.py).

create or replace function baton_clearances(project integer) returns text[]
language sql stable security definer set search_path = public, auth as $$
  select coalesce((select array(select json_array_elements_text(m.clearances)) from members m
                   where m.user_id = auth.uid()::text and m.project_id = project limit 1), '{}'::text[]) $$;

create or replace function baton_is_reviewer(project integer) returns boolean
language sql stable security definer set search_path = public, auth as $$
  select exists (select 1 from members m where m.user_id = auth.uid()::text and m.project_id = project and m.role = 'reviewer') $$;

create or replace function baton_is_member(project integer) returns boolean
language sql stable security definer set search_path = public, auth as $$
  select exists (select 1 from members m where m.user_id = auth.uid()::text and m.project_id = project) $$;

create or replace function baton_source_audience(source integer) returns text
language sql stable security definer set search_path = public as $$
  select audience from sources where id = source $$;

create or replace function baton_member_id(project integer) returns integer
language sql stable security definer set search_path = public, auth as $$
  select m.id from members m where m.user_id = auth.uid()::text and m.project_id = project limit 1 $$;

-- Supabase grants anon and authenticated full rights on every new table in public, and exposes them over its REST API
-- using a key that is meant to be public. Those grants are taken back here: anon gets nothing, authenticated only reads.
do $$ declare t text; r text; seq text; begin
  foreach t in array array['projects','members','sources','evidence_units','claims','claim_evidence','conflicts','gap_questions',
                           'versions','acknowledgements','audit_events','jobs','access_requests','voice_notes'] loop
    execute format('alter table %I enable row level security', t);
    execute format('revoke all on table %I from public', t);
    seq := pg_get_serial_sequence(t, 'id');
    foreach r in array array['anon', 'authenticated'] loop
      if exists (select 1 from pg_roles where rolname = r) then
        execute format('revoke all on table %I from %I', t, r);
        if seq is not null then
          execute format('revoke all on sequence %s from %I', seq, r);
        end if;
      end if;
    end loop;
    execute format('grant select on table %I to authenticated', t);
  end loop;
end $$;

drop policy if exists baton_project_read on projects;
create policy baton_project_read on projects for select to authenticated using (baton_is_member(id));
drop policy if exists baton_member_read on members;
create policy baton_member_read on members for select to authenticated using (baton_is_member(project_id));
drop policy if exists baton_source_read on sources;
create policy baton_source_read on sources for select to authenticated using (audience = any(baton_clearances(project_id)));
drop policy if exists baton_unit_read on evidence_units;
create policy baton_unit_read on evidence_units for select to authenticated using (
  baton_source_audience(source_id) = any(baton_clearances(project_id))
  and (json_array_length(sensitivity) = 0 or baton_is_reviewer(project_id)));
drop policy if exists baton_claim_read on claims;
create policy baton_claim_read on claims for select to authenticated using (
  audience = any(baton_clearances(project_id)) and (not sensitive or baton_is_reviewer(project_id)));
drop policy if exists baton_evidence_read on claim_evidence;
create policy baton_evidence_read on claim_evidence for select to authenticated using (
  exists (select 1 from claims c where c.id = claim_id));  -- the claims policy applies inside this subquery
drop policy if exists baton_conflict_read on conflicts;
create policy baton_conflict_read on conflicts for select to authenticated using (baton_is_reviewer(project_id));
drop policy if exists baton_question_read on gap_questions;
create policy baton_question_read on gap_questions for select to authenticated using (
  baton_is_reviewer(project_id) or asked_to_id = baton_member_id(project_id));
-- A snapshot holds every audience in one row, so it is reviewer-only; everyone else reads through baton_handover_for().
drop policy if exists baton_version_read on versions;
create policy baton_version_read on versions for select to authenticated using (baton_is_reviewer(project_id));
drop policy if exists baton_ack_read on acknowledgements;
create policy baton_ack_read on acknowledgements for select to authenticated using (
  baton_is_reviewer(project_id) or member_id = baton_member_id(project_id));
drop policy if exists baton_audit_read on audit_events;
create policy baton_audit_read on audit_events for select to authenticated using (baton_is_reviewer(project_id));
drop policy if exists baton_job_read on jobs;
create policy baton_job_read on jobs for select to authenticated using (baton_is_member(project_id));
drop policy if exists baton_access_read on access_requests;
create policy baton_access_read on access_requests for select to authenticated using (
  baton_is_reviewer(project_id) or member_id = baton_member_id(project_id));
drop policy if exists baton_voice_read on voice_notes;
create policy baton_voice_read on voice_notes for select to authenticated using (
  audience = any(baton_clearances(project_id)) and (not sensitive or baton_is_reviewer(project_id)));

-- A viewer's copy of an approved version, filtered inside the database by the same rule.
create or replace function baton_handover_for(project integer, version_number integer) returns jsonb
language sql stable security definer set search_path = public, auth as $$
  select v.snapshot::jsonb
    || jsonb_build_object(
      'claims', coalesce((select jsonb_agg(item) from jsonb_array_elements(v.snapshot::jsonb -> 'claims') item
        where (item ->> 'audience') = any(baton_clearances(project))
          and (not (item ->> 'sensitive')::boolean or baton_is_reviewer(project))), '[]'::jsonb),
      'missing', coalesce((select jsonb_agg(item) from jsonb_array_elements(v.snapshot::jsonb -> 'missing') item
        where (item ->> 'audience') = any(baton_clearances(project))), '[]'::jsonb),
      'conflicts', coalesce((select jsonb_agg(item) from jsonb_array_elements(v.snapshot::jsonb -> 'conflicts') item
        where (item -> 'audiences') <@ to_jsonb(baton_clearances(project))
          and (not (item ->> 'sensitive')::boolean or baton_is_reviewer(project))), '[]'::jsonb))
  from versions v where v.project_id = project and v.number = version_number and baton_is_member(project) $$;

-- Immutable even for the app's own connection: approved versions and audit events can't be changed or removed.
create or replace function baton_forbid_change() returns trigger language plpgsql as $$
  begin raise exception '% is append-only', tg_table_name; end $$;

-- New functions are callable by everyone by default, and on Supabase by anon over the REST API.
do $$ declare f text; r text; begin
  foreach f in array array['baton_clearances(integer)', 'baton_is_reviewer(integer)', 'baton_is_member(integer)', 'baton_source_audience(integer)',
                           'baton_member_id(integer)', 'baton_handover_for(integer, integer)', 'baton_forbid_change()'] loop
    execute format('revoke all on function %s from public', f);
    foreach r in array array['anon', 'authenticated'] loop
      if exists (select 1 from pg_roles where rolname = r) then
        execute format('revoke all on function %s from %I', f, r);
      end if;
    end loop;
  end loop;
end $$;
grant execute on function baton_clearances(integer), baton_is_reviewer(integer), baton_is_member(integer), baton_source_audience(integer),
  baton_member_id(integer), baton_handover_for(integer, integer) to authenticated;

drop trigger if exists baton_versions_immutable on versions;
create trigger baton_versions_immutable before update or delete on versions for each row execute function baton_forbid_change();
drop trigger if exists baton_audit_immutable on audit_events;
create trigger baton_audit_immutable before update or delete on audit_events for each row execute function baton_forbid_change();
