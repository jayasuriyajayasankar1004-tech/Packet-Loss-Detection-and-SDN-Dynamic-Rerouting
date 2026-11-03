#!/usr/bin/env python3
"""Live dashboard backend for the Packet Loss Detection and SDN Rerouting project.

Run from the project root (root is needed for ovs-ofctl, ip link and Mininet):

    sudo /usr/bin/python3 dashboard/server.py

then open  http://localhost:8080  in Firefox inside the VM.

The server only listens on 127.0.0.1, so nothing outside the VM can reach it.
It uses only the Python standard library.
"""
import glob
import json
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
RESULTS = os.path.join(ROOT, 'results')
EVENT_LOG = os.environ.get('SDN_EVENT_LOG', '/tmp/sdn_controller_events.jsonl')
RYU_LOG = os.environ.get('RYU_LOG', '/tmp/ryu_controller.txt')
EXP_LOG = '/tmp/dashboard_experiment.txt'
PORT = int(os.environ.get('DASH_PORT', '8080'))
PYTHON = '/usr/bin/python3'
SWITCHES = ('s1', 's2', 's3', 's4')
PRIMARY_LINK = ('s2-eth2', 's4-eth2')          # both ends of the s2-s4 link
NAME_RE = re.compile(r'^[A-Za-z0-9_.-]{1,80}$')

LOCK = threading.Lock()
EXP = {'phase': 'idle', 'name': None, 'cfg': None, 'started': None, 'finished': None, 'message': ''}


# ----------------------------------------------------------------- helpers
def run(cmd, timeout=5):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, '', str(exc)


def tail(path, n=60):
    try:
        with open(path, encoding='utf-8', errors='replace') as file:
            return [line.rstrip('\n') for line in file.readlines()[-n:]]
    except OSError:
        return None


def read_json(path):
    try:
        with open(path, encoding='utf-8') as file:
            return json.load(file)
    except (OSError, ValueError):
        return None


def ryu_running():
    return run(['pgrep', '-f', 'ryu-manager'])[0] == 0


def mininet_up():
    return os.path.exists('/sys/class/net/s1-eth1')


def link_state(iface):
    try:
        with open('/sys/class/net/%s/operstate' % iface) as file:
            return file.read().strip()
    except OSError:
        return None


def port_no(token):
    token = token.strip('"')
    match = re.search(r'-eth(\d+)$', token)
    if match:
        return int(match.group(1))
    return int(token) if token.isdigit() else token


def dump_flows(switch):
    code, out, _ = run(['ovs-ofctl', '-O', 'OpenFlow13', 'dump-flows', switch])
    if code != 0:
        return None
    rules = []
    for line in out.splitlines():
        prio = re.search(r'priority=(\d+)', line)
        if not prio:
            continue
        in_port = re.search(r'in_port=("?[\w-]+"?)', line)
        actions = re.search(r'actions=(\S+)', line)
        packets = re.search(r'n_packets=(\d+)', line)
        action = actions.group(1) if actions else ''
        out_port = re.search(r'output:("?[\w-]+"?)', action)
        rules.append({
            'priority': int(prio.group(1)),
            'in_port': port_no(in_port.group(1)) if in_port else None,
            'out_port': port_no(out_port.group(1)) if out_port else None,
            'to_controller': 'CONTROLLER' in action,
            'packets': int(packets.group(1)) if packets else 0,
        })
    rules.sort(key=lambda r: (-r['priority'], str(r['in_port'])))
    return rules


PORT_RE = re.compile(r'port\s+"?([^":\s]+)"?:\s*rx\s+(.*?)\n\s*tx\s+(.*?)(?=\n\s*(?:duration|port\s)|\Z)', re.S)
KV_RE = re.compile(r'(\w+)=(\?|\d+)')


