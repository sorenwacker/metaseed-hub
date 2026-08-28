# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Changed
- A release deploy finishes by applying the container image pins, so a pin merged with a release lands with it instead of waiting for the weekly timer; a failure there is logged, not a failed deploy. Takes effect on the host after the Ansible role is applied again.
- Renovate runs from `.github/workflows/renovate.yml`, weekly and on demand, instead of relying on the hosted app, which never ran on this repository. It needs a `RENOVATE_TOKEN` secret; see *Where Renovate runs* in `docs/container-updates.md`.

### Changed
- **The license is MIT**, the same as metaseed's. The hub had carried Apache
  2.0 since a LICENSE file was added to match a badge; nothing recorded a
  reason for the difference. The README says what the hub is and does, and
  the package metadata names the license, authors, keywords, and URLs.

## [0.43.0] - 260827

### Fixed
- **A logged value cannot forge a second log record.** The hub's log handler
  uses metaseed's `OneLineFormatter`, which keeps every record on one line;
  CodeQL's log-injection findings are addressed in that one place.
- **A failed explorer, comparison or graph request no longer describes the
  exception to the client**; the cause goes to the log. The sign-in
  destination check also refuses a backslash (browsers read `/\host` as
  another origin), spec-builder redirects URL-quote the ids they carry, and
  table-row markup escapes the ids it interpolates.
- **CI workflows run with read-only permissions by default and every Action
  is pinned to a commit.** CodeQL scanning is on for the repository with the
  extended query suite.

### Changed
- metaseed is pinned to 0.45.0 or later, which carries the formatter.

## [0.42.0] - 260827

### Changed
- **A profile pushed over the API is a private draft, not a publication.** On
  this hub *published* means visible to every user; 0.41.0 published a pushed
  profile straight away, which put an author's work in front of everyone
  without them choosing it. A push now lands as the caller's draft (and a
  revised push updates it); `"publish": true` publishes explicitly, and
  `POST /api/specs/{id}/unpublish` withdraws one, both with the permissions the
  spec builder applies. `GET /api/specs` says which are drafts and which are
  published, and whose.

### Fixed
- **The Unpublish button on a published specification rendered as text and
  could not be pressed.** The sharing panel had been inserted into the middle
  of the button's tag, so its attributes appeared on the page as words. The
  test that covered the button checked for a substring the broken markup still
  contained; it now parses the page and finds a real button.

## [0.41.0] - 260826

### Added
- **The REST API tells a token holder who it is and exchanges specifications.**
  `GET /api/me` names the account and tenant behind a personal access token,
  which a client needs before it can create a dataset; `GET /api/specs` and
  `GET /api/specs/{name}/{version}` list and return published specifications
  as YAML; `POST /api/specs` publishes a profile document under the spec
  builder's version-bump gate. This is what a metaseed instance uses to push
  and pull profiles and datasets.
- **The spec tools accept the SEEK field markers** metaseed 0.43 defines —
  `seek_attribute_type`, `seek_controlled_vocab` and `seek_cv_free_text`. The
  tools refuse to silently drop a marker they do not expose, so the marker
  parity gate turned the dependency bump into a failing suite rather than a
  field an agent could not declare.

### Changed
- **metaseed is pinned to 0.43.0 or later**, the release that carries the
  template-bound profile fields. An older hub answers 422 when a metaseed
  instance pushes a profile that uses them.

- Dependency updates come from **Renovate** on `config:best-practices`
  instead of Dependabot, which cannot update `uv.lock`. Renovate regenerates
  the lockfile, pins Actions and the production images to digests, keeps
  image majors off (PostgreSQL 16, MariaDB 11), groups Python, Actions and
  image updates into one weekly PR each, and merges minor/patch updates
  itself once CI passes — replacing `dependabot-auto-merge.yml`.
- The admin page reports whether the host's container images match the versions
  it pins. Nothing in the application showed whether a deployment had fallen
  behind on them. Admin-only, and off the deployment host it reports "not
  checked" rather than inventing a fault. See `docs/container-updates.md`.

## [0.40.0] - 260820

### Added
- The admin page lists published specifications with how many datasets use
  each, most used first. Which specifications are load-bearing, and which
  nothing uses, was not visible anywhere: the count existed only inside the
  refusal to delete a draft that datasets depend on. Counted the same way that
  refusal counts it — soft-deleted datasets excluded, across every account,
  because publishing is what makes a specification available to others.

### Changed
- Built with hatchling, as metaseed is, rather than setuptools. The two repos
  were scaffolded a month apart and each kept its scaffold's default; nothing
  chose the split. Versions still come from git tags through setuptools-scm,
  which hatch-vcs wraps, and the built wheel was compared file by file against
  the setuptools one: 154 files, identical, same version string.
- The admin page puts errors in their own tab. They sat between the tables an
  admin actually came for, and an exception message or a path carrying a UUID
  is arbitrarily long, so rows grew until the table pushed past the page. Those
  cells now wrap, and the tab strip's switching moved from an inline copy in
  `dataset_new.html` into `hub.js`, so a second page adopting the markup gets
  panels that actually switch.
- The graph page loads metaseed's drawing instead of its own. `graph.html`
  inlined about 280 lines of vis.js — a lesser copy of the library's, without
  the legend counts or click-a-type-to-hide it now gets for free. The page
  supplies the dataset's data URL and nothing else, and a gate test fails if a
  hub-side copy grows back.

### Fixed
- An expired session no longer leaves the hub looking signed in. No response
  carried a cache directive, so a browser answered history navigations — the
  back button, a restored tab, a reopened window — out of its own cache: the
  dataset list kept drawing itself from a snapshot after the session behind it
  had gone, and opening a dataset from it produced an empty editor, because the
  page's panels load over HTMX and every one of them was refused. Hub responses
  are now `no-store`, so the browser asks and the redirect happens. Alongside
  it, one refusal shape everywhere — `AuthRequiredError`, which the handler
  turns into a redirect, an `HX-Redirect`, or JSON naming the sign-in page, so
  an edit made after the session expired can no longer fail in the console
  while the page looks as though it saved. Signing in returns to the page that
  was refused, and cookies the identity provider has explicitly rejected are
  cleared (an unreachable provider is left alone: an outage is not a verdict).
- Matomo records the visitor's address instead of the proxy's. It sits behind
  nginx, which forwards `X-Real-IP` and `X-Forwarded-For`, but Matomo ignores
  those headers unless told to trust them — so every visit was stored as the
  Docker bridge gateway and almost none could be located. The country report
  read "Unknown" for the large majority, and the handful of countries that did
  appear were not derived from an address, so they measured nothing. Applied
  through `common.config.ini.php`, which Matomo merges and never rewrites.
  Visits already recorded stay unlocatable.
- Importing a workbook no longer writes a count into a containment field. The
  export gives each such field a column holding a number, because the children
  are their own sheets; reimporting stored that string where a list of children
  belongs. Pydantic serialized it anyway and only warned, which is how it
  survived. The count was unreliable too — an Investigation with one Study
  exported `studies` as `'0'`.
- A first sign-in is recorded. The stamp was written before the account was
  provisioned, so a brand-new user had no row to write to and the admin
  directory showed "Never" for someone who had just arrived.

### Removed
- Multi-cell editing, and the `MULTI_CELL_EDITING` flag that hid it. What was
  built was a button that applied one value to a selected block; the intent was
  an Excel-like grid, which it was not, and the selection gesture was never
  finished. Bulk editing is served by downloading the workbook, editing it, and
  importing it back — a path this release also fixes.

## [0.39.2] - 260817

### Fixed
- Signing in creates the account, instead of leaving it to whichever page the
  person happened to open first. A newcomer is sent to the Home guide because
  they have no account yet, and that page rendered without creating one — so a
  colleague could sign in, read it, and stay unresolvable to sharing, which
  finds people by the address on their account. They looked signed in and could
  not be shared with.

