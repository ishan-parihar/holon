# Holon ⇄ Notion

A live mirror of the organization in Notion: departments, employees, the work holarchy (every
work node and its stages) and runs, with their relations, plus a way back in for the Chair's
decisions. Holon's SQLite stays the source of truth; Notion is the Chair's dashboard.

| Data source | Rows | Properties | Relations (each dual: Notion keeps the reverse side) |
|---|---|---|---|
| Holon · Departments | `departments` | Name, Holon ID, Slug, Mission, Profile▾, Status▾, Created | Parent department ↔ Sub-departments; Head ↔ Head of; ← Employees, Work |
| Holon · Employees | `employees` | Name, Holon ID, Title, Roles▾▾, Task classes▾▾, Status▾, Driver▾, Job description, Hired | Department ↔ Employees; Reports to ↔ Direct reports; ← Head of, Assigned work, Approvals, Runs |
| Holon · Work | `tasks` (work nodes and their stages) | Title, Holon ID, Stage▾, Status▾, Outcome▾, Grade▾, Grade reasons, Priority▾, Risk▾, Source▾, Task class▾, Chair☑, Intent, Acceptance, Blocked reason, Decision note, Result, Workdir, Due, Created, Updated; Chair inputs: Chair decision▾, Chair note, Chair answer | Parent ↔ Children; Needs ↔ Needed by; Department ↔ Work; Assignee ↔ Assigned work; Approver ↔ Approvals; ← Runs |
| Holon · Runs | `runs` | Run, Holon ID, Stage kind▾, Status▾, Driver▾, Exit code, Tokens in/out, Cost, Started, Ended | Stage ↔ Runs; Employee ↔ Runs |

▾ select, ▾▾ multi-select, ☑ checkbox (Chair: the decision is the Chair's — filter on it for your
queue). The rules, enforced by `tests/test_notion.py`:

- a column SQLite constrains to a set of values is a select, built from `holon/core/vocab.py`
  (profiles, drivers and task classes come from the instance, and Notion adds a new one on write);
- an id that points at another row is a relation, and every relation is dual;
- every column of the four tables is mirrored or listed in `UNMIRRORED` with where its
  information lives instead (e.g. `key_hash` never leaves the kernel; `depth` is the Parent chain).
  A new kernel column fails the test until someone decides which.

## What flows back

Only the Chair's Notion account(s) (`chair_users` in `notion.json`) can act, in exactly three ways:

- **Chair decision** = approve / reject (and an optional **Chair note**) on an `approve` stage;
- **Chair answer** on work whose status is `needs_input`;
- a new page in *Holon · Work* without a Holon ID becomes new work (Title, Intent, Acceptance,
  Department, Priority, Risk, Task class, Due).

Anything else edited in Notion, by anyone, is overwritten by the next push, and a trashed page is
restored. That is what keeps the two from drifting: Notion never holds a fact Holon does not.

## How it stays in sync

- **Push** (every 20 s in `serve`): each row's mapped values and relation targets are hashed; only
  changed rows are written, pages first and relations second.
- **Webhooks**: Notion calls `serve` within about a minute of a change; events are verified with the
  subscription's token (`X-Notion-Signature`, HMAC-SHA256) and our own writes are ignored. The
  receiver answers only on a secret path (`webhook_path` in `notion.json`, printed by `serve`), so
  nobody who has not been told the URL can reach it or plant a verification token; bodies over
  1 MB are refused.
- **Poll** (every minute; the fallback when webhooks are off, late or lost): asks each data source
  only for pages edited since the last poll (`last_edited_time`), and acts on those a person edited.
  Outbound needs no dates: every push compares each row's hash with what was last written.
- **Reconcile** (every 15 min): the full pass. Handles edits since the last pass, restores trashed
  pages, re-creates deleted ones and trashes orphaned twins.
- **Schema**: `check` diffs every data source against `holon_notion.py`; `check --repair` upgrades
  it in place (adds, recreates a property whose type changed, removes retired ones, then re-pushes
  the values). A schema webhook (someone edited a database's properties) triggers the repair.
- **Verify**: `verify` compares every live page, value and relation, both ends, with SQLite.
  `serve` runs it hourly and re-pushes everything when it finds drift (self-healing).
- **Deleted for good**: the page is recreated on the next push, and every row is re-pushed so the
  relations that named the old page name the new one.

## Set up

On a new machine, from the repository root:

```sh
python3 install.py --notion-parent-page <page id> --notion-token-file ~/.config/holon/notion.token \
    --notion-webhook-hostname holon-notion.<your domain> --systemd
```

does steps 3, 4 and 6 below along with the SQLite instance: the four databases, a dedicated
Cloudflare tunnel (`holon-notion`, needs `cloudflared tunnel login` once) routing only
`/notion/…` to the receiver, and the `holon-notion` and `cloudflared-holon-notion` user units. It
checks the webhook URL answers and prints it for step 5, the one step only you can do in Notion.
Without `--notion-webhook-hostname` the sync polls Notion every minute instead. By hand:

1. In Notion, create an internal connection (Settings → Connections → Develop or manage
   integrations) with *Read content*, *Update content*, *Insert content* and *Read user
   information*. Save its token to a file only you can read:
   `install -m 600 /dev/stdin ~/.config/holon/notion.token`.
2. Create a page for the dashboard and share it with the connection (••• → Connections).
3. Create the databases and the first push:

   ```sh
   python3 adapters/notion/holon_notion.py --holon-home ~/holon setup \
       --parent-page <page id> --token-file ~/.config/holon/notion.token [--chair-user <your user id>]
   ```

4. Run the sync and give it a public HTTPS URL (Notion must reach it). A dedicated Cloudflare
   tunnel keeps a stable hostname (`cloudflared tunnel create holon-notion`, `cloudflared tunnel
   route dns holon-notion <hostname>`, ingress `<hostname>` → `http://localhost:8787`); don't add the
   hostname to a tunnel that has several connectors with different configs, or some requests 404:

   ```sh
   python3 adapters/notion/holon_notion.py --holon-home ~/holon serve --port 8787
   ```

5. In the connection's **Webhooks** tab, subscribe `https://<hostname><webhook_path>` (the path
   `serve` prints) to page and data source events. Notion
   posts a verification token; `serve` stores it in `notion.json` (`webhook_token`) and says so.
   Paste it into Notion's *Verify* dialog. *Resend* is fine: each token replaces the last.
6. To keep it running: `adapters/notion/holon-notion.service` (systemd user unit).

`status` shows the page counts, pending rows and the last events; `push` and `reconcile` run one
pass by hand.

Network: dropped connections are retried; a page create whose reply was lost is created again and
the next `reconcile` trashes the untracked twin. IPv4 addresses are tried first: Python's urllib
waits out each address in turn, so a host with a dead IPv6 route would otherwise hang on every call.

Verified live on 2026-10-13 against the LifeOS workspace (page "Holon (local test)"): setup, schema
check, push of 2 departments, 3 employees, 7 work nodes and 5 runs, then 54 relation values compared
with SQLite (0 mismatches) and a second push writing nothing.

Webhooks verified live the same day through the `holon-notion` tunnel: a Chair edit to a
department's Mission was caught by the poll after 15 s and reverted 2 s later; Notion's signed
`page.properties_updated` event arrived 60 s after the edit (Notion batches property events for about
a minute). So a Chair edit lands within about a minute by whichever path sees it first. A webhook
the receiver refuses is logged (`webhook refused …`), so a token mismatch cannot lose events silently.