def port_counters(switch):
    """rx/tx packet, drop and error counters for every port (ovs-ofctl dump-ports)."""
    code, out, _ = run(['ovs-ofctl', '-O', 'OpenFlow13', 'dump-ports', switch])
    if code != 0:
        return None
    ports = {}
    for name, rx, tx in PORT_RE.findall(out):
        if name.upper() == 'LOCAL':
            continue
        r = {k: int(v) for k, v in KV_RE.findall(rx) if v != '?'}
        t = {k: int(v) for k, v in KV_RE.findall(tx) if v != '?'}
        ports[str(port_no(name))] = {
            'rx_pkts': r.get('pkts', 0), 'rx_drop': r.get('drop', 0), 'rx_errs': r.get('errs', 0),
            'rx_crc': r.get('crc', 0), 'tx_pkts': t.get('pkts', 0), 'tx_drop': t.get('drop', 0),
            'tx_errs': t.get('errs', 0), 'tx_coll': t.get('coll', 0)}
    return ports


def port_states(switch):
    """OpenFlow port state (LIVE / LINK_DOWN) and admin config (PORT_DOWN)."""
    code, out, _ = run(['ovs-ofctl', '-O', 'OpenFlow13', 'dump-ports-desc', switch])
    if code != 0:
        return {}
    states, current = {}, None
    for line in out.splitlines():
        head = re.match(r'\s*(\d+)\(([^)]+)\):', line)
        if head:
            current = head.group(1)
            states[current] = {'name': head.group(2), 'state': '', 'config': ''}
        elif current and 'state:' in line:
            states[current]['state'] = line.split('state:', 1)[1].strip()
        elif current and 'config:' in line:
            states[current]['config'] = line.split('config:', 1)[1].strip()
    return states


def switch_ports():
    result = {}
    for sw in SWITCHES:
        counters = port_counters(sw)
        if counters is None:
            result[sw] = None
            continue
        desc = port_states(sw)
        for no, info in counters.items():
            d = desc.get(no, {})
            info['name'] = d.get('name', '%s-eth%s' % (sw, no))
            info['link_down'] = 'LINK_DOWN' in d.get('state', '') or link_state(info['name']) == 'down'
            info['admin_down'] = 'PORT_DOWN' in d.get('config', '')
        result[sw] = counters
    return result


ERROR_LINE_RE = re.compile(r'error|warn|fail|unreachable|refused|link down|loss alert|port.?stats|disconnect|timed? ?out|unconfirmed|duplicate|est_loss=[1-9]', re.I)
NOISE_RE = re.compile(r'eventlet|deprecat|migrating|readthedocs|RLock', re.I)


def error_lines(lines, limit=40):
    return [l for l in (lines or []) if ERROR_LINE_RE.search(l) and not NOISE_RE.search(l)][-limit:]


def active_path(flows):
    for rule in (flows or {}).get('s1') or []:
        if rule['priority'] == 100 and rule['in_port'] == 1:
            return {2: 'primary', 3: 'alternate'}.get(rule['out_port'], 'unknown')
    return None


def controller_events():
    events = []
    for line in tail(EVENT_LOG, 20) or []:
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    return events


def chown_to_user(path):
    uid, gid = os.environ.get('SUDO_UID'), os.environ.get('SUDO_GID')
    if not uid or not gid:
        return
    for base, dirs, files in os.walk(path):
        for name in [base] + [os.path.join(base, f) for f in dirs + files]:
            try:
                os.chown(name, int(uid), int(gid))
            except OSError:
                pass


# --------------------------------------------------------------- endpoints
def status():
    up = mininet_up()
    flows = {s: dump_flows(s) for s in SWITCHES} if up else {}
    states = [link_state(i) for i in PRIMARY_LINK]
    if not up:
        link = None
    elif all(s == 'up' for s in states):
        link = 'up'
    else:
        link = 'down'
    ryu_tail = tail(RYU_LOG, 80)
    with LOCK:
        exp = dict(EXP)
    return {
        'time': time.time(),
        'ryu': ryu_running(),
        'network': up,
        'link_s2_s4': link,
        'active_path': active_path(flows),
        'flows': flows,
        'ports': switch_ports() if up else {},
        'ryu_errors': error_lines(ryu_tail),
        'exp_errors': error_lines(tail(EXP_LOG, 400)) if exp['phase'] != 'idle' else [],
        'ryu_log': ryu_tail,
        'ryu_log_path': RYU_LOG,
        'events': controller_events(),
        'experiment': exp,
    }