### Changed
- The browser tests block the build again. They were advisory on the grounds
  that a browser and Keycloak round-trip is too flaky to gate, and then failed
  unnoticed for six days while shipping the bug above, which they had caught.

## [0.39.1] - 260817

### Fixed
- The person who created a spec draft can share it again. Creation writes no
  membership row, and sharing read ownership from that table alone, so the
  creator had no role and every owner-only control — the add-member form
  included — was hidden from them. Ownership now falls back to the resource's
  own creator column, which repairs the drafts that already exist rather than
  needing them migrated. An explicit membership still decides, and datasets,
  which have no creator column, are unchanged.

## [0.39.0] - 260817

### Changed
- Requires metaseed 0.42.0, which no longer ships the ISA-MIAPPE Combined
  (reconciled) profile. A dataset still bound to it has no built-in spec to
  validate against.

### Removed
- `SpecNotFoundError`, which nothing raised; the single load path reports a
  missing or empty spec as `DatasetDataLoadError`. An exception class nothing
  raises invites `except` clauses that can never fire.
- A second `spec_to_dict` in `spec_builder_helpers`, unused and near-identical
  to the one in `spec_builder/state.py` that is actually called.
- The `now` parameter of `select_expired` and `prune`. It was never read, yet
  its docstring said callers could pin a reference time in tests — and six
  tests passed it believing so.

### Fixed
- `/api/health` reports an unhealthy service without describing it. The
  endpoint is unauthenticated and connection errors carry the host, port,
  database name and user; those now go to the server log instead.
- `get_profile_relationships` and `spec_clone` resolve a profile name in the
  caller's own tenant, as `create_dataset` and `get_profile_schema` already
  did. When two tenants publish the same name and version, the tools no longer
  disagree about which specification that name denotes.
- Importing a file into a dataset built on a hub specification no longer
  answers 500. The route derived the root entity through the built-in profile
  loader, which cannot resolve a draft's or published spec's name.
- A browser edit refuses a dataset whose specification could not be loaded.
  The mutation dependency had reimplemented the write-path loader and left out
  its demand for a client, so an empty dataset with a broken spec accepted
  edits against an improvised facade.
- A connection leaving a room deletes only the room it emptied. The check ran
  against a reference captured before three awaits, so two simultaneous leaves
  raised KeyError, and a leave racing a join unsubscribed a room that had live
  connections — those users stopped receiving broadcasts with the socket still
  open.
- Account erasure removes the user's own soft-deleted datasets and specs.
  They never blocked deletion, because no list view shows them, but nothing
  else in the codebase hard-deletes a dataset — so an erased account kept its
  stored data, invisible and unowned. Rows a second owner shares are left
  restorable.
- A soft-deleted user loses membership-based access immediately. The three
  access-ladder helpers resolved the caller by identity provider subject alone,
  so a deleted account with a still-valid token kept reading and editing
  datasets shared with it — the path the MCP layer had already closed.
- The one-shot cookie carrying a new personal access token is marked secure
  outside debug, like every other cookie this app writes. It held a live
  credential for sixty seconds.
- Two error branches of the source import escape what they echo back: the
  submitted value, and the profile name, which for a spec-backed dataset is a
  draft name its author chose.
- A cell edit that names an item the list does not have is refused instead of
  editing a different one. `idx` came off the URL and was checked with
  `idx < len(list)`, which is true for every negative number, so `-1` edited or
  deleted the last item; an index past the end changed nothing yet still
  answered 200.
- A field name the entity does not have is a 400, not a 500. Four table
  handlers passed it to `field_info`, which raises `KeyError`.
- Malformed comment ids on spec drafts are 404s. The ids are UUID-typed
  columns, so an unparsable value raised a database error on Postgres while
  SQLite-backed tests coerced it and passed.
- Example data can only be read from metaseed's examples directory. The
  dataset's profile and version are free text and were joined straight onto
  the path, so `..` walked out of the package and an absolute value discarded
  the base entirely.

### Added
- The footer shows metaseed's GitHub stars beside the hub's, each labelled
  with its repository and carrying a hover title. Two bare star counts side by
  side said nothing about which project they belonged to.

## [0.38.1] - 260817

### Changed
- Block cell selection, fill and paste are behind `MULTI_CELL_EDITING`, off by
  default. The "Apply to selection" control appeared in every inline table
  header while the selection gesture did not work in the browser — a button
  that does nothing, crowding the "+ Add Row" beside it. The server route and
  its whole-or-nothing semantics are unchanged and tested; what was never
  verified was the browser gesture, so the feature is hidden until it is.

## [0.38.0] - 260817

### Changed
- metaseed floor raised to 0.41.0, which carries that library's full 260816
  review remediation. The hub was verified clear of 0.41.0's removed names
  before that release.

### Fixed
- Stored XSS in the delete-draft error: the names of datasets using a spec —
  whatever their owners typed — were interpolated raw into an HTML fragment
  htmx swaps into the page. The same class in `dataset_load_example` for the
  dataset's profile and version. Both escaped, with a scan test that fails on
  any unescaped value in an error `<div>`, because point fixes kept missing
  siblings.
- The members panel no longer 500s. `members_of` joined `User` to filter but
  selected only membership rows, leaving `member.user` a lazy relationship the
  template dereferences per row — a sync load on an async session. It looked
  fine only because tests create every user in one session.
- The comments panel no longer 500s at reply depth 3. The template's reply
  macro recurses without bound while eager loading stopped at two levels; the
  thread is now loaded flat in one query and assembled in Python.
- `/explore` works for built-in profiles again. `HubSpecLoader.load_profile`
  forwarded a `ctx` argument the library's method does not accept, so every
  fall-through to a built-in profile raised TypeError. A test now compares the
  override against the library signature it inherits.
- `PATCH /api/datasets/{id}` validates the payload it stores. It assigned
  `dataset.data` and committed, skipping the write load path every UI mutation
  goes through — so an API client could store nodes the profile cannot place,
  which the next UI save silently deletes.

## [0.37.0] - 260815

### Fixed
- Stored XSS on the dataset page (introduced with the embedded DCAT card in
  0.36.0): the JSON-LD block was rendered unescaped, so a dataset title
  containing `</script>` ended the block and had the rest parsed as markup,
  running attacker-supplied HTML for everyone the dataset was shared with.
  `<`, `>` and `&` are escaped as JSON before embedding, which leaves the
  card valid JSON-LD; the content-negotiated responses are RDF, not HTML,
  and were never affected.
- A block apply is limited to 1000 cells. A selection is bounded by the
  visible table, so an unbounded batch was a crafted request that made the
  worker rebuild arbitrarily many entities.

