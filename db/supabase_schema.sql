-- Baton on Supabase Postgres: tables and Row-Level Security.
-- The running app currently uses SQLite (see docs/CHANGES_FROM_PLAN.md). db/test_rls.sql proves these policies on Postgres.
--
-- visible(claim, viewer) = audience is among the viewer's clearances AND (viewer is the reviewer OR claim is not sensitive).
-- Default deny: with RLS on and no matching policy, a row can't be read. Only background jobs use the service key.

create table projects (
  id bigint generated always as identity primary key,
  name text not null, role_title text not null default 'Tech lead',
  as_of date not null, last_day date not null, created_at timestamptz not null default now()
);

create table members (
  id bigint generated always as identity primary key,
  project_id bigint not null references projects on delete cascade,
  user_id uuid,  -- auth.users.id, set when the person signs in
  name text not null, title text not null default '',
  role text not null check (role in ('leaver', 'successor', 'reviewer', 'teammate')),
  aliases text[] not null default '{}', clearances text[] not null default '{public}'
);
create index on members (project_id, user_id);

create table sources (
  id bigint generated always as identity primary key,
  project_id bigint not null references projects on delete cascade,
  code text not null, type text not null, title text not null, audience text not null,
  authorised_by text not null, consent_note text not null default '', created_at timestamptz not null default now()
);

create table evidence_units (
  id bigint generated always as identity primary key,
  project_id bigint not null references projects on delete cascade,
  source_id bigint not null references sources on delete cascade,
  audience text not null,       -- copied from the source so the policy needs no join
  uid text not null, seq int not null, author text not null, ts timestamptz,
  body text not null,           -- secrets are masked before this is written
  link text not null default '', sensitivity text[] not null default '{}'
);

create table claims (
  id bigint generated always as identity primary key,
  project_id bigint not null references projects on delete cascade,
  code text not null, type text not null, subject text not null default '', statement text not null, value text not null default '',
  as_of timestamptz, due date, audience text not null, origin text not null default 'extracted',
  label text not null default 'needs_review', sensitive boolean not null default false,
  review_state text not null default 'pending', embedding_json jsonb  -- pgvector replaces this in the hosted build
);

create table claim_evidence (
  id bigint generated always as identity primary key,
  claim_id bigint not null references claims on delete cascade,
  unit_id bigint references evidence_units, quote text not null default '', match text not null, entailment real
);

create table versions (
  id bigint generated always as identity primary key,
  project_id bigint not null references projects on delete cascade,
  number int not null, approved_by text not null, approved_at timestamptz not null default now(),
  snapshot jsonb not null, content_hash text not null, unique (project_id, number)
);

create table audit_events (
  id bigint generated always as identity primary key,
  project_id bigint not null, actor text not null, action text not null, target text not null default '', detail text not null default '',
  at timestamptz not null default now()
);

-- Helpers run as their owner, so policies can read members without recursing through members' own policy.
create function clearances(uid uuid, project bigint) returns text[] language sql stable security definer set search_path = public as $$
  select coalesce((select clearances from members where user_id = uid and project_id = project limit 1), '{}'::text[]) $$;
create function is_reviewer(uid uuid, project bigint) returns boolean language sql stable security definer set search_path = public as $$
  select exists (select 1 from members where user_id = uid and project_id = project and role = 'reviewer') $$;
create function is_member(uid uuid, project bigint) returns boolean language sql stable security definer set search_path = public as $$
  select exists (select 1 from members where user_id = uid and project_id = project) $$;

alter table projects enable row level security;
alter table members enable row level security;
alter table sources enable row level security;
alter table evidence_units enable row level security;
alter table claims enable row level security;
alter table claim_evidence enable row level security;
alter table versions enable row level security;
alter table audit_events enable row level security;

create policy project_read on projects for select using (is_member(auth.uid(), id));
create policy member_read on members for select using (is_member(auth.uid(), project_id));
create policy source_read on sources for select using (audience = any(clearances(auth.uid(), project_id)));
create policy unit_read on evidence_units for select using (
  audience = any(clearances(auth.uid(), project_id))
  and (cardinality(sensitivity) = 0 or is_reviewer(auth.uid(), project_id)));
create policy claim_read on claims for select using (
  audience = any(clearances(auth.uid(), project_id))
  and (not sensitive or is_reviewer(auth.uid(), project_id)));
create policy evidence_read on claim_evidence for select using (
  exists (select 1 from claims c where c.id = claim_id));  -- the claims policy applies inside this subquery
create policy claim_review on claims for update
  using (is_reviewer(auth.uid(), project_id) and audience = any(clearances(auth.uid(), project_id)))
  with check (is_reviewer(auth.uid(), project_id) and audience = any(clearances(auth.uid(), project_id)));
-- A snapshot holds every audience in one row, so it is reviewer-only; everyone else reads through handover_for().
create policy version_read on versions for select using (is_reviewer(auth.uid(), project_id));
create policy audit_read on audit_events for select using (is_reviewer(auth.uid(), project_id));

-- A viewer's copy of an approved version, filtered inside the database by the same rule.
create function handover_for(project bigint, version_number int) returns jsonb
language sql stable security definer set search_path = public as $$
  select jsonb_build_object(
    'number', v.number, 'approved_by', v.approved_by,
    'claims', coalesce((
      select jsonb_agg(item) from jsonb_array_elements(v.snapshot -> 'claims') item
      where (item ->> 'audience') = any(clearances(auth.uid(), project))
        and (not (item ->> 'sensitive')::boolean or is_reviewer(auth.uid(), project))), '[]'::jsonb))
  from versions v where v.project_id = project and v.number = version_number and is_member(auth.uid(), project) $$;

-- Immutable even for the service key: approved versions and audit events can't be changed or removed.
create function forbid_change() returns trigger language plpgsql as $$
  begin raise exception '% is append-only', tg_table_name; end $$;
create trigger versions_immutable before update or delete on versions for each row execute function forbid_change();
create trigger audit_immutable before update or delete on audit_events for each row execute function forbid_change();
