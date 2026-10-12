# Holon ⇄ Notion

A live mirror of the organization in Notion: departments, employees, the work holarchy (every
work node and its stages) and runs, with their relations, plus a way back in for the Chair's
decisions. Holon's SQLite stays the source of truth; Notion is the Chair's dashboard.

| Data source | Rows | Relations (Notion keeps the reverse side) |
|---|---|---|
| Holon · Departments | `departments` | Parent department, Head → Employees; ← Employees |
| Holon · Employees | `employees` | Department ↔ Employees, Reports to |
| Holon · Work | `tasks` (work nodes and their stages) | Parent ↔ Children, Needs, Department, Assignee; ← Runs |
| Holon · Runs | `runs` | Stage ↔ Runs, Employee |

Select options (stages, statuses, roles, risks, grades, sources) are generated from
`holon/core/vocab.py`, so a vocabulary change shows up as schema drift (`check`) and is fixed by
`check --repair`.

## What flows back

Only the Chair's Notion account(s) (`chair_users` in `notion.json`) can act, in exactly three ways:

- **Chair decision** = approve / reject (and an optional **Chair note**) on an `approve` stage;
- **Chair answer** on work whose status is `needs_input`;
- a new page in *Holon · Work* without a Holon ID becomes new work (Title, Intent, Acceptance,
  Department).

Anything else edited in Notion, by anyone, is overwritten by the next push, and a trashed page is
restored. That is what keeps the two from drifting: Notion never holds a fact Holon does not.

## How it stays in sync

- **Push** (every 20 s in `serve`): each row's mapped values and relation targets are hashed; only
  changed rows are written, pages first and relations second.
- **Webhooks**: Notion calls `serve` within about a minute of a change; events are verified with the
  subscription's token (`X-Notion-Signature`, HMAC-SHA256) and our own writes are ignored.
- **Reconcile** (every 5 min): Notion delivers webhooks at most once and aggregates them, so a
  periodic pass handles edits made since the last pass, restores trashed pages and re-creates
  deleted ones.
- **Schema**: `check` diffs every data source against `holon_notion.py`; `check --repair` fixes it.

## Set up

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

4. Run the sync and give it a public HTTPS URL (Notion must reach it), e.g. `tailscale funnel 8787`
   or `cloudflared tunnel --url http://127.0.0.1:8787`:

   ```sh
   python3 adapters/notion/holon_notion.py --holon-home ~/holon serve --port 8787
   ```

5. In the connection's **Webhooks** tab, subscribe that URL to page and data source events. Notion
   posts a verification token; `serve` stores it in `notion.json` (`webhook_token`) and says so.
   Paste it into Notion's *Verify* dialog.
6. To keep it running: `adapters/notion/holon-notion.service` (systemd user unit).

`status` shows the page counts, pending rows and the last events; `push` and `reconcile` run one
pass by hand.