### Changed
- metaseed floor raised to 0.39.0, whose `graph.js` is reusable without its
  transport (metaseed#254).

### Added
- A value that repeats down a column is entered once: shift-click selects a
  block of inline-table cells, and Ctrl+Enter (or "Apply to selection") writes
  the value to every cell in one request and one dataset version. The block is
  applied whole or refused whole — a target row deleted meanwhile refuses the
  batch rather than half-writing it — and the column naming a row's parent is
  never filled this way, since re-parenting is a link change that
  `metaseed.facade.linking` owns.
- A selected block copies out as tab-separated rows (Ctrl+C) and a
  tab-separated grid pastes in at the clicked cell (Ctrl+V), so a column of
  varying values can come from a spreadsheet. Paste fills cells and never
  adds rows: values past the last row or column, or landing on a
  parent-reference column, are dropped and the rest keep their positions.
  Both gestures are one server write — filling repeats a value, pasting
  varies it — so what is allowed and how a value is converted cannot drift
  between them. A value the column cannot hold is stored as typed and
  reported by validation rather than silently discarded.

## [0.36.0] - 260815

### Added
- The MCP endpoint accepts an OIDC bearer beside personal access tokens
  (metaseed-hub#38's third auth adapter): a hub session's access token
  authenticates an MCP client as its holder, decided by prefix so a hub
  token is never sent to the IdP.
- A dataset page is harvestable (metaseed#30): it embeds the dataset's DCAT
  record as JSON-LD, the dataset URL answers content negotiation
  (`Accept: application/ld+json` / `text/turtle` returns the record itself),
  and responses carry a `rel="describedby"` Link — the signals FAIR
  assessors read. Reachability follows the deployment's authentication.

- An opt-in F-UJI FAIRness regression check (metaseed#31):
  `tests/test_fuji_fairness.py` scores a reachable dataset URL through a
  running F-UJI service (`FUJI_URL`/`FUJI_TARGET`) and fails when the FsF
  score regresses below the baseline. Skipped without the environment, so
  the core suite stays hermetic.

### Removed
- The FeatureGrant mechanism. It gated the SEEK panel and settings, the DCAT
  column in the spec builder, and the whole adapter export menu behind
  per-group grants — and no interface ever wrote a grant row, so everything
  it guarded was hidden from every user since it shipped. Adapters (SEEK,
  DCAT, ENA, …) are plugins available to every signed-in user, as metaseed's
  registry offers them; SEEK's real gate is the per-user connection itself.
  The `feature_grants` table is dropped (with an exact-mirror downgrade).

### Changed
- metaseed floor raised to 0.38.0: the shipped `isa-miappe-combined`
  profile, the cross-profile nested-entity resolution fix, and the
  hub-consumer contract test guarding this hub's imports.
- Entity routes ride the single load path: `EntityService.ensure_state`
  delegates to `ensure_dataset_facade_for_write`, deleting its duplicated
  spec resolution and strict load. A dataset with an unplaceable stored node
  now gets the same 409 refusal from every mutating route, and a writer on
  an empty dataset with a broken spec is refused with the reason instead of
  receiving a silent, unusable state (`require_client`).
- Presence is global. Room membership lives in a per-room Redis sorted set
  scored by heartbeats: every instance stamps its connections in every 30
  seconds, reads the whole set back, and a process that stops refreshing
  ages out after three missed beats. Without Redis, presence falls back to
  the local room, which is then also the whole truth.

## [0.35.0] - 260814

### Fixed
- A published-spec name collision across tenants resolves to the caller's own
  tenant, then the oldest publication — `.first()` on an unordered query
  handed back whichever row the database returned.
- The explore picker only offers what the loader will accept: a draft shared
  via `SpecDraftMember` now loads for its member instead of being offered and
  then refused.
- An import row whose `_parent` matches nothing is reported instead of being
  silently re-rooted.
- A new table row carries only its identity and its parent reference; the
  fabricated placeholders ("New Title", 0, False) that persisted with
  validation skipped are gone, and an unknown field name can no longer 500
  the row editor.
- A rule can apply to several entities over MCP (`applies_to` accepts a list,
  as the spec schema always has).
- Cloning a template is a POST — a state-changing GET minted drafts for
  crawlers and prefetchers — and the spec YAML upload is bounded at 2 MB.
- Both previously unrunnable alembic downgrades run: every constraint the
  migrations touch is named (gated by a scan test), and the tenant_id
  restoration backfills from the owning workspace before adding NOT NULL.
- Docstrings state what the code does: `can_edit_spec` (author or shared edit
  role), the websocket manager (messages scale via pub/sub, presence is
  per-instance), and PAT sessions (no roles, no entitlements — by
  construction). The ontology demo spec uses the `ontologies` key the picker
  actually filters on.

### Removed
- Dead helpers kept alive only by their own tests: `_db_user_id` and
  `require_draft_owner` (`require_owner_role` is the live mechanism).

## [0.34.0] - 260813

### Fixed
- `get_ontology_term` (MCP) no longer reports an outage as nonexistence. The
  term router answers None both for a missing term and for a source that
  failed; the tool now asks whether any source could actually see the
  ontology before calling the term missing — the same three-outcome rule the
  rest of the checks follow, broken at this boundary too.

### Fixed
- Publishing a draft no longer orphans the datasets built on it. Publish
  deleted the draft, the datasets' `spec_draft_id` went NULL with it, and each
  one lost its specification — editing disabled — while `delete_draft` refuses
  in exactly that situation. Publish is the moment a draft becomes the spec,
  so the datasets are rebound to the new Spec row before the draft goes.
- A malformed version typed into profile metadata is refused in the form
  instead of persisted. Pydantic validates `version` on construction, not on
  assignment, so `v1.0` was stored — and every later load of the draft raised,
  bricking it.
- Publishing a name/version that already exists reports the collision as a
  form message instead of a 500 from the unique index.

### Fixed
- Three import routes no longer bypass the unloadable-node refusal. Importing
  into a dataset, loading an example into one, and importing from a source
  all loaded permissively and then saved — so any stored node the profile
  could not place was silently deleted by the very operation that looked like
  it was only adding. They now refuse with the node named, exactly as every
  cell edit already did.
- A long cell value is no longer truncated into the editable input. The table
  cut values over 50 characters for display and rendered the cut string into
  an input whose blur saves — reading a dataset could corrupt it. The full
  value feeds the input; the stylesheet clips it visually.
- Deleting a primitive-list row re-renders the table. The other rows kept
  their original indices in every edit and delete URL, so the next edit
  overwrote the wrong item and an edit past the new end was silently dropped.
- MCP `save_dataset` writes through the canonical path: the stored form
  carries the tree envelope and the spec-hash stamp the drift check reads,
  the previous state becomes a version exactly as a browser save would, and a
  payload whose entities cannot be read or placed is refused with the reason
  — it used to be stored raw and quietly lost on the next load. A legacy raw
  blob is snapshotted before its first canonical migration, so nothing
  pre-envelope becomes unrecoverable.

## [0.33.1] - 2026-08-13

Security release, from the 260813 codebase review (`docs/REVIEW.md`). Every fix
was proven by a failing test first.

### Fixed
- A member's role now decides what they may do. The sharing panel has offered
  VIEWER since sharing shipped, and nothing on the content-mutation paths read
  it: any member could edit or delete a shared dataset. Content mutations —
  every table, cell and row edit, entity create/delete, imports, example
  loading, version restore — authorize through the edit roles; deleting the
  dataset itself requires an owner; reads and comments stay open to every
  member.
- The REST API honours dataset sharing. It answered tenant-owned datasets only,
  so a dataset shared with you was editable in the browser and a 404 over the
  same account's API token. Access control now lives in one non-UI module
  (`metaseed_hub.access`) that the UI, REST API and MCP layers all import — the
  API had been importing its tenancy checks from the UI layer. A stranger's
  probe still reads 404, never 403: existence is not disclosed across tenants.
- A freshly minted access token no longer rides in a redirect URL, where it
  landed in the server access log and browser history. It travels in a
  one-shot encrypted cookie the profile page reads once and expires.
- A soft-deleted user's access token authenticates as nobody. Every other
  lookup treats such a user as nonexistent; authentication was the one
  credential path that outlived the account.
- Token verification accepts only asymmetric JWT algorithms. The allowed list
  came from the IdP's own discovery document, so an issuer advertising HS256
  would have had tokens verified symmetrically against the public RSA key — a
  public value.
- A websocket client can no longer forge server message types: a frame
  claiming `presence` is dropped before broadcast instead of being relayed as
  the room's member list.
- Account deletion is no longer blocked by items the user cannot see. A
  dataset they had already deleted, or a spec they had withdrawn, still
  counted as "needs a new owner" while every list view filtered it out — with
  soft delete the only delete available, the account was unremovable.
- Erasure now takes the per-user tenant row (named after the person) and the
  SeekConnection holding their encrypted SEEK API key. The module docstring
  claimed the cascade removed all personal data; neither hangs off the user
  row. A tenant still holding co-owned work that survives the user is scrubbed
  to an opaque name rather than deleted.

## [0.33.0] - 2026-08-13

### Added
- The spec tools can set `within`, the field marker that scopes a column to one
  branch of an ontology (metaseed #229). The hub's own gate refused to run at
  all while metaseed defined a marker these tools did not expose, which is how
  this was caught rather than shipped.
- The spec tools can set `reference_scope`, which says whether a reference must
  resolve inside the dataset or may name a record held elsewhere — a GBIF taxon,
  a museum catalogue record (metaseed 0.34.0).
- The rule tools take every attribute of the rule format, gated by a test
  against metaseed the way the field markers already are. They took six, so a
  caller could declare a cardinality rule's type but not its bounds, a
  uniqueness rule but not its scope. That now includes `where`, `when` and
  `require`, the predicates that let a rule depend on what a field holds
  (metaseed #211). Exposing them took `_spec_tools.py` past the thousand-line
  limit, so rule authoring now lives in its own `_rule_tools.py`.

### Changed
- Requires metaseed 0.34.0.
- Ontology lookups — the term picker, the MCP search, suggestion and term tools
  — ask metaseed's term router rather than OLS directly, so a vocabulary
  configured on the server is offered here too. The hub held its own OLS-only
  copies of these calls, which meant a local vocabulary would have been
  invisible in the hub while the standalone application offered it. Results say
  which source answered. `tests/test_term_sources_are_adapters.py` fails when a
  lookup goes back to OLS directly; the catalogue listing and the cache
  statistics still name OLS, because both are questions about OLS itself.

### Added
- A reference column in an inline table offers the rows it may name, as the
  exported spreadsheet already does. A reference typed by hand is the commonest
  way a row ends up attached to nothing. Suggestions come from a
  dataset-scoped endpoint: values from another dataset would be useless and a
  disclosure.

## [0.32.0] - 2026-08-12

### Added
- Exported spreadsheets carry the standard's vocabularies (metaseed 0.32.0,
  after RightField): a controlled column becomes a dropdown, a column naming
  another sheet picks from the rows that exist, a repeated identifier is
  flagged, headings carry their descriptions, and each sheet is a table that
  absorbs typed rows with their validation. Rules warn rather than block, and
  conditional formatting keeps marking what was accepted — which is what
  catches pasted values.
- A test that fails if the hub re-implements something the library owns.

### Fixed
- Pushes to SEEK reuse what a previous push created instead of building a
  second copy of everything (metaseed 0.31.0; production never had it because
  the hub was pinned to 0.30.0).
- Importing a workbook no longer reports the export's own hidden sheet as an
  unknown entity type.

### Changed
- The workbook is built by the library, from the facade both applications hold.
  The hub's copy of the builder — correct, and no longer improving — is gone.
- Fraunces and DM Sans are served by the hub rather than fetched from Google's
  CDN on every page load. The stylesheet has always named them; nothing loaded
  them from here.
- The landing page states what the hub does, and says plainly that this is in
  heavy development: features change and stored data may need migrating.
- The privacy page states how long backups keep a deleted account's data (up to
  6 months) and how long recorded errors are kept (30 days), both gated against
  the code's own values.

## [0.31.6] - 2026-08-11

### Security
- The members list of a dataset, draft or specification was readable by any
  signed-in user who knew the resource's id: the routes that change access
  checked ownership, the route that reads it checked nothing. Seeing who has
  access now requires being shared with the resource or owning the account it
  lives in, and an unrelated request gets 404 rather than 403. Introduced in
  0.31.0 with the single sharing mechanism.

### Fixed
- Export filenames were stamped with the server's local date, so a file written
  at 01:00 CEST was dated a day ahead of the instant it was written. All clock
  reads are UTC, and a test fails on a naive one.

## [0.31.5] - 2026-08-11

### Fixed
- Saving an entity that has related items listed could wipe its own fields. The
  inline child tables sat inside the parent's form, and a child row's inputs
  carry the child's field names — a Source has a title just as a Study does —
  so they were submitted with the parent and the last empty cell won.

## [0.31.4] - 2026-08-11

### Added
- Check SEEK, on a dataset's SEEK panel: reports which of the profile's ISA
  Templates the instance is missing and where an administrator installs them.
- A push says it has started and disables its button while it runs.

### Fixed
- A push against a SEEK that is down said "SEEK rejected GET /isa_tags (503)",
  which reads as a bad request. Any 5xx now says the instance is not serving
  SEEK. A push that created nothing says so rather than reporting zero counts
  as success.

## [0.31.3] - 2026-08-11

### Fixed
- The SEEK panel appeared on every dataset, including ENA and PRIDE ones, where
  a push has nothing to hang records on. It now appears only where a push can
  work, read from the profile's own SEEK roles.

## [0.31.2] - 2026-08-11

### Added
- A profile's ISA Templates can be downloaded from the dataset's SEEK panel.
  Only a SEEK administrator can install them, and SEEK's ISA-JSON exporter
  needs them; the file could not be obtained from the hub at all.

## [0.31.1] - 2026-08-11

### Fixed
- Correcting the SEEK URL no longer costs the API key: leaving the field blank
  keeps the stored one.
- Pushes went to whichever project SEEK returned first. The project is chosen
  on the profile page, and a later check keeps the choice.

## [0.31.0] - 2026-08-11

### Added
- Published specifications can be shared. They had a members table and nothing
  that wrote to it, so handing one to a colleague meant editing the database.
- An item always keeps an owner: the last one cannot be demoted, removed, or
  leave.

### Changed
- Datasets, specification drafts and published specifications share one
  sharing mechanism, one set of routes and one panel. There were two nearly
  identical routers, four copies of the members markup — two calling routes
  that no longer existed — and three role vocabularies.
- One role vocabulary everywhere: owner, editor, viewer. `curator` becomes
  `editor` in existing data.

## [0.30.5] - 2026-08-10

### Removed
- Teams and notes. Both were removed from the hub as features long ago but
  their models, tables and test fixtures remained, and one dead branch — "an
  admin or owner of a team in this tenant may edit a specification" — sat in
  the permission check where it could never be true. Production held zero rows
  in all three tables.

### Changed
- "Workspace" no longer appears anywhere. It named a concept the hub does not
  have, in 61 places across function names, error messages and admin screens.
  Everything says account, which is what a tenant is: one person's. A test
  fails if a retired word comes back.
- The test harness resets the whole schema per run rather than dropping the
  tables the models still declare: a table whose model has been deleted is
  invisible to the metadata, stays behind, and its foreign key blocks dropping
  everything else.

## [0.30.4] - 2026-08-10

### Fixed
- Withdrawing a published specification no longer breaks the datasets built on
  it. acdc_ks 2.0 was withdrawn on 260728 and two datasets in another account
  raised an error on every page from then on: withdrawal soft-deletes the spec,
  which removes it from the lookup every dataset performs on load. The check
  lives in `unpublish_spec`, so the API and MCP paths are covered, and it
  searches hub-wide by name and version — datasets bind that way, and the ones
  at risk are usually not the publisher's.

### Added
- The specification page names the datasets built on it and disables Unpublish
  while any exist, rather than only refusing after the click.

## [0.30.3] - 2026-08-10

### Fixed
- Saving a SEEK connection failed with a not-null violation on `created_at`:
  the model declares a server default, the creating migration did not, and
  tests build their tables from the model, which carries it. `feature_grants`
  had the same defect, dormant until something inserted a grant through the
  application; both are fixed.
- `alembic check` covered only twelve hand-listed tables while the application
  had grown past twenty, so `datasets`, `seek_connections`, `feature_grants`
  and the rest were never compared. The list now comes from the models, the
  check compares server defaults, and a test builds a database from the
  migrations alone and asserts it matches — the only test that does not build
  its schema from the models, which is why nothing caught this.

### Added
- The status names the project pushes will land in, and reports an account in
  no project as the blocker it is — SEEK attaches every record to a project.

### Changed
- The SEEK connection is configured on the profile page, beside the other
  per-user credentials, instead of a page nothing linked to. `/hub/seek` and
  `/hub/seek/settings` redirect there.

## [0.30.2] - 2026-08-10

### Fixed
- A SEEK connection is no longer refused for having no project. The check lists
  projects instead of demanding one: reaching SEEK with a valid key is a
  working connection, and an account in no project is reported as the separate
  thing it is.
- A failed check no longer discards the settings. What was typed is stored
  either way — URL and key — with the outcome recorded, so a typo in the URL no
  longer costs the API key and a SEEK that is briefly down no longer wipes a
  working connection.
- Failures name their cause: a hostname the server cannot resolve, nothing
  answering at the port, a rejected key, an answer that is not a SEEK API.

### Added
- The connection's standing is shown on the SEEK settings page and beside the
  push button on the dataset page: working (with the instance and when it was
  checked), not working (with the reason), or not configured.
- Check again re-runs the check against the stored connection, without retyping
  the API key.

## [0.30.1] - 2026-08-10

### Changed
- The SEEK panel sits like its neighbours: Push and Settings share a row, the
  downloadable checkbox reads as one label beneath.

## [0.30.0] - 2026-08-10

### Added
- The SEEK plugin, behind the `seek` feature flag: a per-user connection to a
  FAIRDOM-SEEK instance (API key encrypted at rest, verified against SEEK
  before saving, never rendered back), and a Push to SEEK panel on the dataset
  page that provisions the profile and syncs the dataset. An optional
  "downloadable" checkbox maps to the sharing level SEEK's ISA-JSON export
  requires; unchecked, records stay private to the key's person.
  Import from SEEK is deliberately absent: the importer derives its profile
  from the instance, and hub datasets are bound to installed profiles — it
  arrives when derived specs can be persisted.

### Changed
- The metaseed dependency includes the `seek` extra.

## [0.29.1] - 2026-08-10

### Fixed
- The beta-tester invite on the landing page pointed at the wrong SRAM
  collaboration, routing every applicant — and their group memberships — into
  a collaboration the hub's grants never matched.

## [0.29.0] - 2026-08-10

### Fixed
- Entitlements now arrive from SRAM: the token was the only place the hub
  looked, and SRAM puts `eduperson_entitlement` only on its userinfo endpoint.
  A user in a plugin group saw none of its functions; now `verify_token` falls
  back to userinfo (cached per token), one code path for both issuers.
- Excel import stops flattening every dataset: the export writes a `_parent`
  column and the import files each child under the node whose declared
  identifier it names. Every cell is written as text, so gene names stay names
  and identifiers keep their leading zeros.

### Changed
- Require metaseed `>=0.30.0`: Excel import in the shared templates, the
  optional-fields filter on long forms, the clamped profile picker with the
  full description in a tooltip, and the reference-driven assay link for SEEK.
- Rendered-page tests assert the export buttons a user sees match the features
  their groups grant.
- After changing `keycloak-realm.json`, drop the dev `keycloak` database and
  restart: Keycloak imports the realm once and it persists in postgres.

## [0.28.0] - 2026-08-10

### Changed
- An adapter export is offered only to a user whose group has its plugin
  enabled — for the buttons and for a hand-typed URL alike. Adapter keys and
  feature names are the same six strings, so membership of a plugin's group is
  what turns its export on. The `beta-testers` group is granted all six.
- The landing page says integrations are rolled out per group and links the
  SRAM registration page for beta-tester applications.
- Duplicated code fails the pre-push hook (`pylint --enable=duplicate-code`),
  giving the single-source-of-truth rule the gate it lacked.

## [0.27.0] - 2026-08-09

### Added
- Per-group feature flags. Group membership comes from the identity provider
  (Keycloak in development, SRAM in production, one claim shape for both);
  which feature a group may use is hub state in the new `feature_grants`
  table. `require_feature` gates routes with 404, and the DCAT field marker is
  the first gated feature: without a grant the value is not saved and the
  editor says so. No grants ship, so every gated feature starts off.
- The MCP field tools accept `isa_tag`, with a parity test pinning them to the
  markers metaseed defines — without it the next metaseed upgrade breaks both
  tools at runtime.

### Changed
- Only membership of the SRAM admin group grants admin. Realm roles grant
  nothing: they exist only in the dev Keycloak, so an admin path through them
  was a door that existed in development and not in production. `ADMIN_ROLE`
  names the group, as a full URN or a bare group name.
- The models module is a package split by aggregate; every name still imports
  from `metaseed_hub.models`.
- The test schema is built once per run, dead connections are cleared first,
  and tables are emptied with ordered DELETE — the suite is faster and a
  killed run no longer wedges the next one.

## [0.26.0] - 2026-08-05

### Changed
- Require metaseed `>=0.29.0`, which brings the SEEK export capability to the
  hub: provisioning Sample Types and syncing a dataset to a SEEK instance, the
  browsable "what will be created" preview on the SEEK page, remote data-file
  sync, per-version provisioning, and the `seek-ready-template` built-in
  profile. The lockfile is updated so the deploy installs metaseed 0.29.0.

## [0.25.0] - 2026-08-04

### Changed
- Take the metaseed where the SEEK profile is called SEEK (#103)
- Say why a dataset will not open (#102)

## [0.24.1] - 2026-08-04

### Changed
- Title a specification by its name, not the slug it is stored under (#101)

## [0.24.0] - 2026-08-04

### Changed
- Require the metaseed that stops a half-filled row being refused (#100)
- Keep a draft saveable when another one holds its spec's name (#99)
- Fix dataset sharing, specification creation, and the forked field form (#97)
- Stop re-testing on the tag the commit was already tested under

## [0.23.0] - 2026-08-01

### Changed
- Let people set the DCAT property and see spec advice in the browser, not only over MCP

## [0.22.0] - 2026-08-01

### Changed
- Let agents declare which field identifies an entity, not only its type
- Refuse a browser edit that would delete unloaded entities, as the agent path already does

## [0.21.1] - 2026-08-01

### Changed
- Refuse an edit that would delete the entities a dataset failed to load

## [0.21.0] - 2026-07-31

### Changed
- Report the entities that could not be loaded instead of dropping them out of sight
- Split the MCP tests by tool family and gate the file-size rule that let them drift
- Use metaseed's public model attribute; a private one can be renamed in a patch release

## [0.20.0] - 2026-07-31

### Changed
- Refuse a version bump that hides breaking changes, and stamp datasets with the spec they were authored against

## [0.19.0] - 2026-07-31

### Changed
- Let hub spec drafts express entity relationships; the instructions promised items= but the tool had no such parameter

## [0.18.4] - 2026-07-31

### Changed
- Call the spec builder Builder everywhere; three templates named one destination three ways

## [0.18.3] - 2026-07-30

### Changed
- Declare the mcp dependency the code imports directly, bounded below 2.x

## [0.18.2] - 2026-07-30

### Changed
- Let agents bring external YAML specs and cloned profiles into the hub as drafts

## [0.18.1] - 2026-07-30

### Changed
- Reuse metaseed's spec-builder core instead of maintaining a fork; hub keeps config only

## [0.18.0] - 2026-07-30

### Changed
- Confine metaseed.ui imports to one boundary module and rebuild graph/export on the public API (#53 step 4)
- Answer the recurring MCP questions in one place
- Let agents correct specs, create hierarchies in one call, and look up ontology terms over MCP

## [0.17.3] - 2026-07-30

### Changed
- Load and save datasets through the facade so cache and storage cannot diverge (#53 steps 1-3)

## [0.17.2] - 2026-07-30

### Changed
- Name both connected fields on reference edges; entity-only edges hid the join columns

## [0.17.1] - 2026-07-30

### Changed
- Share the spec-linkage MCP instructions with metaseed so agents stop building flat specs

## [0.17.0] - 2026-07-30

### Changed
- Drop vulture whitelist entries for code that no longer exists
- Escape error output in HTML fragments and fix dataset import and editing defects
- Enforce spec draft roles and reject malformed spec-builder input instead of erroring
- Surface dataset load failures instead of silently saving empty state
- Store and read datasets in tree format over MCP so agent edits cannot clobber web data
- Prevent websocket broadcast corruption and cross-instance connection id collisions
- Fix OIDC config precedence and handle identity-provider network failures gracefully
- Record the codebase review findings and remediation plan

## [0.16.6] - 2026-07-29

### Changed
- Correct the legal basis to public task; a free university tool is not a contract (#93)

## [0.16.5] - 2026-07-29

### Changed
- Disclose analytics and fix fabricated claims on the privacy page; add landing-page SEO metadata and robots.txt (#92)
- Remove the broken Matomo subpath UI; keep tracker public, admin via tunnel only
- Let Matomo use its own CSP so its login JS is not blocked by the hub's
- Restrict the Matomo UI to TU Delft networks; keep tracker public
- Set Matomo site id to 1 now that the site exists
- Expose only the Matomo tracker endpoints publicly, never the installer or admin (#91)

## [0.16.4] - 2026-07-28

### Changed
- Self-hosted Matomo analytics: cookieless, same-origin, config-gated (#90)

## [0.16.3] - 2026-07-28

### Changed
- Fix mobile caching, overflow, tight pages, and two fabricated privacy contacts (#89)

## [0.16.2] - 2026-07-28

### Changed
- Stop icons filling the width on mobile, which also un-centred the page (#88)

## [0.16.1] - 2026-07-28

### Changed
- Make the header and overview usable on a phone (#86)
- Pin the MCP Host allowlist instead of disabling the rebinding check (#87)

## [0.16.0] - 2026-07-28

### Changed
- Tell an agent about the tools it actually has (#85)
- Explain what the hub is for, without taking over the dataset list (#84)

## [0.15.1] - 2026-07-28

### Changed
- Make Cancel on the publish dialog actually cancel (#83)

## [0.15.0] - 2026-07-28

### Changed
- Count each user's authored specs in the admin directory (#82)
- Let an agent populate data and build specs, not just replace whole datasets (#81)
- Correct a comment claiming ValidationResult is truthy when invalid (#80)

## [0.14.5] - 2026-07-28

### Changed
- Report what is missing when an agent saves, from the spec's own rules (#79)

## [0.14.4] - 2026-07-28

### Changed
- Let a script authenticate to the API with a token, not only a browser session (#78)

## [0.14.3] - 2026-07-28

### Changed
- Bound what an agent can destroy through the MCP endpoint (#77)

## [0.14.2] - 2026-07-28

### Changed
- Make the MCP endpoint reachable at the address it is documented as (#76)

## [0.14.1] - 2026-07-28

### Changed
- Serve MCP at /hub/mcp so an agent can work as one hub user (#75)
- Let a dataset be created from a published spec at all (#74)
- Accept the unpublish form's CSRF token so the button can work (#73)

## [0.14.0] - 2026-07-28

### Changed
- Make publishing share a spec with every user, as it always claimed to (#72)
- Stop a spec vanishing from its author when a shared draft is published (#71)
- Let an admin remove content published or created by mistake (#70)
- Match pre-commit ruff to the locked version so it stops undoing the lint gate (#69)
- Run the migrations in the suite so a broken one cannot stay green (#68)
- Let a spec published by mistake be withdrawn to a private draft (#67)
- Say what an import failure was, not just that it failed (#66)

## [0.13.0] - 2026-07-27

### Changed
- Consume metaseed 0.19.0, where a PRIDE import is a tree
- Refuse an import that found nothing instead of reporting success

## [0.12.0] - 2026-07-27

### Changed
- Let a user fill an empty dataset from the repository its profile came from

## [0.11.0] - 2026-07-27

### Changed
- Consume metaseed 0.18.0, with the dcat extra so the record is offered

## [0.10.0] - 2026-07-27

### Changed
- Record unhandled errors and show them in the admin dashboard
- Refuse a spec-draft save that would overwrite a newer edit
- Reformat entity_routes with the pinned ruff
- Show per-user dataset counts and last sign-in to admins
- Stop offering a restore that cannot change anything

## [0.9.0] - 2026-07-27

### Changed
- Back up the database on a timer with tiered retention (#59)
- Add_entity_node writes through the facade (#53 Phase 3.2)
- Add-entity rows write through the facade, not just the cache (#53)
- Revert #54 + add regression: cache-added rows must survive save
- Revert "Merge pull request #54 from sorenwacker/phase2-serialize-via-client"
- Serialize datasets via MetaseedClient, removing the duplicate serializer
- Lock the two dataset serializers to one compatible format (#51)
- Remove dead params/guards and tighten types from review appendix
- Clear editing pointers when a spec draft is reset
- Store role and status enums by value, consistent with reactions
- Validate dataset name in the REST create/update endpoints
- Surface malformed constraint input as a form error, not a 500
- …and 17 further changes; see git history.

## [0.8.0] - 2026-07-25

### Changed
- Security best practices: app-wide CSRF guard, SAST gate, self-hosted JS (#31)
- Close spec-builder CSRF gaps from the second review (#30)

## [0.7.0] - 2026-07-25

### Changed
- Security review hardening (#29)

## [0.6.1] - 2026-07-25

### Changed
- Browser E2E for adapter export + advisory selenium CI job (#28)

## [0.6.0] - 2026-07-25

### Changed
- Surface metaseed adapter exports in the dataset UI; require metaseed>=0.16.0 (#27)

## [0.5.0] - 2026-07-24

### Changed
- Deps: depend on metaseed from PyPI instead of a git pin (#26)
- Bump pyasn1 from 0.6.3 to 0.6.4 (#24)
- Add the Apache 2.0 LICENSE the README already advertises
- Bump mcp from 1.27.1 to 1.28.1 (#21)

## [0.4.65] - 2026-06-23

### Added
- The admin dashboard shows a security warning when the application is running with the default development `SECRET_KEY`

## [0.4.64] - 2026-06-23

### Added
- CSRF tokens are signed with the application `secret_key` (HMAC), so the server only accepts cookies it issued; this hardens the double-submit check against cookie fixation and forgery

### Removed
- Removed the non-functional team chat: messages were never persisted or broadcast. The route, template, model, and the unused `chat_messages` table are dropped; discussion is served by comments

## [0.4.63] - 2026-06-23

### Fixed
- Restored the `secret_key` setting so the `SECRET_KEY` provided by the deployment environment is accepted; its earlier removal made settings loading reject the variable, which aborted the auto-deploy at the migration step

## [0.4.62] - 2026-06-23

### Changed
- Updated metaseed to v0.10.0

## [0.4.61] - 2026-06-23

### Fixed
- CI runs the test suite against a PostgreSQL service so database-backed tests execute instead of erroring on a missing connection
- CI runs on version tag pushes so a release tag fails visibly when the suite is broken

## [0.4.60] - 2026-06-22

### Fixed
- Multi-tenant isolation: table mutations, dataset member management, and spec-comment reactions are now scoped to the caller's access
- Soft-deleted datasets are excluded from the shared access helper, the mutation path, and draft publish
- Stored XSS in entity validation output from unescaped user-derived labels and messages
- WebSocket Redis pub/sub: serialized shared-connection access and stopped the listener spinning before the first subscription
- CSRF validation on dataset member-management and entity-validate routes
- Field renames are validated like field creation, rejecting invalid and duplicate names
- Auth callback rejects a missing access token instead of setting a broken session cookie

### Changed
- Routed tenant/user provisioning, page rendering, and form coercion through their canonical helpers
- Removed dead auth, config, and form surface

## [0.4.59] - 2026-06-16

### Changed
- Remove tenant from admin dashboard as it is not a relevant monitoring axis
- Request eduperson_entitlement and offline_access scopes so SRAM releases admin group membership
- Bump cryptography from 48.0.0 to 48.0.1 (#13)
- Bump pyjwt from 2.12.1 to 2.13.0 (#12)

## [0.4.58] - 2026-06-15

### Added
- GitHub star count for the hub repository in the footer

### Fixed
- Keycloak user sync returning 403 by authenticating as the master-realm admin

## [0.4.57] - 2026-06-15

### Changed
- Updated metaseed to v0.8.1

## [0.4.55] - 2026-06-10

### Changed
- Add changelog

## [0.4.53] - 2026-06-10

### Added
- Pre-push hook that runs tests before pushing
- Support for `ontologies` field in spec builder
- Auto-convert spec names to lowercase slug format

### Fixed
- Draft spec profile case mismatch causing "Version not found" errors
- Dataset name field width in create dataset form
- Button layout in dataset creation UI

### Changed
- Updated metaseed to v0.7.7

## [0.4.52] - 2026-06-08

### Removed
- Non-functional export buttons from explorer

## [0.4.51] - 2026-06-07

### Added
- Configurable OAuth scope for different IdPs

## [0.4.50] - 2026-06-06

### Fixed
- Create dataset versions when saving via EntityService
- Ensure all save paths create version history

## [0.4.40-0.4.49] - 2026-05-26 to 2026-06-05

### Added
- Admin role configuration for SRAM
- Version display on login page
- Comment pagination and compact UI

### Fixed
- Spec export download and session timeout
- Date serialization to ISO strings for JSONB storage
- Pydantic URL type handling in JSON serialization
- Modal dialog text colors and input styling

### Changed
- Updated metaseed to v0.7.4

## [0.4.30-0.4.39] - 2026-05-20 to 2026-05-25

### Added
- Field reordering in spec builder
- Ontology demo spec
- Single-select ontology modal fix

### Fixed
- Redirect to home after dataset delete
- Use MetaseedClient.load() for graph operations
- Skip validation on entity save, show warnings instead

### Changed
- Migrated to MetaseedClient public API
- Removed global dataset_states cache

## [0.4.20-0.4.29] - 2026-05-15 to 2026-05-19

### Fixed
- Preserve nested entity values when saving
- Skip 'Field required' warning for nested fields with partial data
- Use cached state for draft specs

### Changed
- Updated metaseed to v0.3.9

## [0.4.10-0.4.19] - 2026-05-10 to 2026-05-14

### Added
- YAML copy button
- Show shared specs in Create Dataset

### Fixed
- Load draft specs from database for dataset mutations
- Disable cache for draft specs
- Preserve entities even when validation fails

## [0.4.0-0.4.9] - 2026-05-01 to 2026-05-09

### Added
- GitHub issues link to footer
- OLS ontology API integration
- Comprehensive RBAC tests for datasets and specs

### Fixed
- Show shared datasets on home page
- Grant dataset access via DatasetMember sharing

### Refactored
- Split spec_builder router into focused route modules
- Extract auth dependency and add type aliases

## [0.3.0-0.3.9] - 2026-04-15 to 2026-04-30

### Added
- Threaded comments with reactions
- Excel template export/import workflow
- Auto-versioning for datasets on save
- Detailed git-style diff view for version history
- Delete button to dataset sidebar
- User profile page showing SRAM info

### Fixed
- State caching to prevent data loss on save
- Refresh root type buttons on entity create/delete

### Refactored
- Renamed Project to Dataset throughout codebase
- Simplified dataset UI and navigation

## [0.2.0-0.2.9] - 2026-04-01 to 2026-04-14

### Added
- Explorer tool with dark header navigation
- Workspace members and spec draft integration
- Delete button for spec drafts
- Import/export and single entity field support
- DatabaseDatasetRepository for metaseed DI integration

### Fixed
- Footer visibility and layout
- Version info display
- Full-page layout handling

### Changed
- Initial metaseed integration (v0.2.0-0.2.4)
## [0.4.49] - 2026-06-08

### Changed
- Pin metaseed to v0.7.4

## [0.4.48] - 2026-06-08

### Changed
- Add contract tests for metaseed serialize() JSON output
- Update metaseed to main branch with JSON serialization fix

## [0.4.47] - 2026-06-08

### Changed
- Handle Pydantic URL types in JSON serialization

## [0.4.46] - 2026-06-08

### Changed
- Serialize date objects to ISO strings before JSONB storage

## [0.4.45] - 2026-06-08

### Changed
- Spec export download and session timeout
- Bump starlette from 1.0.0 to 1.0.1 (#11)

## [0.4.44] - 2026-06-04

### Changed
- Hide admin button until SRAM configured

## [0.4.43] - 2026-06-04

### Changed
- Widen profile page for long SRAM entitlements

## [0.4.42] - 2026-06-04

### Changed
- Request eduperson_entitlement scope from SRAM

## [0.4.41] - 2026-06-04

### Changed
- Show version on login page

## [0.4.40] - 2026-06-04

### Changed
- Add admin_role to Settings for pydantic validation

## [0.4.39] - 2026-06-04

### Changed
- Add SRAM admin role configuration
- Update metaseed to v0.7.0
- Add comment pagination and compact UI improvements

## [0.4.38] - 2026-06-01

### Changed
- Modal dialog text colors and input styling

## [0.4.37] - 2026-06-01

### Changed
- Add text color to modal dialog
- Redirect to home after dataset delete
- Add field reordering, ontology demo spec, single-select ontology fix
- Use MetaseedClient.load() in ensure_dataset_facade for graph
- Migrate to MetaseedClient public API

## [0.4.36] - 2026-05-28

### Changed
- Remove global dataset_states cache, always load from DB

## [0.4.35] - 2026-05-28

### Changed
- Upgrade metaseed to v0.3.9

## [0.4.34] - 2026-05-28

### Changed
- Debug: add logging to add_table_row and check for null facade
- Skip 'Field required' warning for nested fields with partial data
- Use cached state for draft specs to preserve in-memory changes
- Debug: add logging to single entity field save flow
- Preserve nested entity values when saving main form

## [0.4.33] - 2026-05-28

### Changed
- Merge single entity field values instead of replacing

## [0.4.32] - 2026-05-28

### Changed
- Don't auto-fill reference fields with random UUIDs

## [0.4.31] - 2026-05-28

### Changed
- Ignore tests requiring PostgreSQL

## [0.4.30] - 2026-05-28

### Changed
- Skip validation on entity save, show warnings instead

## [0.4.29] - 2026-05-28

### Changed
- Add comprehensive error handling to entity save flow

## [0.4.28] - 2026-05-28

### Changed
- Always use model_construct to skip validation on entity save

## [0.4.27] - 2026-05-28

### Changed
- Remove dead code, simplify entity save flow

## [0.4.26] - 2026-05-28

### Changed
- Allow saving entities without required nested entities

## [0.4.25] - 2026-05-28

### Changed
- Debug: add logging to deserialize_tree to trace entity loading

## [0.4.24] - 2026-05-28

### Changed
- Verify entities with validation errors still appear in sidebar

## [0.4.23] - 2026-05-28

### Changed
- Preserve entities even when validation fails during deserialization

## [0.4.22] - 2026-05-28

### Changed
- Disable cache for draft specs and improve error handling

## [0.4.21] - 2026-05-28

### Changed
- Ensure draft specs are loaded for all dataset operations
- Load draft specs from database for dataset mutations

## [0.4.20] - 2026-05-28

### Changed
- Serialize spec enums as strings in YAML export

## [0.4.19] - 2026-05-28

### Changed
- Add YAML copy button, show shared specs in Create Dataset

## [0.4.18] - 2026-05-28

### Changed
- Use metaseed OntologyService for OLS lookups
- Add GitHub issues link to footer

## [0.4.17] - 2026-05-28

### Changed
- Grant dataset access via DatasetMember sharing

## [0.4.16] - 2026-05-28

### Changed
- Add comprehensive RBAC tests for datasets and specs
- Show shared datasets on home page, add tests

## [0.4.15] - 2026-05-28

### Changed
- Add test for home page showing shared specs
- Add OLS ontology API and fix shared specs visibility

## [0.4.14] - 2026-05-26

### Changed
- Extract auth dependency and add type aliases

## [0.4.13] - 2026-05-26

### Changed
- Split spec_builder router.py into focused route modules

## [0.4.12] - 2026-05-26

### Changed
- Handle None values for description and root_entity in dict_to_spec

## [0.4.11] - 2026-05-26

### Changed
- Remove stale keycloak_id lookups in spec_builder_list and edit_draft
- Make user ID handling robust and consistent
- Owned_drafts query to use users.id not keycloak_id

## [0.4.10] - 2026-05-26

### Changed
- Add comprehensive spec draft sharing tests

## [0.4.9] - 2026-05-26

### Changed
- Shared drafts query filter by tenant, add sharing tests

## [0.4.8] - 2026-05-26

### Changed
- Filter owner query by tenant, show disabled dropdown for owner

## [0.4.7] - 2026-05-26

### Changed
- Show owner in sharing panel even if User record not found

## [0.4.6] - 2026-05-26

### Changed
- Include shared drafts in explorer

## [0.4.5] - 2026-05-26

### Changed
- Use display_name from spec_data in explorer

## [0.4.4] - 2026-05-26

### Changed
- Show shared spec drafts to members, allow member access
- Improve sharing UI, add docs link, fix sidebar tabs
- Upgrade metaseed to v0.3.7
- Add user to spec_builder render context for navbar avatar

## [0.4.3] - 2026-05-26

### Changed
- Show user initials in circle avatar in navbar

## [0.4.2] - 2026-05-26

### Changed
- Add user profile page showing SRAM info
- Show error when sharing with user who hasn't logged in yet
- Pin metaseed to v0.3.3
- Ensure redirect works after dataset delete
- Add profile/version selection to dataset import
- Add New Dataset button to empty state
- Remove Edit Specification button from published spec view
- Improve delete error handling with toast notifications
- Manually cascade delete dataset relations
- Refresh root type buttons on entity create/delete
- Clear state cache on dataset delete

## [0.4.1] - 2026-05-21

### Changed
- Add delete button to dataset sidebar

## [0.4.0] - 2026-05-21

### Changed
- Upgrade metaseed to latest main (0.3.3.dev11)
- Add state caching tests to prevent data loss regression
- Use cached state in ensure_dataset_facade to prevent data loss on save
- Add detailed git-style diff view for version history
- Use tabs for overview (History, Comments, Sharing)
- Overview route returns full overview content with version history
- Move history to overview page with diffs, comments/sharing to collapsibles
- Add auto-versioning for datasets on save
- Remove template export feature
- Add import button to dataset page for updating existing datasets
- Add Excel template export/import workflow
- Add threaded comments with reactions to datasets and specs
- …and 15 further changes; see git history.

## [0.3.11] - 2026-05-12

### Changed
- Add toast notification styles for save messages

## [0.3.10] - 2026-05-12

### Changed
- Add YAML spec import and fix explore page spec loading

## [0.3.9] - 2026-05-12

### Changed
- Show version footer on login page

## [0.3.8] - 2026-05-12

### Changed
- Add prompt=consent for offline_access scope

## [0.3.7] - 2026-05-12

### Changed
- Add refresh token support for longer sessions
- Bump urllib3 from 2.6.3 to 2.7.0 (#6)

## [0.3.6] - 2026-05-11

### Changed
- Update metaseed to v0.2.4
- Use ensure_project_facade in all project routes
- Load spec before deserializing tree
- Handle SpecBuilderState format in spec_data
- Support user-defined specs for projects
- Show actual error messages in explorer

## [0.3.5] - 2026-05-11

### Changed
- Show user drafts and published specs in explorer
- Use fixed height viewport for erd-layout pages
- Make erd-layout fill vertical space with flex
- Add erd-layout to full-page layout handling
- Add smoke tests for explorer and static assets
- Install all extras in CI for dev dependencies
- Include metaseed CSS for explorer layout styles
- Correct erd-common.js path in explorer template

## [0.3.4] - 2026-05-08

### Changed
- Reinstall package on deploy for clean version
- Fetch tags in deploy script for clean versions
- Use base.html for chat and explore pages
- Update metaseed to v0.2.3, removes vulnerable deps
- Update metaseed to v0.2.2
- Reduce code duplication in UI routes and helpers
- Correct health check URL and add version to response
- Allow systemctl status with flags in sudoers

## [0.3.3] - 2026-05-07

### Changed
- Add import/export and single entity field support

## [0.3.2] - 2026-05-07

### Changed
- Pin metaseed to v0.2.0

## [0.3.1] - 2026-05-07

### Changed
- Improve workspace and project UI styling
- Force git fetch to handle tag conflicts
- Improve deploy script robustness and error handling
- Bump python-multipart from 0.0.26 to 0.0.27 (#5)
- Bump mako from 1.3.11 to 1.3.12 (#4)

## [0.3.0] - 2026-05-06

### Changed
- Add workspace members and spec draft integration
- Add Explorer tool and dark header navigation
- Add delete button for spec drafts
- Use window.entities instead of const for spec builder
- Use calc height for layouts and make version text visible
- Use height 100% for full-page layouts
- Make hub-main a flex container for full-page layouts
- Add version_info to spec builder templates
- Use flex layout instead of fixed height for full-page layouts
- Move footer inside hub-layout so it's visible
- Host vis-network locally instead of CDN
- Include commit info in version display

## [0.2.0] - 2026-05-04

### Changed
- Use git reset instead of pull in deploy script
- Add /version endpoint
- Add version_info to shared render_template
- Add CI/CD badges to README
- Ignore database tests in CI, add live link to README
- Display package version in footer
- Update test imports for serialize/deserialize_tree
- Clean up README
- Consolidate UI code and add security hardening
- Remove unused valid_count variable
- Remove debug logging
- Use metaseed's build_graph for proper ISA relationship visualization
- …and 90 further changes; see git history.

## [0.1.0] - 2026-05-01

### Changed
- UI improvements and infrastructure hardening
- Look for examples in /app/examples on server
- Add load example data button and make delete button visible
- Use alias for csrf_token form field
- Accept CSRF token from form data in addition to header
- Remove debug logging
- Support all OIDC signing algorithms from discovery
- Debug: add token verification error logging
- Debug: add token exchange error logging
- Use OIDC discovery for auth endpoints
- Use generic OIDC instead of Keycloak-specific auth
- Ignore app user home files
- …and 55 further changes; see git history.

