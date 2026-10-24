"""`holon mcp`: the organization as MCP tools, authenticated by the environment the harness passes on."""

import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from holon import mcp
from holon.cli import REGISTRY, _positional_flags
from tests.helpers import KernelCase

REPO = Path(__file__).resolve().parents[1]


class Protocol(KernelCase):
    def test_tools_come_from_the_registry_and_exclude_chair_commands(self):
        names = {t["name"] for t in mcp.tools()}
        self.assertTrue({"holon_whoami", "holon_submit", "holon_memory_search", "holon_run_heartbeat"} <= names)
        actors = {mcp._tool_name(c.path): c.actor for c in REGISTRY}
        for n in names - {"holon_whoami", "holon_submit"}:
            self.assertIn(actors[n], ("any", "employee"), n)
        self.assertNotIn("holon_plan_approve", names)
        search = next(t for t in mcp.tools() if t["name"] == "holon_memory_search")
        self.assertEqual(search["inputSchema"]["required"], ["query"])
        self.assertNotIn("as_", json.dumps(mcp.tools()), "identity comes from the token, never an argument")

    def test_the_agent_fence_carries_the_knowledge_join_commands(self):
        names = {t["name"] for t in mcp.tools()}
        self.assertTrue({"holon_memory_episodes", "holon_playbook_list", "holon_playbook_show",
                         "holon_events", "holon_skill_path"} <= names,
                        "the kernel's synthesis commands belong in the agent fence (KB-2, KB-3)")

    def test_the_allowlist_is_derived_from_the_actor_class_not_hand_listed(self):
        """F1: the fence was a hand-maintained tuple and drifted from the law the registry already
        states. `task list` was missing, so a mounted employee could not discover the work it was
        meant to claim. The list is now derived, and this proves the derivation round-trips: every
        exposed tool's actor class is one an employee may act under, and no chair or kernel command
        is reachable through `call` even though it is in the registry.

        The blocklist is not absolute: `AGENT_ALWAYS` restores the two tools a body cannot work
        without (`whoami` for its identity, `submit` to hand its result in), which are typed tools
        published separately rather than the generic flag-forwarding path. Every other blocklist
        entry stays out -- that difference is the whole rule, and it lives in one place."""
        from holon.mcp import agent_commands, AGENT_ALWAYS, AGENT_ACTORS
        exposed = set(agent_commands())
        for c in REGISTRY:
            if c.path in exposed:
                self.assertIn(c.actor, AGENT_ACTORS,
                              f"holon {' '.join(c.path)} (actor {c.actor}) is in the agent fence")
        for c in REGISTRY:
            if c.actor not in ("chair", "kernel") or " ".join(c.path) in mcp.AGENT_BLOCKLIST:
                continue
            self.assertNotIn(c.path, exposed,
                             f"holon {' '.join(c.path)} (actor {c.actor}) must not be exposed")
        always = set(AGENT_ALWAYS)
        # measured, not inspected: `whoami` and `submit` are the two a body cannot work without,
        # so their exposure is a behaviour, not a constant. Every other blocklisted command stays
        # out. This catches an emptied AGENT_ALWAYS as readily as an overfull one, because it
        # asserts the reachable set, not the list that produced it.
        self.assertIn(("whoami",), exposed, "a body cannot reach its own identity")
        self.assertIn(("submit",), exposed, "a body cannot hand its result in")
        restored = {" ".join(p) for p in exposed} & set(mcp.AGENT_BLOCKLIST)
        self.assertEqual(restored, {"whoami", "submit"},
                         f"exactly these blocklisted commands are agent-reachable: {sorted(restored)}. "
                         "whoami and submit must be, and nothing else on the blocklist may be")


    def test_chair_actions_are_derived_from_the_actor_class_not_hand_listed(self):
        """The bot controls surface (2026-10-23): a chat channel offers buttons, and the set it may
        offer must come from the law the registry already states. A hand-listed twin would drift —
        the same defect as the MCP fence — and a drifted Chair surface is worse than an inert one:
        a button the Chair cannot press trains them to ignore the channel. So `chair_actions()` is
        derived, and this proves both halves: every chair-actor command is offered, and nothing an
        agent may reach is ever presented as a Chair action."""
        from holon.cli import chair_actions
        offered = set(chair_actions())
        # HALF 1: everything only the Chair may do is offered, or the channel cannot do its job.
        for c in REGISTRY:
            if c.actor == "chair":
                self.assertIn(c.path, offered,
                              f"holon {' '.join(c.path)} is chair-only but no button offers it")
        # HALF 2: nothing the kernel runs by itself (kernel, intake) is ever a Chair action.
        for c in REGISTRY:
            if c.actor in ("kernel", "intake"):
                self.assertNotIn(c.path, offered,
                                 f"holon {' '.join(c.path)} is {c.actor}, not a Chair action")
        # HALF 3, the one the first draft got wrong: `status` is `any` and `msg reply` is
        # `employee`, so a strict chair-only list would ship a channel with no landing screen
        # and no way to answer an employee. Those two actors are the shared surface and must be
        # offered too — which is safe precisely because the actor gate is enforced at the CLI
        # seam, so the bot pressing `status` and a body pressing `status` run the same command
        # under different principals.
        for path in (("status",), ("plan", "queue"), ("msg", "reply"), ("msg", "thread"),
                     ("task", "list"), ("org",), ("doctor",)):
            self.assertIn(path, offered,
                          f"holon {' '.join(path)} is the shared surface the channel is built on")
        self.assertIn(("plan", "approve"), offered, "the approval surface is the point of the channel")
        self.assertGreater(len(offered), 40, f"the Chair surface is large: {len(offered)}")

    def test_a_command_the_registry_declares_any_reaches_the_fence(self):
        """The drift guard's positive half: a command registered `any` is agent-visible the day it
        is registered, with no hand-listed step to forget. `task list` is the one the omission hid."""
        names = {t["name"] for t in mcp.tools()}
        for c in REGISTRY:
            if c.actor in ("any", "employee") and " ".join(c.path) not in mcp.AGENT_BLOCKLIST:
                self.assertIn(mcp._tool_name(c.path), names,
                              f"holon {' '.join(c.path)} is {c.actor} but has no tool")
        self.assertIn("holon_task_list", names, "the work a mounted employee must claim is discoverable")

    def test_no_exposed_tool_takes_an_argument_it_cannot_obtain(self):
        """F2: two of the exposed tools were unusable as shipped — `claim` and `run show` each take
        an id no exposed tool could produce, so the capability was inert rather than absent. Every
        required positional must now be obtainable from a lister the fence also exposes, or be
        supplied by identity (`whoami`) or be free text. This is the guard that makes F2 permanent."""
        exposed = {mcp._tool_name(c.path) for c in REGISTRY if c.path in mcp.agent_commands()}
        # the id-bearing field each exposed lister puts in its rows, so a required positional can be
        # checked against what an agent can actually obtain
        listers = {"holon_task_list": {"id", "root_id", "stage"},
                   "holon_emp_list": {"id", "employee"},
                   "holon_dept_list": {"slug", "id"},
                   "holon_plan_queue": {"id", "plan"},
                   "holon_kb_list": {"slug"},
                   "holon_schedule_list": {"id"},
                   "holon_routine_list": {"id"},
                   "holon_playbook_list": {"name"},
                   "holon_msg_inbox": {"id"},
                   "holon_events": {"id"}}
        self.assertTrue(listers.keys() <= exposed, "every id lister the fence promises is exposed")
        # a required positional is obtainable when a lister emits a row field that names it: the
        # argparse dest (`task_id`, `employee`, `slug`, `ref`) against the row key (`id`, `employee`,
        # `slug`). `ref` and `objective_id` come from `plan queue` / `objective` rows.
        obtainable = {"id", "task_id", "employee", "slug", "dept", "ref", "objective_id",
                      "stage", "root_id"}
        for c in REGISTRY:
            if c.path not in mcp.agent_commands():
                continue
            for a, kw in c.args:
                if not a or a[0].startswith("-") or kw.get("nargs") in ("?", "*"):
                    continue
                kind = a[0]
                # identity supplies the run id; free text (a question, a query, a path) is the caller's own
                if kind in ("query", "question", "title", "path", "doc", "to", "body", "name", "verb",
                            "tokens", "run_id"):
                    continue
                # an id is obtainable when a lister emits it. `memory search` returns item rows
                # carrying `id`, so `item_id` counts even though no command is called `memory list`.
                # `msg inbox` emits message rows carrying `id`, which is how `msg reply` and
                # `msg thread` obtain the id they name.
                aliases = {"item_id": "id", "message": "id"}
                self.assertIn(aliases.get(kind, kind), obtainable,
                              f"holon {' '.join(c.path)} requires {kind}, which no exposed lister emits")


    def test_handshake_and_unknown_methods(self):
        init = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2024-11-05"}})
        self.assertEqual(init["result"]["protocolVersion"], "2024-11-05")
        self.assertIn("tools", init["result"]["capabilities"])
        self.assertIsNone(mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertEqual(mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "nope"})["error"]["code"], -32601)


