"""An in-memory Notion API (2025-09-03 data sources) for the Notion adapter's tests.

Strict where drift would hide: page writes must name properties the data source has, with the
right type, and relations must point at pages of the relation's data source. As Notion does, dual
relations keep their synced property up to date (and go with it when deleted), and a page write
that names a new select option adds it.
"""

import itertools
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BOT, CHAIR = "bot-0001", "person-chair"


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())


def _read(kind, v):
    """A page-write value as Notion returns it."""
    if kind in ("title", "rich_text"):
        return [{"type": "text", "text": {"content": x["text"]["content"]}, "plain_text": x["text"]["content"]}
                for x in v]
    if kind == "date" and v and "T" in v["start"]:  # Notion keeps a date-time to the minute
        return {**v, "start": v["start"][:16] + ":00.000+00:00"}
    return v


class FakeNotion:
    def __init__(self):
        self.ids = (f"{i:08x}-0000-4000-8000-000000000000" for i in itertools.count(1))
        self.ds, self.pages, self.lock, self.requests = {}, {}, threading.Lock(), 0
        self.drop_next_create = False  # the network loses the reply to one page create
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    # ── test hooks: what a person does in the Notion app ──
    def human_edit(self, page_id, props, user=CHAIR):
        with self.lock:
            self._write(self.pages[page_id], props)
            self.pages[page_id].update(last_edited_by={"object": "user", "id": user}, last_edited_time=_now())

    def human_create(self, ds, props, user=CHAIR):
        with self.lock:
            return self._create(ds, props, user)["id"]

    def trash(self, page_id):
        with self.lock:
            self.pages[page_id]["in_trash"] = True

    def delete_forever(self, page_id):
        with self.lock:
            del self.pages[page_id]

    def drop_property(self, ds, name):
        with self.lock:
            del self.ds[ds]["properties"][name]

    def in_ds(self, ds):
        return [p for p in self.pages.values() if p["parent"]["data_source_id"] == ds and not p["in_trash"]]

    # ── the API ──
    def _schema_prop(self, name, spec):
        kind = next(k for k in spec if k not in ("name", "description"))
        prop = {"id": name[:4], "name": name, "type": kind, kind: dict(spec[kind])}
        if kind == "relation" and spec[kind].get("type") == "dual_property":
            synced = spec[kind]["dual_property"].get("synced_property_name") or "Related"
            return prop, (spec[kind]["data_source_id"], synced)
        return prop, None

    def _set_props(self, ds_id, props):
        for name, spec in props.items():
            if spec is None:  # deleting one side of a dual relation deletes the other, as Notion does
                gone = self.ds[ds_id]["properties"].pop(name, None)
                rel = (gone or {}).get("relation") or {}
                if rel.get("type") == "dual_property":
                    self.ds[rel["data_source_id"]]["properties"].pop(rel["dual_property"]["synced_property_name"], None)
                continue
            prop, dual = self._schema_prop(name, spec)
            self.ds[ds_id]["properties"][name] = prop
            if dual:
                target, synced = dual
                prop["relation"]["dual_property"]["synced_property_name"] = synced
                self.ds[target]["properties"][synced] = {
                    "id": synced[:4], "name": synced, "type": "relation",
                    "relation": {"data_source_id": ds_id, "type": "dual_property",
                                 "dual_property": {"synced_property_name": name}}}

    def _write(self, page, props):
        schema = self.ds[page["parent"]["data_source_id"]]["properties"]
        for name, value in props.items():
            if name not in schema:
                raise ValueError(f"{name} is not a property of this data source")
            kind = schema[name]["type"]
            if set(value) != {kind}:
                raise ValueError(f"{name} is a {kind} property, got {list(value)}")
            v = value[kind]
            if kind in ("select", "multi_select"):  # Notion adds an option a page write names
                opts = schema[name][kind].setdefault("options", [])
                for o in ([v] if kind == "select" else v):
                    if o and o["name"] not in {x["name"] for x in opts}:
                        opts.append({"name": o["name"]})
            if kind == "relation":
                target = schema[name]["relation"]["data_source_id"]
                for ref in v:
                    if self.pages[ref["id"]]["parent"]["data_source_id"] != target:
                        raise ValueError(f"{name} must point at pages of {target}")
                rel = schema[name]["relation"]
                if rel.get("type") == "dual_property":
                    synced = rel["dual_property"]["synced_property_name"]
                    for other in self.pages.values():  # drop old back-references, add the new ones
                        if other["parent"]["data_source_id"] != target:
                            continue
                        refs = other["properties"].get(synced, {}).get("relation")
                        if refs is not None:
                            other["properties"][synced]["relation"] = [r for r in refs if r["id"] != page["id"]]
                    for ref in v:
                        tgt = self.pages[ref["id"]]["properties"].setdefault(synced, {"type": "relation", "relation": []})
                        tgt["relation"].append({"id": page["id"]})
            page["properties"][name] = {"id": schema[name]["id"], "type": kind, kind: _read(kind, v)}

    def _create(self, ds, props, user):
        pid = next(self.ids)
        page = {"object": "page", "id": pid, "parent": {"type": "data_source_id", "data_source_id": ds},
                "properties": {}, "in_trash": False, "last_edited_by": {"object": "user", "id": user},
                "last_edited_time": _now()}
        self.pages[pid] = page
        self._write(page, props)
        return page

    def handle(self, method, path, body):
        self.requests += 1
        with self.lock:
            if path == "/v1/users/me":
                return 200, {"object": "user", "id": BOT, "type": "bot"}
            if path == "/v1/users":
                return 200, {"results": [{"id": CHAIR, "type": "person"}, {"id": BOT, "type": "bot"}]}
            if path == "/v1/databases" and method == "POST":
                db, ds = next(self.ids), next(self.ids)
                self.ds[ds] = {"object": "data_source", "id": ds, "properties": {}}
                self._set_props(ds, body["initial_data_source"]["properties"])
                return 200, {"object": "database", "id": db, "data_sources": [{"id": ds}]}
            m = re.fullmatch(r"/v1/data_sources/([^/]+)(/query)?", path)
            if m:
                ds = m.group(1)
                if m.group(2):
                    return 200, {"results": [json.loads(json.dumps(p)) for p in self.in_ds(ds)], "has_more": False}
                if method == "PATCH":
                    self._set_props(ds, body["properties"])
                return 200, self.ds[ds]
            if path == "/v1/pages" and method == "POST":
                return 200, self._create(body["parent"]["data_source_id"], body["properties"], BOT)
            m = re.fullmatch(r"/v1/pages/([^/]+)", path)
            if m:
                page = self.pages.get(m.group(1))
                if page is None:
                    return 404, {"object": "error", "status": 404, "code": "object_not_found"}
                if method == "PATCH":
                    if "properties" in body:
                        self._write(page, body["properties"])
                    if "in_trash" in body:
                        page["in_trash"] = body["in_trash"]
                    page.update(last_edited_by={"object": "user", "id": BOT}, last_edited_time=_now())
                return 200, json.loads(json.dumps(page))
        return 404, {"object": "error", "status": 404, "message": f"no route {method} {path}"}

    def _handler(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def _do(self, method):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else {}
                try:
                    code, out = fake.handle(method, self.path, body)
                except (ValueError, KeyError) as e:
                    code, out = 400, {"object": "error", "status": 400, "message": str(e)}
                if fake.drop_next_create and method == "POST" and self.path == "/v1/pages":
                    fake.drop_next_create = False
                    self.close_connection = True
                    return
                data = json.dumps(out).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # noqa: N802
                self._do("GET")

            def do_POST(self):  # noqa: N802
                self._do("POST")

            def do_PATCH(self):  # noqa: N802
                self._do("PATCH")

            def log_message(self, format, *args):  # noqa: A002
                pass

        return H
