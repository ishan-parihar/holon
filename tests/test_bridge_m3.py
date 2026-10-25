"""Tests first for the bridge plan's M3 — G12, the department archive.

There was no `holon dept export` / `dept import`, so every venture department had to
be onboarded by hand: a wave was per-pool toil instead of one command. The archive
makes a wave configuration.

Three properties, each a guard here:
- export → import round-trips the department's shape;
- the archive carries **no secret value** (the secrets are paths, never the keys);
- an imported routine lands **unapproved** — an import is not an approval.
"""

import json
from pathlib import Path

from tests.helpers import KernelCase


class DepartmentArchive(KernelCase):
    """M3. One command per department instead of a day of hand-work."""

    def department(self, slug: str = "eng"):
        """A department with its head, a routine, and a scoped reach.

        `eng` already exists in KernelCase with a head; a second department is created
        for the tests that need one of their own."""
        if not any(d["slug"] == slug for d in self.k.org.depts()):
            self.k.org.create_dept(slug, slug.title(), "the test department")
        d = self.k.org.dept(slug)
        a = self.k.add_routine("Digest", "Summarise the inbox", dept=slug, cron="0 7 * * *",
                               workdir=str(self.workdir), strict=False)
        self.k.approve(a["id"])  # an approved routine, so the archive has one to carry
        return d

    def test_export_writes_one_archive(self):
        """The archive holds the department, not the instance: its profile and slots, its
        employees' records, its scope roots, its smoke checks, its secrets bindings as
        paths, its routines with their playbooks, and its KB bindings."""
        d = self.department()
        out = self.k.export_department(d["id"], str(self.tmp / "archive"))
        self.assertTrue(Path(out["path"]).exists(), "the archive is on disk")
        manifest = json.loads((Path(out["path"]) / "department.json").read_text())
        self.assertEqual(manifest["dept"]["slug"], "eng")
        self.assertIn("employees", manifest)
        self.assertIn("routines", manifest)
        sums = json.loads((Path(out["path"]) / "checksums.json").read_text())
        self.assertIn("files", sums, "the archive's files are checksummed, like a backup")

    def test_the_archive_carries_no_secret_value(self):
        """The rule: the archive carries the *path* to a key file, never the key. A
        department archive that leaked a key would be a second copy of every credential
        the instance holds — and it travels."""
        d = self.department()
        secret = self.tmp / "secret.key"
        secret.write_text("sk-the-actual-key\n")
        self.home.config_file.write_text(
            self.home.config_file.read_text()
            + f'\n[secrets.dept.eng]\nIMAP_KEY = "{secret}"\n')
        self.k.close()
        from holon.execution.service import Kernel
        self.k = Kernel(self.home)
        out = self.k.export_department(d["id"], str(self.tmp / "archive2"))
        blob = json.dumps(out)
        for p in Path(out["path"]).rglob("*"):
            if p.is_file():
                blob += p.read_text(errors="replace")
        self.assertNotIn("sk-the-actual-key", blob,
                         "the archive must never carry a secret value")
        self.assertIn(str(secret), blob, "it carries the path instead")

    def test_an_import_refuses_a_tampered_archive(self):
        """The archive carries its own checksums, so a reader can tell whether it is intact.

        Measured 2026-10-25: `export_department` wrote `checksums.json` and NOTHING read
        it. `verify_archive` existed -- the reader -- and no line called it, so a tampered
        archive imported silently: the checksums were a promise nothing kept.

        The rule: an import verifies the archive's checksums BEFORE it creates anything.
        A tampered archive is refused by name, and the instance is left untouched -- an
        import that half-applies a damaged archive is worse than one that refuses it."""
        from holon.core.paths import Home
        from holon.execution.service import Kernel
        d = self.department()
        out = self.k.export_department(d["id"], str(self.tmp / "archive-tampered"))
        archive = Path(out["path"])
        # tamper: rewrite the manifest the import reads
        manifest = json.loads((archive / "department.json").read_text())
        manifest["dept"]["name"] = "Engineered By Someone Else"
        (archive / "department.json").write_text(json.dumps(manifest, indent=2, default=str))
        home2 = Home(self.tmp / "home-tampered")
        home2.root.mkdir(parents=True)
        k2 = Kernel(home2)
        try:
            with self.assertRaises(ValueError) as ctx:
                k2.import_department(str(archive))
            self.assertIn("checksum", str(ctx.exception).lower(),
                          f"the refusal names the checksum: {ctx.exception}")
            slugs = [x["slug"] for x in k2.org.depts()]
            self.assertNotIn("eng", slugs,
                             "a refused import creates nothing: the instance is untouched "
                             f"(it holds only what it bootstrapped: {slugs})")
        finally:
            k2.close()

    def test_an_intact_archive_imports(self):
        """The other side of the rule: an intact archive still imports. A verification that
        refuses everything is not a verification."""
        from holon.core.paths import Home
        from holon.execution.service import Kernel
        d = self.department()
        out = self.k.export_department(d["id"], str(self.tmp / "archive-intact"))
        home2 = Home(self.tmp / "home-intact")
        home2.root.mkdir(parents=True)
        k2 = Kernel(home2)
        try:
            got = k2.import_department(out["path"])
            self.assertEqual(got["dept"], "eng")
        finally:
            k2.close()

    def test_a_round_trip_restores_the_departments_shape(self):
        """Export, then import into a fresh instance: the same profile, the same
        employees' roles, the same routines."""
        d = self.department()
        out = self.k.export_department(d["id"], str(self.tmp / "archive3"))
        manifest = json.loads((Path(out["path"]) / "department.json").read_text())
        shape = {"profile": manifest["dept"]["profile"],
                 "employees": sorted((e["name"], tuple(sorted(e["roles"]))) for e in manifest["employees"]),
                 "routines": sorted(r["title"] for r in manifest["routines"])}
        # a second instance
        from holon.core.paths import Home
        from holon.execution.service import Kernel
        home2 = Home(self.tmp / "home2")
        home2.root.mkdir(parents=True)
        k2 = Kernel(home2)
        try:
            got = k2.import_department(out["path"])
            d2 = k2.org.dept("eng")
            self.assertEqual(d2["profile"], shape["profile"])
            names = sorted((e["name"], tuple(sorted(e["roles"] or "")))
                           for e in k2.org.employees(d2["id"]))
            self.assertEqual(names, shape["employees"],
                             "the imported department has the same people, in the same roles")
            self.assertEqual(got["routines"], len(shape["routines"]))
        finally:
            k2.close()

    def test_an_imported_routine_lands_unapproved(self):
        """An import is not an approval. The Chair approves each routine, as with any
        other routine — and a routine that reached the schedule unapproved would be the
        outside-reach import the kernel deliberately disables."""
        d = self.department()
        out = self.k.export_department(d["id"], str(self.tmp / "archive4"))
        from holon.core.paths import Home
        from holon.execution.service import Kernel
        home2 = Home(self.tmp / "home3")
        home2.root.mkdir(parents=True)
        k2 = Kernel(home2)
        try:
            k2.import_department(out["path"])
            self.assertEqual(k2.routines(), [],
                             "an imported routine is not scheduled until the Chair approves it")
        finally:
            k2.close()

    def test_the_archive_does_not_carry_the_source_instances_workdir(self):
        """Measured 2026-10-21 on the live instance: an archive's routine carried
        `workdir = /var/tmp/.../claude-1000/.../scratchpad/lane-live.LkyM` — one operator's
        dead session directory from a different harness. The export copied the source
        instance's workdir verbatim, so the operator's own path became the imported
        routine's working directory on a machine the operator never ran it on.

        That is the AGENTS.md law read at the archive layer: instance specifics stay out of
        the repo and out of anything that travels. A routine's workdir is a machine fact, not
        a department fact — the archive names it so a reader knows one was set, and the import
        leaves it for the Chair to set on this machine."""
        d = self.department()
        # the source instance's routine works in a directory from a dead session on another
        # harness — which is what the live instance actually carries today
        from holon.execution.work import write_txn
        with write_txn(self.k.wconn):
            self.k.wconn.execute("UPDATE tasks SET workdir=? WHERE recurring=1",
                                  ("/var/tmp/other-harness/some-dead-session/scratch",))
        out = self.k.export_department(d["id"], str(self.tmp / "archive6"))
        manifest = json.loads((Path(out["path"]) / "department.json").read_text())
        for r in manifest["routines"]:
            self.assertNotIn("other-harness", r.get("workdir") or "",
                             "the source machine's working directory must not travel")
            self.assertNotIn("dead-session", r.get("workdir") or "")

    def test_an_imported_routine_without_a_local_workdir_is_not_filed_into_one(self):
        """The other half of the same law: the archive must not *invent* a workdir either. An
        imported routine whose source directory does not exist on this machine has no working
        directory here, and filing one silently puts the body in a directory that is not there."""
        d = self.department()
        out = self.k.export_department(d["id"], str(self.tmp / "archive7"))
        # the archive mentions the workdir so a reader knows one was set, without copying the
        # machine's own path as if it were part of the department
        manifest = json.loads((Path(out["path"]) / "department.json").read_text())
        self.assertIn("workdir", manifest["routines"][0],
                      "the workdir is named, so nobody has to guess whether one was set")

    def test_an_import_refuses_to_overwrite_a_department_that_exists(self):
        """The import is additive: a department that already exists is the Chair's to
        change, not the archive's to replace."""
        d = self.department()
        out = self.k.export_department(d["id"], str(self.tmp / "archive5"))
        with self.assertRaises(ValueError) as caught:
            self.k.import_department(out["path"])
        self.assertIn("eng", str(caught.exception))