class GatedCommands(KernelCase):
    """The `--force` gate exists because a one-tap surface could spend it by accident. The Chair's
    control surface is being built on these tools, so the question is whether an employee session
    can reach a destructive command at all.

    Measured: `tools()` publishes only `agent_commands()`, which `call()` enforces again by name.
    None of the registry's four gated commands (gc, keys gc, restore, isolation apply) appear, and
    each is refused as an unknown tool. Derived from the registry, not restated: gating a command
    that is also in `AGENT_ACTORS` would publish it, so the guard is the intersection."""

    def test_no_gated_command_reaches_an_agent_session(self):
        from holon import mcp
        from holon.cli import REGISTRY
        published = {t["name"] for t in mcp.tools()}
        gated = [" ".join(c.path) for c in REGISTRY
                 if "--force" in [a[0] for a, _ in (c.args or [])]]
        self.assertTrue(gated, "the registry gates at least one command, or this guard is moot")
        for path in gated:
            tool = "holon_" + "_".join(p.replace("-", "_") for p in path.split())
            self.assertNotIn(tool, published,
                             f"`holon {path}` is gated with --force and published to an employee "
                             f"session as {tool}: a one-tap surface could spend the force")
            ok, text = mcp.call(tool, {"force": True})
            self.assertTrue(ok, f"{tool} must be refused by name, not reachable: {text[:80]}")

    def test_the_gate_is_not_derivable_from_the_employee_actor_class(self):
        """The guard above only holds while the gated commands stay outside `AGENT_ACTORS`. If one
        is ever re-assigned to an actor class an employee holds, the tools list would publish it and
        the previous test would catch it -- this makes the invariant explicit rather than incidental."""
        from holon import mcp
        from holon.cli import REGISTRY
        gated_paths = {" ".join(c.path) for c in REGISTRY
                       if "--force" in [a[0] for a, _ in (c.args or [])]}
        leaked = gated_paths & {" ".join(p) for p in mcp.agent_commands()}
        self.assertEqual(leaked, set(),
                         f"a gated command is in an agent actor class: {sorted(leaked)}")


