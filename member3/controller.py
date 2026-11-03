"""Ryu OpenFlow 1.3 controller with path monitoring and loss-driven rerouting.

Rerouting from the primary path (s1-s2-s4) to the alternate path (s1-s3-s4)
is triggered by any of:
  * LOSS_ALERT datagrams sent by the UDP receiver (sequence gaps),
  * active path monitoring: periodic OpenFlow port-stats polling that compares
    packets entering the primary path (s2 port 1) with packets leaving it
    (s4 port 2),
  * an OpenFlow port-down event.

Every reroute is appended as one JSON line to EVENT_LOG so run_experiment.py
can record the exact reroute time.
"""
import json
import os
import socket
import time

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (
    CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER, set_ev_cls,
)
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub

EVENT_LOG = os.environ.get("SDN_EVENT_LOG", "/tmp/sdn_controller_events.jsonl")


class SimpleController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    ALERT_HOST = "0.0.0.0"
    ALERT_PORT = 9999
    LOSS_THRESHOLD = 3          # loss alerts needed ...
    LOSS_WINDOW = 5             # ... within this many seconds
    REROUTE_COOLDOWN = 5
    # Active path monitoring (port statistics)
    MONITOR_INTERVAL = 1.0      # seconds between port-stats polls
    MONITOR_MIN_PACKETS = 30    # ignore windows with too little traffic
    MONITOR_LOSS_PCT = 15.0     # estimated path loss that counts as bad
    MONITOR_BAD_WINDOWS = 2     # consecutive bad windows before rerouting
    PRIMARY_IN = (2, 1)         # (dpid, port): traffic entering primary path
    PRIMARY_OUT = (4, 2)        # (dpid, port): traffic leaving primary path

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.datapaths = {}
        self.current_path = None
        self.loss_alerts = []
        self.last_reroute_time = 0.0
        self.primary_failed = False
        self.reroute_start_time = None
        self._primary_installed = False
        self.port_prev = {}
        self.port_delta = {}
        self.bad_windows = 0
        self.alert_thread = hub.spawn(self.loss_alert_listener)
        self.monitor_thread = hub.spawn(self.path_monitor)
        self.logger.info("SDN controller started; OpenFlow 1.3; UDP alerts on %s; events -> %s",
                         self.ALERT_PORT, EVENT_LOG)

    # ------------------------------------------------------------------ flows
    def add_flow(self, datapath, priority, match, actions):
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        instructions = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        datapath.send_msg(parser.OFPFlowMod(
            datapath=datapath, priority=priority, match=match,
            instructions=instructions,
        ))

    def delete_all_flows(self, datapath):
        # A non-strict OFPFC_DELETE ignores priority and removes every flow,
        # so the table-miss rule is re-added afterwards in _install_path.
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        datapath.send_msg(parser.OFPFlowMod(
            datapath=datapath, command=ofproto.OFPFC_DELETE,
            out_port=ofproto.OFPP_ANY, out_group=ofproto.OFPG_ANY,
            match=parser.OFPMatch(),
        ))
        datapath.send_msg(parser.OFPBarrierRequest(datapath))

    def add_table_miss(self, datapath):
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        self.add_flow(datapath, 0, parser.OFPMatch(), [
            parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)
        ])

    @staticmethod
    def primary_rules(dpid):
        return {
            1: [(1, 2), (2, 1)],
            2: [(1, 2), (2, 1)],
            4: [(2, 1), (1, 2)],
        }.get(dpid, [])

    @staticmethod
    def alternate_rules(dpid):
        return {
            1: [(1, 3), (3, 1)],
            3: [(1, 2), (2, 1)],
            4: [(3, 1), (1, 3)],
        }.get(dpid, [])

    def _install_path(self, path):
        rules_for_path = self.primary_rules if path == "primary" else self.alternate_rules
        label = "PRIMARY" if path == "primary" else "ALTERNATE"
        self.logger.info("Installing %s path", label)
        for dpid, datapath in list(self.datapaths.items()):
            self.delete_all_flows(datapath)
            self.add_table_miss(datapath)
            parser = datapath.ofproto_parser
            for in_port, out_port in rules_for_path(dpid):
                self.add_flow(datapath, 100, parser.OFPMatch(in_port=in_port),
                              [parser.OFPActionOutput(out_port)])
                self.logger.info("s%s: in_port=%s -> out_port=%s", dpid, in_port, out_port)
            datapath.send_msg(parser.OFPBarrierRequest(datapath))
        self.current_path = path
        self.primary_failed = path != "primary"
        if path == "alternate":
            self.reroute_start_time = time.time()
        self.logger.info("%s path active", label)

    def install_primary_path(self):
        self._install_path("primary")

    def install_alternate_path(self):
        self._install_path("alternate")

    def initialize_network(self):
        if len(self.datapaths) == 4 and not self._primary_installed:
            self.logger.info("All four switches connected; initializing primary route")
            self.install_primary_path()
            self._primary_installed = True
            self.port_prev.clear()
            self.port_delta.clear()
            self.bad_windows = 0

    # --------------------------------------------------------- switch events
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        self.logger.info("Switch features received: DPID=%s", datapath.id)
        self.add_table_miss(datapath)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def state_change_handler(self, ev):
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[datapath.id] = datapath
            self.logger.info("Switch connected: DPID=%s (%s/4)", datapath.id, len(self.datapaths))
            self.initialize_network()
        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(datapath.id, None)
            self.logger.warning("Switch disconnected: DPID=%s", datapath.id)
            self._primary_installed = False
            self.current_path = None

    @set_ev_cls(ofp_event.EventOFPErrorMsg, MAIN_DISPATCHER)
    def openflow_error_handler(self, ev):
        msg = ev.msg
        self.logger.error("OpenFlow error from DPID=%s: type=%s code=%s data=%s",
                          msg.datapath.id, msg.type, msg.code, msg.data.hex() if msg.data else "")

    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def port_status_handler(self, ev):
        msg = ev.msg
        ofproto = msg.datapath.ofproto
        if msg.reason not in (ofproto.OFPPR_DELETE, ofproto.OFPPR_MODIFY):
            return
        if msg.desc.state & ofproto.OFPPS_LINK_DOWN:
            self.logger.warning("Link down detected on s%s port %s", msg.datapath.id, msg.desc.port_no)
            if self.current_path == "primary" and not self.primary_failed:
                self.handle_failure("openflow_port_down")

    # ------------------------------------------------------ path monitoring
    def path_monitor(self):
        """Poll port counters and estimate loss on the primary path."""
        while True:
            for datapath in list(self.datapaths.values()):
                parser = datapath.ofproto_parser
                ofproto = datapath.ofproto
                datapath.send_msg(parser.OFPPortStatsRequest(datapath, 0, ofproto.OFPP_ANY))
            hub.sleep(self.MONITOR_INTERVAL)
            self.check_primary_path()

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply_handler(self, ev):
        dpid = ev.msg.datapath.id
        for stat in ev.msg.body:
            key = (dpid, stat.port_no)
            prev = self.port_prev.get(key)
            if prev is not None and stat.rx_packets >= prev:
                self.port_delta[key] = stat.rx_packets - prev
            self.port_prev[key] = stat.rx_packets

    def check_primary_path(self):
        entered = self.port_delta.pop(self.PRIMARY_IN, None)
        left = self.port_delta.pop(self.PRIMARY_OUT, None)
        self.port_delta.clear()
        if self.current_path != "primary" or entered is None or left is None:
            return
        if entered < self.MONITOR_MIN_PACKETS:
            self.bad_windows = 0
            return
        loss_pct = max(0.0, 100.0 * (entered - left) / entered)
        self.logger.info("Path monitor: primary in=%s out=%s est_loss=%.1f%%",
                         entered, left, loss_pct)
        if loss_pct >= self.MONITOR_LOSS_PCT:
            self.bad_windows += 1
            if self.bad_windows >= self.MONITOR_BAD_WINDOWS:
                self.bad_windows = 0
                self.handle_failure("port_stats_loss_%.1f%%" % loss_pct)
        else:
            self.bad_windows = 0

    # ------------------------------------------------------------ rerouting
    def record_event(self, event):
        try:
            with open(EVENT_LOG, "a", encoding="utf-8") as file:
                file.write(json.dumps(event) + "\n")
        except OSError as exc:
            self.logger.warning("Could not write event log %s: %s", EVENT_LOG, exc)

    def handle_failure(self, reason):
        now = time.time()
        if now - self.last_reroute_time < self.REROUTE_COOLDOWN:
            self.logger.info("Reroute suppressed by cooldown; reason=%s", reason)
            return
        if self.current_path != "alternate":
            self.logger.warning("Failure detected (%s); switching to alternate path", reason)
            self.install_alternate_path()
            self.last_reroute_time = now
            self.record_event({"event": "reroute", "path": "alternate",
                               "reason": reason, "timestamp": self.reroute_start_time})

    def loss_alert_listener(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.ALERT_HOST, self.ALERT_PORT))
            self.logger.info("Loss alert listener bound to UDP %s", self.ALERT_PORT)
            while True:
                data, address = sock.recvfrom(4096)
                try:
                    alert = json.loads(data.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self.logger.warning("Ignoring malformed alert from %s", address)
                    continue
                if not isinstance(alert, dict):
                    continue
                reason = alert.get("reason", "unknown")
                self.logger.warning("Loss alert from %s: %s seq=%s",
                                    address[0], reason, alert.get("seq"))
                now = time.time()
                self.loss_alerts = [t for t in self.loss_alerts if now - t <= self.LOSS_WINDOW]
                self.loss_alerts.append(now)
                if len(self.loss_alerts) >= self.LOSS_THRESHOLD:
                    self.loss_alerts.clear()
                    self.handle_failure("loss_alert_" + str(reason))
        except OSError as exc:
            self.logger.exception("UDP alert listener failed: %s", exc)
        finally:
            sock.close()
