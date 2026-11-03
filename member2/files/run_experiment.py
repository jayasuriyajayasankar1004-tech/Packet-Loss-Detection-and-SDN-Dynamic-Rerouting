#!/usr/bin/env python3
"""Run UDP packet-loss experiments on a four-switch Mininet topology."""
import argparse
import glob
import json
import os
import shlex
import subprocess
import time
from functools import partial

from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import info, setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController
from mininet.topo import Topo

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
OF = 'OpenFlow13'
BW = 10
DELAY = '5ms'
ALERT_HOST_IP = '192.168.100.1'
ALERT_PORT = 9999
CONTROLLER_EVENT_LOG = os.environ.get('SDN_EVENT_LOG', '/tmp/sdn_controller_events.jsonl')


def read_controller_reroute(since):
    """Return the first controller reroute event logged after `since`."""
    try:
        with open(CONTROLLER_EVENT_LOG, encoding='utf-8') as file:
            for line in file:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get('event') == 'reroute' and event.get('timestamp', 0) >= since:
                    return event
    except OSError:
        pass
    return None


class PathTopo(Topo):
    def build(self):
        self.addHost('h1', ip='10.0.0.1/24')
        self.addHost('h2', ip='10.0.0.2/24')
        for name in ('s1', 's2', 's3', 's4'):
            self.addSwitch(name, protocols=OF)
        opts = dict(cls=TCLink, bw=BW, delay=DELAY)
        self.addLink('h1', 's1', port2=1)
        self.addLink('h2', 's4', port2=1)
        self.addLink('s1', 's2', port1=2, port2=1, **opts)
        self.addLink('s2', 's4', port1=2, port2=2, **opts)
        self.addLink('s1', 's3', port1=3, port2=1, **opts)
        self.addLink('s3', 's4', port1=2, port2=3, **opts)


def install_flows(net, path):
    fixed = {'s2': [(1, 2), (2, 1)], 's3': [(1, 2), (2, 1)]}
    variable = {
        'primary': {'s1': [(1, 2), (2, 1)], 's4': [(2, 1), (1, 2)]},
        'alternate': {'s1': [(1, 3), (3, 1)], 's4': [(3, 1), (1, 3)]},
    }[path]
    for sw, rules in {**fixed, **variable}.items():
        net.get(sw).cmd('ovs-ofctl -O OpenFlow13 del-flows', sw)
        for in_port, out_port in rules:
            net.get(sw).cmd('ovs-ofctl -O OpenFlow13 add-flow %s "priority=100,in_port=%d,actions=output:%d"' % (sw, in_port, out_port))


def setup_alert_link(net):
    """Create a veth link between h2's namespace and the host's UDP listener."""
    h2 = net.get('h2')
    host_if, peer_if = 'alert-host', 'alert-h2'
    subprocess.run(['ip', 'link', 'delete', host_if], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['ip', 'link', 'add', host_if, 'type', 'veth', 'peer', 'name', peer_if], check=True)
    subprocess.run(['ip', 'addr', 'replace', ALERT_HOST_IP + '/24', 'dev', host_if], check=True)
    subprocess.run(['ip', 'link', 'set', host_if, 'up'], check=True)
    # Must run in the root namespace: alert-h2 does not exist inside h2 yet.
    subprocess.run(['ip', 'link', 'set', peer_if, 'netns', str(h2.pid)], check=True)
    h2.cmd('ip addr replace 192.168.100.2/24 dev %s' % peer_if)
    h2.cmd('ip link set %s up' % peer_if)
    info('*** Alert link ready: h2 192.168.100.2 -> host %s:%d\n' % (ALERT_HOST_IP, ALERT_PORT))


def set_loss(net, pct):
    link = net.linksBetween(net.get('s2'), net.get('s4'))[0]
    for intf in (link.intf1, link.intf2):
        intf.config(bw=BW, delay=DELAY, loss=pct)
        info('*** %s: %s\n' % (intf.name, intf.cmd('tc qdisc show dev %s' % intf.name).strip()))


def find_member1_dir(override=None):
    if override:
        return os.path.abspath(override)
    for path in (os.path.join(ROOT, 'member1'), os.path.join(ROOT, 'member 1'), os.path.join(ROOT, 'member1', 'files')):
        if all(os.path.isfile(os.path.join(path, name)) for name in ('sender.py', 'receiver.py')):
            return path
    hits = glob.glob(os.path.join(ROOT, '**', 'sender.py'), recursive=True)
    for hit in hits:
        folder = os.path.dirname(hit)
        if os.path.isfile(os.path.join(folder, 'receiver.py')):
            return folder
    raise FileNotFoundError('Could not find member1/sender.py and receiver.py')


