"""Real Fleet HTTP/UDS contracts with explicitly synthetic devices and a simulated scene.

This development harness tests framing, authentication and journal transitions.
The synthetic owners move a scripted scene that is written to a simulation state
file; the platform's simulation observer (tools/sim/transfer_observer.py, D-927
decision 3) turns it into facts as a separate process. No physics, sensor
calibration, native driver or field-acceptance evidence.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET

import httpx

from tools.integration.fleet_harness import enable, _free_port

WORKCELL, INSTANCE, ROBOT = "w-1", "arm-1", "unused"
JOINTS = ["joint1", "joint2", "joint3", "joint4", "joint5", "gripper_joint_1"]
ASSETS = "learning/envs/isaac/assets/open_manipulator_description"
# shortcut: arm bases face away from the shared zone at the origin; the S4 installation profile will own them.
BASES = {INSTANCE: {"position_m": [.45, 0, 0], "orientation_xyzw": [0, 0, 0, 1]},
         "arm-2": {"position_m": [-.45, 0, 0], "orientation_xyzw": [0, 0, 1, 0]}}


class SyntheticOwner:
    MAX_FRAME_BYTES = 64 * 1024

    def __init__(self, capability, Receipt, Grant, scene, task_store):
        self.capability, self.Receipt, self.Grant = capability, Receipt, Grant
        self.scene, self.tasks = scene, task_store
        self.actions, self.operations = {}, []
        self.events = 10

    def dispatch(self, request, *, peer_uid):
        if peer_uid != os.getuid():
            raise PermissionError("unapproved simulation peer")
        operation = request["operation"]
        self.operations.append(operation)
        cap = self.capability
        if operation == "GetTransferCapability":
            return dict(version=2, status=200, capability=cap.model_dump(mode="json"))
        if operation == "GetOwnerIdentity":
            return dict(version=2, status=200, identity=dict(workcell_id=cap.workcell_id,
                instance_id=cap.instance_id, journal_id=cap.journal_id, simulation=True))
        if operation == "SubmitAction":
            grant = self.Grant.model_validate(request["grant"])
            fence = self.tasks.dispatch_control()
            if ((grant.workcell_id, grant.instance_id, grant.owner_journal_id, grant.owner_session_id)
                    != (cap.workcell_id, cap.instance_id, cap.journal_id, cap.owner_session_id)
                    or not fence["dispatch_enabled"] or grant.authority_epoch != fence["authority_epoch"]
                    or grant.dispatch_generation != fence["generation"]):
                raise PermissionError("synthetic owner scope or fence mismatch")
            if grant.action_id in self.actions:
                raise AssertionError("unexpected repeated SubmitAction")
            self.actions[grant.action_id] = grant
            state = "ACCEPTED"
        else:
            grant = self.actions[request["action_id"]]
            state = "CANCEL_REQUESTED" if operation == "CancelAction" else "SUCCEEDED"
            if state == "SUCCEEDED":
                self.scene["position_m"] = [.05, 0, .2]
        self.events += 1
        at = datetime.now(timezone.utc)
        receipt = self.Receipt(mission_id=grant.mission_id, step_id=grant.step_id, action_id=grant.action_id,
            attempt_id=grant.attempt_id, workcell_id=grant.workcell_id, instance_id=grant.instance_id,
            request_digest=grant.request_digest, authority_epoch=grant.authority_epoch,
            dispatch_generation=grant.dispatch_generation, journal_id=cap.journal_id,
            state=state, journal_event_id=self.events, observed_at=at, reason=None,
            phase_summaries=tuple(dict(phase_id=phase, ordinal=i, state="SUCCEEDED", journal_event_id=i + 1,
                observed_at=at) for i, phase in enumerate(("approach", "grasp", "transfer", "release")))
                if state == "SUCCEEDED" else ())
        return dict(version=2, status=200, receipt=receipt.model_dump(mode="json"))


class SyntheticStops:
    def stop(self, **kwargs):
        return {"state": "LOCAL_LATCHED"}

    def rearm(self, **kwargs):
        return {"state": "OPEN"}


class ArmFleet:
    def __init__(self, *, root, workdir, request_key_path=None, request_keys=("neutral-arm-http:0",), one_attempt=True):
        self.root, self.workdir = Path(root), Path(workdir)
        self.request_key_path = Path(request_key_path) if request_key_path else None
        self.request_keys, self.one_attempt = tuple(request_keys), one_attempt
        self.tokens = {name: secrets.token_urlsafe(40) for name in ("operator", "viewer", "approver")}
        self.observer_token = secrets.token_urlsafe(40)
        self.url = f"http://127.0.0.1:{_free_port()}"
        self.scene = {"position_m": [0, 0, .2]}
        self.stopped, self.errors = threading.Event(), []
        self.observed = set()
        self.owners, self.servers, self.threads = [], [], []
        self.transfers = {}
        self.server = self.observer = None

    def start(self):
        db = self.workdir / "fleet.sqlite3"
        options = self.install(db)
        import uvicorn
        from fleet.server.app import create_app
        from fleet.server.console import FleetConsole
        from fleet.server.mission_service import MissionService
        from fleet.server.mission_store import MissionStore
        from fleet.server.proposal_store import ProposalStore
        from fleet.server.task_service import FleetTaskService
        users = {hashlib.sha256(token.encode()).hexdigest(): {"principal_id": name,
            "role": "operator" if name == "approver" else "service"} for name, token in self.tokens.items()}
        self.app = create_app(FleetConsole([], []), task_service=FleetTaskService(self.tasks, robot_ids=()),
            mission_service=MissionService(MissionStore(db)), proposal_store=ProposalStore(db), site_users=users,
            deployment_profile="simulation", **options)
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=int(self.url.rsplit(":", 1)[1]),
                                                    log_level="error", lifespan="on"))
        self._thread(self.server.run)
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise TimeoutError("Fleet HTTP did not start")
            time.sleep(.01)
        with self.client("approver") as client:
            generation = client.get("/api/fleet/dispatch-control").json()["generation"]
            client.post("/api/fleet/dispatch/rearm", json={"expected_generation": generation}).raise_for_status()
        self._workers()

    def attach(self, url, tokens):
        """Approve and observe for a Fleet started elsewhere with install()'s options (one-Fleet full flow)."""
        self.url, self.tokens = url, tokens
        self._workers()

    def _workers(self):
        script = self.root / "tools/sim/transfer_observer.py"
        if not script.is_file():
            raise FileNotFoundError("platform lacks tools/sim/transfer_observer.py (D-927 decision 3)")
        state, bases = self.workdir / "sim-state.json", self.workdir / "sim-bases.json"
        bases.write_text(json.dumps(BASES), encoding="utf-8")
        self._write_state(state, 0)
        self._thread(lambda: self._publish_state(state))
        self.observer_log = (self.workdir / "observer.log").open("w", encoding="utf-8")
        self.observer = subprocess.Popen([sys.executable, str(script),
            "--origin", self.url, "--assets", str(self.root / ASSETS), "--bases", str(bases), "--state", str(state),
            "--poll-s", "0.05", "run"], env={**os.environ, "ROSY_TRANSFER_OBSERVER_TOKEN": self.observer_token},
            stdout=self.observer_log, stderr=subprocess.STDOUT)
        self._thread(self._approve)

    def _write_state(self, path, sequence):
        now = time.time_ns()
        arm = {"sequence": sequence, "observed_at_ns": now, "joint_names": JOINTS, "positions_rad": [0.0] * len(JOINTS),
               "velocities_rad_s": [0.0] * len(JOINTS), "gripper_state": "OPEN", "held_state": "NONE",
               "held_object_ref": None}
        state = {"schema": "rosy.sim-transfer-state/1", "owners": {i: arm for i in BASES}, "object": {
            "position_m": list(self.scene["position_m"]), "orientation_xyzw": [0, 0, 0, 1], "observed_at_ns": now}}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        os.replace(temporary, path)

    def _publish_state(self, path):
        sequence = 0
        while not self.stopped.wait(.05):
            sequence += 1
            self._write_state(path, sequence)

    def install(self, db):
        """Start the synthetic owners and return the create_app options that install ARM into a Fleet on db."""
        enable(self.root)
        if "rosy_cell" in sys.modules:
            raise RuntimeError("neutral harness must start without the palletizing package")
        sys.modules["rosy_cell"] = None
        sys.path.insert(0, str(self.root / "middleware/devices/omx/adapter"))
        from core_common.protocol.schemas import ArmTransferCapability, DeviceActionReceipt, FleetArmTransferGrant
        from fleet.server.task_store import FleetTaskStore
        from fleet.server.physical_transfer_evidence import PhysicalTransferPolicy, TransferParticipant
        from fleet.server.physical_transfer_observer import PhysicalTransferObserver
        from fleet.server.physical_transfer_store import TransferInstallation
        from omx_adapter.action_api import ActionApi, UnixActionServer
        from rosy.world.state.projections.omx_geometry import load_omx_geometry
        from rosy.world.state.projections.physical_transfer import Bounds3, PhysicalPose

        self.workdir.mkdir(parents=True)
        assets = self.root / "learning/envs/isaac/assets/open_manipulator_description"
        urdf = (assets / "urdf/omx_f/omx_f.urdf").read_bytes()
        meshes = {n.attrib["filename"]: (assets / n.attrib["filename"].removeprefix(
            "package://open_manipulator_description/")).read_bytes() for n in ET.fromstring(urdf).findall(".//mesh")}
        geometry = load_omx_geometry(urdf, meshes)
        self.links = sorted(geometry.required_links)
        participants = tuple(TransferParticipant(workcell_id=WORKCELL, instance_id=ident, arm_resource_id=WORKCELL + "." + ident,
            geometry=geometry, calibration_revision="cal-1", transform_revision="tf-1") for ident in (INSTANCE, "arm-2"))
        policy = PhysicalTransferPolicy(participants=participants, primary=(WORKCELL, INSTANCE),
            zones=(Bounds3((-.1, -.1, 0), (.1, .1, .3)),), zone_resource_ids=("w-1.shared",),
            object_resource_id="object-1", zone_margin_m=.01, producer_id="observer-1", evaluator_revision="eval-1",
            frame_id="world", catalog_digest="c" * 64, bindings_digest="d" * 64, object_ref="object-1",
            object_origin_id="model-origin", source_pose=PhysicalPose((0, 0, .2), (0, 0, 0, 1)),
            target_pose=PhysicalPose((.05, 0, .2), (0, 0, 0, 1)), position_tolerance_m=.002,
            orientation_tolerance_rad=.02, max_linear_speed_m_s=.01, max_angular_speed_rad_s=.02,
            max_age_ns=500_000_000, min_separation_ns=100_000_000)
        capabilities = tuple(ArmTransferCapability(action_kind="ARM_TRANSFER", control_mode="fleet_managed",
            workcell_id=p.workcell_id, instance_id=p.instance_id, journal_id="journal-" + p.instance_id,
            owner_session_id="session-1", catalog_digest=policy.catalog_digest, bindings_digest=policy.bindings_digest,
            resource_policy_digest=policy.digest, capability_revision="cap-1", config_revision="config-1") for p in participants)
        installed = TransferInstallation(policy=policy, source="source", target="target", capabilities=capabilities)
        sockets = self.workdir / "sockets"
        self.tasks = FleetTaskStore(db)
        for cap in capabilities:
            owner = SyntheticOwner(cap, DeviceActionReceipt, FleetArmTransferGrant, self.scene, self.tasks)
            path = sockets / cap.instance_id / "control.sock"
            path.parent.mkdir(parents=True)
            framed = ActionApi(None)
            framed.dispatch = owner.dispatch
            server = UnixActionServer(framed, path)
            self.owners.append(owner)
            self.servers.append(server)
            self._thread(server.serve_forever)
            deadline = time.monotonic() + 5
            while not path.exists():
                if time.monotonic() > deadline:
                    raise TimeoutError("synthetic owner socket did not start")
                time.sleep(.01)
        return dict(omx_socket_root=sockets, omx_stop_transport=SyntheticStops(), arm_transfer_installation=installed,
            arm_transfer_observer=PhysicalTransferObserver(producer_id=policy.producer_id, token=self.observer_token,
                valid_until=datetime.now(timezone.utc) + timedelta(minutes=10)))

    def _thread(self, function):
        thread = threading.Thread(target=function, daemon=True)
        self.threads.append(thread)
        thread.start()

    def client(self, who="operator"):
        return httpx.Client(base_url=self.url, headers={"Authorization": "Bearer " + self.tokens[who]}, timeout=5)

    def _approve(self):
        """Test-operator approval (D-927 decision 4); observation phases are only read, never answered here."""
        pending = [] if self.request_key_path else list(self.request_keys)
        loaded = False
        try:
            with self.client() as proposer, self.client("approver") as approver, httpx.Client(
                    base_url=self.url, headers={"X-Physical-Observation-Token": self.observer_token}, timeout=5) as observer:
                while not self.stopped.wait(.05):
                    if self.request_key_path and not loaded:
                        if not self.request_key_path.exists():
                            continue
                        request_key = self.request_key_path.read_text(encoding="utf-8").strip()
                        if not request_key.startswith("harbor:") or not request_key.endswith(":0"):
                            raise ValueError("invalid web request key")
                        pending.append(request_key)
                        loaded = True
                    for request_key in tuple(pending):
                        response = proposer.get("/api/fleet/arm-transfers/jobs", params={"request_key": request_key})
                        if response.status_code == 200:
                            job = response.json()["job"]
                            # shortcut: each move's object starts at the one installed source; per-move bindings come with S4.
                            self.scene["position_m"] = [0, 0, .2]
                            generation = approver.get("/api/fleet/dispatch-control").json()["generation"]
                            approver.post("/api/fleet/arm-transfers/jobs/" + job["mission_id"] + "/admit", json={
                                "expected_proposal_digest": job["proposal_digest"], "expected_generation": generation}).raise_for_status()
                            pending.remove(request_key)
                        elif response.status_code != 404:
                            response.raise_for_status()
                    response = observer.get("/api/fleet/arm-transfers/observation-context")
                    response.raise_for_status()
                    context = response.json()["context"]
                    if context is not None:
                        self.observed.add(context["phase"])
                    if self.observer.poll() is not None:
                        raise RuntimeError("simulation observer exited")
        except Exception as error:
            if not self.stopped.is_set():
                self.errors.append(type(error).__name__)

    def stop(self):
        self.stopped.set()
        if self.server is not None:
            self.server.should_exit = True
        for server in self.servers:
            server.stop()
        if self.observer is not None:
            self.observer.terminate()
            self.observer.wait(timeout=5)
            self.observer_log.close()
        for thread in self.threads:
            thread.join(timeout=5)
        sys.modules.pop("rosy_cell", None)
        if self.errors or any(thread.is_alive() for thread in self.threads):
            raise RuntimeError("simulation harness worker failed or did not stop")
        submits = sum(o.operations.count("SubmitAction") for o in self.owners)
        lookups = sum(o.operations.count("GetAction") for o in self.owners)
        print("Synthetic UDS submits:", submits, "lookups:", lookups, "physical proofs:", sorted(self.observed), flush=True)
        if self.one_attempt and (submits != 1 or lookups < 1 or self.observed != {"PRE_DISPATCH", "POST_ACTION"}):
            raise AssertionError("simulation did not complete exactly one admitted physical attempt")


class FullFlowFleet:
    """D-927 decision 2: one simulation Fleet serves TRIP (synthetic Pinky) and ARM (synthetic owners)."""

    def __init__(self, *, root, workdir, request_keys):
        self.root, self.workdir = Path(root), Path(workdir) / "fleet"
        self.arm = ArmFleet(root=root, workdir=Path(workdir) / "arm", request_keys=request_keys, one_attempt=False)
        self.fleet = None

    def start(self):
        from tools.integration.fleet_harness import RealFleet
        self.workdir.mkdir(parents=True)
        self.fleet = RealFleet(root=self.root, workdir=self.workdir, transport_only=True,
                               extra_app=self.arm.install(self.workdir / "fleet.sqlite3"))
        self.fleet.start()
        self.url, self.tokens = self.fleet.url, self.fleet.tokens
        self.arm.attach(self.url, self.tokens)

    def stop(self):
        try:
            self.arm.stop()
        finally:
            if self.fleet is not None:
                self.fleet.stop()