class OverStdio(KernelCase):
    def session(self, env, *calls):
        msgs = [{"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": mcp.PROTOCOL}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"}]
        msgs += [{"jsonrpc": "2.0", "id": i + 1, "method": "tools/call", "params": {"name": n, "arguments": a}}
                 for i, (n, a) in enumerate(calls)]
        full = {**os.environ, "PYTHONPATH": str(REPO), "HOLON_HOME": str(self.home.root), **env}
        p = subprocess.run([sys.executable, "-m", "holon", "mcp"], input="\n".join(json.dumps(m) for m in msgs) + "\n",
                           capture_output=True, text=True, env=full, timeout=120)
        replies = [json.loads(line) for line in p.stdout.splitlines() if line.strip()]
        self.assertEqual(len(replies), len(calls) + 1, p.stderr)
        return [(r["result"]["isError"], r["result"]["content"][0]["text"]) for r in replies[1:]]

    def test_an_observe_stage_agent_works_and_submits_through_tools(self):
        self.k.auth.init_chair_key()
        t = self.add(dept="eng")
        self.k.memory.add_item("dept:eng", "fact", "Greeting files live at the repository root", "test")
        c = self.k.claim("plan", self.hod)
        other = self.k.claim("plan", self.hod, task_id=self.add("other", dept="eng")["id"])
        env = {"HOLON_RUN_TOKEN": c.token, "HOLON_CHAIR_KEY_FILE": str(self.tmp / "absent.key")}
        (who_err, who), (s_err, found), (o_err, _), (sub_err, sub) = self.session(
            env, ("holon_whoami", {}), ("holon_memory_search", {"query": "greeting files"}),
            ("holon_submit", {"run_id": other.run["id"], "result": self.plan_env()}),
            ("holon_submit", {"result": self.plan_env()}))
        self.assertFalse(who_err)
        self.assertEqual(json.loads(who)["stage"]["stage"], "plan")
        self.assertIn("You are Bo", json.loads(who)["identity"])
        self.assertIn("repository root", found)
        self.assertTrue(o_err, "a token covers only its own run")
        self.assertFalse(sub_err, sub)
        self.assertEqual(self.pipeline_of(t)[-1], ("approve", "ready"))

    def test_without_a_token_or_key_tools_are_read_only(self):
        self.k.auth.init_chair_key()
        self.add(dept="eng")
        (err, text), = self.session({"HOLON_CHAIR_KEY_FILE": str(self.tmp / "absent.key")},
                                    ("holon_msg_send", {"to": "dept:eng", "body": "hello"}))
        self.assertTrue(err)
        self.assertIn("is for the employee actor class", text)


    def test_an_employee_can_read_an_exposed_command_whose_write_is_the_chairs_alone(self):
        """F-A's safety proof: the derivation exposes `charter authority` (actor `any`) although it
        `mutates`, because the write path inside the command checks the principal. An employee must
        read the authority table and be refused the write, through the same MCP path a harness uses."""
        self.k.auth.init_chair_key()
        self.add(dept="eng")
        c = self.k.claim("plan", self.hod)
        env = {"HOLON_RUN_TOKEN": c.token, "HOLON_CHAIR_KEY_FILE": str(self.tmp / "absent.key")}
        (is_err, read), = self.session(env, ("holon_charter_authority", {}))
        self.assertFalse(is_err, read)
        self.assertIn("emp:", read)
        (is_err, refused), = self.session(env, ("holon_charter_authority",
                                                {"set_for": self.hod, "task_class": "*", "max_grade": "G9"}))
        self.assertTrue(is_err, "an employee must not widen its own authority through the fence")
        self.assertIn("only the Chair", refused)


class SubmitSchema(KernelCase):
    """G-b (measured 2026-10-21): what `holon_submit` publishes about the envelope's shape.

    The tool's schema is the only thing a client can coerce *to*. Publishing a free-form object
    means the client guesses — and the guess it made (an untyped array becomes `{"item": …}`) lost a
    real body's work. The kernel already knows the shape; the tool must publish it.
    """

    def _submit_tool(self):
        return next(t for t in mcp.tools() if t["name"] == "holon_submit")

    def test_the_published_schema_names_the_envelopes_fields(self):
        schema = self._submit_tool()["inputSchema"]["properties"]["result"]
        props = schema.get("properties") or {}
        self.assertTrue(props, "the client is told the field types, not left to guess")
        for field in ("kind", "steps", "writes", "external_effects", "findings"):
            self.assertIn(field, props, f"{field} is a field the client must type correctly")

    def test_a_list_field_is_published_as_an_array(self):
        """The defect this repairs: a list field published with no type arrives wrapped.
        Every list field the contract names is published as an array."""
        schema = self._submit_tool()["inputSchema"]["properties"]["result"]
        variants = schema.get("oneOf") or [schema]
        for name in ("steps", "writes", "external_effects", "learnings"):
            typed = [s for v in variants for k, s in (v.get("properties") or {}).items() if k == name]
            if not typed:
                continue
            # `steps` is text-or-list in the contract, so it is a oneOf — every other list field is
            # a plain array. What matters in both: a list is ADMITTED, and named as the type to
            # coerce to, which is what the client had no way to learn before.
            spec = typed[0]
            admits_list = spec.get("type") == "array" or "array" in json.dumps(spec)
            self.assertTrue(admits_list, f"{name} admits a list, published as {json.dumps(spec)[:80]}")

    def test_the_stage_less_server_publishes_types_too(self):
        """A server started with no run token (`_run_stage()` is None) is the one a hand-started
        probe hits, and it is where the free-form object came back. The contract's types must be
        published there too — the shape repair is not conditional on having a token."""
        schema = mcp._result_schema_for_stage(None)
        self.assertTrue(schema.get("properties") or schema.get("oneOf"),
                        "a server with no token still publishes types")

    def test_a_real_submit_call_is_accepted_against_the_published_schema(self):
        """The schema is not decoration: an envelope it describes is the envelope the kernel accepts."""
        self.k.auth.init_chair_key()
        self.add(dept="eng")
        c = self.k.claim("plan", self.hod)
        env = self.plan_env(external_effects=[])
        for field in ("steps", "writes", "external_effects"):
            self.assertIsInstance(env.get(field), list, f"{field} is a list in the canonical envelope")
        got = self.k.submit(c.run["id"], env)
        self.assertEqual(got["approval"]["stage"], "approve")

    def test_the_field_a_body_declares_empty_is_published(self):
        schema = self._submit_tool()["inputSchema"]["properties"]["result"]
        self.assertIn("external_effects", schema["properties"],
                      "a field a body may declare empty is published, so its emptiness is intended")


class ArgumentNames(OverStdio):
    """G-c (measured 2026-10-21): the names a runtime sends, and the names the CLI accepts.

    The operant body's own lesson: `holon_dept_roles` sent `{"slug": "conformance"}` and came back
    "unrecognized arguments: --slug". Its native tool layer built `--slug` from the MCP schema's
    property name — which is the name the kernel publishes. A tool that names a property `slug` and
    then rejects `--slug` publishes a contract its own client cannot follow.

    The kernel already marks these `"positional": true` (H-14). A runtime that reads the marker
    sends the value bare; one that does not sends `--name`. Both must work, because the kernel
    publishes the name either way and cannot dictate which runtime reads the marker."""

    def _session_with_token(self):
        self.k.auth.init_chair_key()
        self.add(dept="eng")
        c = self.k.claim("plan", self.hod)
        return {"HOLON_RUN_TOKEN": c.token, "HOLON_CHAIR_KEY_FILE": str(self.tmp / "absent.key")}

    def test_a_named_property_sent_as_a_flag_is_accepted(self):
        """The CLI, reached the way a native tool layer reaches it, must accept the name the MCP
        schema publishes. This is the operant body's exact call: `--slug`, answered with a usage
        dump."""
        env = self._session_with_token()
        (err, text), = self.session(env, ("holon_dept_roles", {"slug": "eng"}))
        self.assertFalse(err, text)
        # the same name, sent the way a runtime that ignores the positional marker builds it:
        argv = _positional_flags(["--json", "dept", "roles", "--slug", "eng"])
        self.assertEqual(argv, ["--json", "dept", "roles", "eng"], "the published name is accepted")

    def test_a_property_that_is_not_a_positional_keeps_failing(self):
        """The normalizer rewrites only what the command names as a positional. An unknown flag, and
        a name that belongs to another command, must still be rejected: a tolerance that silently
        swallowed every `--flag` would hide real mistakes."""
        self.assertEqual(_positional_flags(["dept", "roles", "--bogus", "eng"]),
                         ["dept", "roles", "--bogus", "eng"], "an unknown flag is left to fail")
        self.assertEqual(_positional_flags(["memory", "search", "--slug", "eng"]),
                         ["memory", "search", "--slug", "eng"],
                         "`slug` is not a positional of memory search")

    def test_the_submit_run_id_flag_is_normalized(self):
        """`run_id` is a positional of `submit`, so the flag form a runtime derives is accepted —
        this is the second reported rejection, on the tool whose failure loses a body's work."""
        rid = "run_01M5DX8EBA18TZVX5385Y2CACJ"
        self.assertEqual(_positional_flags(["submit", "--run_id", rid]), ["submit", rid])

    def test_a_positional_property_sent_bare_still_works(self):
        env = self._session_with_token()
        (err, text), = self.session(env, ("holon_playbook_list", {}))
        self.assertFalse(err, f"the bare form the marker asks for is accepted: {text}")

    def test_the_submit_run_id_sent_as_a_flag_is_accepted(self):
        """The same runtime shape, on the tool whose failure loses a body's work: `run_id` sent as
        `--run_id` was the second reported rejection."""
        self.k.auth.init_chair_key()
        t = self.add(dept="eng")
        c = self.k.claim("plan", self.hod)
        other = self.k.claim("plan", self.hod, task_id=self.add("other", dept="eng")["id"])
        env = {"HOLON_RUN_TOKEN": other.token, "HOLON_CHAIR_KEY_FILE": str(self.tmp / "absent.key")}
        (err, text), = self.session(env, ("holon_submit", {"run_id": other.run["id"],
                                                      "result": self.plan_env()}))
        self.assertFalse(err, f"run_id names the run: {text}")
        self.assertEqual(self.pipeline_of(t)[-1], ("plan", "claimed"),
                         "a token covers only its own run: the other run's id is not a submission")



class OneFence(unittest.TestCase):
    """Two gates over one registry must give one answer. They did not.

    Measured 2026-10-24: `mcp.agent_commands()` derived 54 commands from actor classes, and the
    broker gate admitted 57 -- the difference being exactly `help`, `submit` and `whoami`, the
    three `AGENT_BLOCKLIST` names the fence refuses on purpose. `BROKER_EXTRA` in `cli.py` is a
    hand-maintained escape hatch that hands them back through the other door, so a body mounted
    as MCP is refused `holon_submit` while a body routed through the broker is allowed the same
    verb. The 2026-10-21 ledger row fixed the MCP fence's hand-maintained list and left the
    broker's in place, which is the same defect one file over.

    The contract: one derivation, consumed by both gates. An agent's reach is `agent_commands()`,
    whichever transport it arrived on -- a fence with two answers is a fence nobody can reason
    about, and it is exactly the class that made `task list` unreachable for a month.
    """

    def test_the_broker_gate_admits_exactly_the_derived_fence(self):
        """The broker's own gate holds no second list. Calling the CLI the way the broker calls
        it (`main(..., broker={})`) is the measurement, not inspecting a constant: a second
        hand-maintained tuple would make a blocked command succeed through this gate alone.

        The allowed side uses `plan queue`, which parses with no positional arguments. That
        matters for what this guard can see: a `FORBIDDEN` return is the gate's verdict, but
        an argparse usage error raises `SystemExit`, which propagates and *errors* this test
        rather than passing it -- so an argv that could fail argparse would make the guard
        fail for a reason other than the gate. An argv that always parses keeps the assertion
        about the gate alone."""
        import holon.cli as cli
        # `help` is on the blocklist and in no restore list: through the broker it must be refused
        rc = cli.main(["help"], broker={"token": None, "key": None, "files": {}, "stdin": ""})
        self.assertEqual(cli.FORBIDDEN, rc,
                         "the broker admitted `help`, a blocklisted command: the gate carries a "
                         "list of its own again")
        # and a command an agent needs must pass the gate: `plan queue` takes no positional, so
        # nothing downstream can fail it, and a non-FORBIDDEN exit is the gate letting it through
        rc = cli.main(["plan", "queue"], broker={"token": None, "key": None, "files": {}, "stdin": ""})
        self.assertNotEqual(cli.FORBIDDEN, rc,
                            "the broker refused `plan queue`: the work an agent must claim is "
                            "unreachable through the transport agents actually use")
        self.assertIsInstance(rc, int, "`plan queue` never reached the gate: this guard cannot "
                                      "tell past-the-gate from refused-at-it with this argv, and a "
                                      "SystemExit escaping would error the test rather than lie")

    def test_both_gates_agree_for_every_registry_command(self):
        """The whole registry, both paths: nothing may cross one gate and be refused at the other.

        The measurement is the broker's real decision, not a second call to the derivation. The
        first version of this guard built its "broker" side from `mcp.agent_commands()` as well,
        so it compared the derivation with itself: it could not fail whatever the broker's own
        gate did, and it passed while a mutation re-admitted a blocklisted command through that
        gate. Every command's broker verdict now comes from calling `cli.main` the way the broker
        calls it, so the two answers come from two different places and can disagree.

        What "the gate let it through" means here, measured rather than assumed: a refusal carries
        one of two messages. `is not available to agents` is the fence's own; `is for the <role>
        actor class` is the actor-class check below it, which refuses regardless of the fence.
        Only the fence's message is compared -- a command refused for its actor class is not
        evidence about the fence, and comparing it would make this guard fail for a reason the
        fence has nothing to do with. Rows needing positionals to parse are recorded, not guessed:
        argparse exits the process on a usage error, and that exit is not the gate's verdict."""
        import holon.cli as cli
        derived = {" ".join(p) for p in mcp.agent_commands()}
        blocked = set(mcp.AGENT_BLOCKLIST)
        unreached, wrong = [], []
        for c in REGISTRY:
            name = " ".join(c.path)
            err = io.StringIO()
            try:
                with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                    rc = cli.main(list(c.path), broker={"token": None, "key": None,
                                                        "files": {}, "stdin": ""})
            except SystemExit:
                unreached.append(name)  # argparse refused the bare argv: the gate was never reached
                continue
            # the fence's own refusal, distinguished from the actor-class refusal below it
            by_fence = rc == cli.FORBIDDEN and "is not available to agents" in err.getvalue()
            via_broker = not by_fence
            via_mcp = name in derived
            if via_mcp != via_broker:
                wrong.append(f"{name} (mcp={via_mcp}, broker={via_broker}, actor={c.actor}, "
                             f"blocked={name in blocked})")
        self.assertEqual([], wrong,
                         "a command crosses the two gates differently: " + "; ".join(wrong))
        # The rows the bare argv could not reach are named, not silently counted as agreement: a
        # command that stops parsing here was never asked whether the fence admitted it, so it
        # would be dishonest to report it as agreeing. Every such row must be a command whose
        # parser required something -- otherwise the argv reached nothing when it should have.
        for n in unreached:
            cmd = next(c for c in REGISTRY if " ".join(c.path) == n)
            # the bare argv stopped parsing because argparse needed something: a required
            # positional or an `--option` marked `required`. Either way the command never
            # reached the fence, so it must be one of those, or the argv reached nothing when
            # it should have got to the gate and asked it.
            needed = [a for a in cmd.args
                      if a[1].get("required")
                      or (a[0] and not str(a[0][0]).startswith("-")
                          and a[1].get("default") is None
                          and a[1].get("nargs") not in ("?", "*"))]
            self.assertTrue(needed,
                             f"`{n}` never reached the gate with a bare argv yet declares no "
                             f"required argument: the fence was not tested for it")