def list_runs():
    runs = []
    for folder in glob.glob(os.path.join(RESULTS, '*')):
        if not os.path.isdir(folder):
            continue
        name = os.path.basename(folder)
        events = read_json(os.path.join(folder, 'events.json'))
        if events is None:
            continue
        runs.append({
            'name': name,
            'mtime': os.path.getmtime(folder),
            'events': events,
            'summary': read_json(os.path.join(folder, 'summary.json')),
            'receiver': read_json(os.path.join(folder, 'receiver_stats.json')),
            'sender': read_json(os.path.join(folder, 'sender_stats.json')),
            'has_plot': os.path.exists(os.path.join(folder, 'loss_plot.png')),
        })
    runs.sort(key=lambda r: -r['mtime'])
    return runs


def experiment_state():
    with LOCK:
        exp = dict(EXP)
    exp['log'] = tail(EXP_LOG, 40) if exp['phase'] != 'idle' else []
    return exp


def watch_experiment(proc, name, logfile):
    proc.wait()
    folder = os.path.join(RESULTS, name)
    with LOCK:
        EXP['phase'] = 'analysing'
    logfile.write('\n*** Running analyze.py\n')
    logfile.flush()
    ok = proc.returncode == 0 and os.path.exists(os.path.join(folder, 'events.json'))
    if ok:
        result = subprocess.run([PYTHON, os.path.join(ROOT, 'member2', 'files', 'analyze.py'), folder],
                                cwd=ROOT, stdout=logfile, stderr=subprocess.STDOUT, timeout=120)
        ok = result.returncode == 0
    chown_to_user(folder)
    logfile.close()
    with LOCK:
        EXP['phase'] = 'done' if ok else 'failed'
        EXP['finished'] = time.time()
        EXP['message'] = ('Finished. Results saved in results/%s' % name) if ok else \
            'The experiment failed. Read the output below.'


def start_experiment(body):
    mode = body.get('mode')
    fault = body.get('fault')
    try:
        loss = int(body.get('loss', 30))
        duration = int(body.get('duration', 20))
    except (TypeError, ValueError):
        return 400, {'error': 'Loss and duration must be whole numbers.'}
    if mode not in ('controller', 'manual') or fault not in ('loss', 'down'):
        return 400, {'error': 'Mode must be controller or manual, and fault must be loss or down.'}
    if not 1 <= loss <= 90 or not 10 <= duration <= 60:
        return 400, {'error': 'Loss must be 1 to 90 and duration 10 to 60 seconds.'}
    with LOCK:
        if EXP['phase'] in ('running', 'analysing'):
            return 409, {'error': 'An experiment is already running. Wait for it to finish.'}
        if mininet_up():
            return 409, {'error': 'A Mininet network is already running (for example the --cli demo). '
                                  'Type exit at the mininet> prompt first.'}
        if mode == 'controller' and not ryu_running():
            return 409, {'error': 'Ryu is not running. Start it in Terminal 1 first.'}
        name = '%s_%s%s_%s' % (mode, fault, loss if fault == 'loss' else '', time.strftime('%H%M%S'))
        cmd = [PYTHON, os.path.join(ROOT, 'member2', 'files', 'run_experiment.py'),
               '--mode', mode, '--fault', fault, '--loss', str(loss),
               '--duration', str(duration), '--fault-at', '6', '--rate', '100',
               '--reroute-delay', '1', '--out', os.path.join(RESULTS, name)]
        os.makedirs(RESULTS, exist_ok=True)
        logfile = open(EXP_LOG, 'w', encoding='utf-8')
        logfile.write('$ ' + ' '.join(cmd) + '\n')
        logfile.flush()
        proc = subprocess.Popen(cmd, cwd=ROOT, stdout=logfile, stderr=subprocess.STDOUT)
        EXP.update({'phase': 'running', 'name': name, 'started': time.time(), 'finished': None,
                    'cfg': {'mode': mode, 'fault': fault, 'loss': loss, 'duration': duration},
                    'message': 'Running. This takes about %d seconds.' % (duration + 12)})
    threading.Thread(target=watch_experiment, args=(proc, name, logfile), daemon=True).start()
    return 200, {'ok': True, 'name': name}