def run_experiment(net, args):
    h1, h2 = net.get('h1', 'h2')
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    member1 = find_member1_dir(args.member1_dir)
    sender = os.path.join(member1, 'sender.py')
    receiver = os.path.join(member1, 'receiver.py')
    q = shlex.quote
    # Loss alerts go to the controller only in controller mode; in manual mode
    # there is no alert link, so the receiver is started without --alert-host.
    alert_opts = ('--alert-host %s --alert-port %d ' % (ALERT_HOST_IP, ALERT_PORT)
                  if args.mode == 'controller' else '')
    receiver_cmd = ('timeout %d python3 %s --port 5000 --idle 3 %s--recv-log %s --stats %s --output %s > %s 2>&1 &' %
                    (args.duration + 15, q(receiver), alert_opts, q(os.path.join(out, 'recv.csv')),
                     q(os.path.join(out, 'receiver_stats.json')), q(os.path.join(out, 'received.txt')),
                     q(os.path.join(out, 'receiver_out.txt'))))
    h2.cmd(receiver_cmd)
    time.sleep(1)
    start = time.time()
    sender_cmd = ('python3 %s --host 10.0.0.2 --port 5000 --count %d --interval %s --sent-log %s --stats %s > %s 2>&1 &' %
                  (q(sender), args.rate * args.duration, 1.0 / args.rate,
                   q(os.path.join(out, 'sent.csv')), q(os.path.join(out, 'sender_stats.json')),
                   q(os.path.join(out, 'sender_out.txt'))))
    h1.cmd(sender_cmd)
    time.sleep(args.fault_at)
    events = {'mode': args.mode, 'fault': args.fault, 'send_start': start, 'fault_time': time.time()}
    if args.fault == 'down':
        net.configLinkStatus('s2', 's4', 'down')
    else:
        set_loss(net, args.loss)
    info('*** Fault injected: %s\n' % args.fault)
    if args.mode == 'manual':
        time.sleep(args.reroute_delay)
        install_flows(net, 'alternate')
        events['reroute_time'] = time.time()
        info('*** Manual reroute complete\n')
    remaining = args.duration + 2 - (time.time() - start)
    if remaining > 0:
        time.sleep(remaining)
    h1.cmd('wait')
    h2.cmd('wait')
    if args.mode == 'controller':
        reroute = read_controller_reroute(start)
        if reroute:
            events['reroute_time'] = reroute['timestamp']
            events['reroute_reason'] = reroute.get('reason')
            info('*** Controller rerouted (%s) %.3fs after fault\n' %
                 (reroute.get('reason'), reroute['timestamp'] - events['fault_time']))
        else:
            info('*** No controller reroute found in %s\n' % CONTROLLER_EVENT_LOG)
    with open(os.path.join(out, 'events.json'), 'w', encoding='utf-8') as file:
        json.dump(events, file, indent=2)
    info('*** Results saved in %s\n' % out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('manual', 'controller'), default='controller')
    parser.add_argument('--controller-ip', default='127.0.0.1')
    parser.add_argument('--controller-port', type=int, default=6653)
    parser.add_argument('--fault', choices=('loss', 'down'), default='loss')
    parser.add_argument('--loss', type=float, default=30)
    parser.add_argument('--duration', type=int, default=20)
    parser.add_argument('--fault-at', type=int, default=6)
    parser.add_argument('--rate', type=int, default=100)
    parser.add_argument('--member1-dir', default=None)
    parser.add_argument('--app', choices=('udp',), default='udp', help='included UDP sender/receiver application')
    parser.add_argument('--reroute-delay', type=float, default=1.0)
    parser.add_argument('--out', default=os.path.join(ROOT, 'results', 'run1'))
    parser.add_argument('--cli', action='store_true')
    args = parser.parse_args()
    setLogLevel('info')
    if args.mode == 'manual':
        net = Mininet(topo=PathTopo(), controller=None, link=TCLink,
                      switch=partial(OVSSwitch, failMode='secure', protocols=OF))
    else:
        net = Mininet(topo=PathTopo(), link=TCLink,
                      switch=partial(OVSSwitch, protocols=OF),
                      controller=lambda n: RemoteController(n, ip=args.controller_ip, port=args.controller_port))
    net.start()
    try:
        if args.mode == 'controller':
            setup_alert_link(net)
        net.staticArp()
        if args.mode == 'manual':
            install_flows(net, 'primary')
        time.sleep(3)
        info('*** Connectivity check\n')
        net.ping([net.get('h1'), net.get('h2')], timeout=2)
        if args.cli:
            CLI(net)
        else:
            run_experiment(net, args)
    finally:
        net.stop()
        subprocess.run(['ip', 'link', 'delete', 'alert-host'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == '__main__':
    main()