def set_link(body):
    state = body.get('state')
    if state not in ('up', 'down'):
        return 400, {'error': 'State must be up or down.'}
    if not mininet_up():
        return 409, {'error': 'No Mininet network is running. Start the --cli demo first.'}
    errors = []
    for iface in PRIMARY_LINK:
        code, _, err = run(['ip', 'link', 'set', iface, state])
        if code != 0:
            errors.append(err.strip() or 'failed on %s' % iface)
    if errors:
        return 500, {'error': '; '.join(errors)}
    return 200, {'ok': True, 'state': state}


def ping():
    if not mininet_up():
        return 409, {'error': 'No Mininet network is running. Start the --cli demo first.'}
    code, out, _ = run(['pgrep', '-f', 'mininet:h1'])
    pids = out.split()
    if code != 0 or not pids:
        return 409, {'error': 'Could not find host h1. Is the Mininet network running?'}
    code, out, err = run(['mnexec', '-a', pids[0], 'ping', '-c', '3', '-W', '1', '10.0.0.2'], timeout=12)
    return 200, {'ok': code == 0, 'output': (out or err).strip()}


# ------------------------------------------------------------------ server
class Handler(BaseHTTPRequestHandler):
    server_version = 'SDNDashboard/1.0'

    def log_message(self, fmt, *args):
        pass

    def send_json(self, code, payload):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, path, ctype):
        try:
            with open(path, 'rb') as file:
                data = file.read()
        except OSError:
            self.send_json(404, {'error': 'Not found'})
            return
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ('/', '/index.html'):
            self.send_file(os.path.join(HERE, 'index.html'), 'text/html; charset=utf-8')
        elif url.path == '/api/status':
            self.send_json(200, status())
        elif url.path == '/api/runs':
            self.send_json(200, {'runs': list_runs()})
        elif url.path == '/api/experiment':
            self.send_json(200, experiment_state())
        elif url.path == '/api/plot':
            name = (parse_qs(url.query).get('run') or [''])[0]
            if not NAME_RE.match(name):
                self.send_json(400, {'error': 'Bad run name'})
                return
            self.send_file(os.path.join(RESULTS, name, 'loss_plot.png'), 'image/png')
        else:
            self.send_json(404, {'error': 'Not found'})

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length > 10000:
            self.send_json(413, {'error': 'Request too large'})
            return
        try:
            body = json.loads(self.rfile.read(length) or b'{}')
        except ValueError:
            self.send_json(400, {'error': 'Body must be JSON'})
            return
        path = urlparse(self.path).path
        if path == '/api/experiment':
            self.send_json(*start_experiment(body))
        elif path == '/api/link':
            self.send_json(*set_link(body))
        elif path == '/api/ping':
            self.send_json(*ping())
        else:
            self.send_json(404, {'error': 'Not found'})


def main():
    if os.geteuid() != 0:
        print('Note: not running as root. Flow rules, link control and experiments need:')
        print('      sudo /usr/bin/python3 dashboard/server.py')
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    print('SDN dashboard running at http://localhost:%d  (Ctrl+C to stop)' % PORT)
    print('Project folder: %s' % ROOT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.')


if __name__ == '__main__':
    main()
