# Member 2 Contribution — Mininet Network Simulation and Testing

**Contributor:** Nischal  
**Role:** Member 2 — Mininet Topology, Network Simulation, Fault Injection and Experiment Analysis  
**Project:** Packet Loss Detection and SDN Dynamic Rerouting

## Contribution overview

I worked on the Mininet-based network environment used to test packet-loss detection and SDN-assisted dynamic rerouting. My responsibility was to build and validate the data-plane topology, introduce controlled network faults, run experiments, and collect measurements that help evaluate packet loss and recovery.

## Work carried out

### 1. Mininet topology design

- Designed a topology with two hosts (`h1` and `h2`) and four Open vSwitch switches (`s1`, `s2`, `s3`, and `s4`).
- Connected the hosts and switches to provide two possible routes between sender and receiver.
- Used Mininet and Open vSwitch to create a repeatable network simulation environment.

### 2. Primary and alternate path configuration

- Configured the primary route: `h1 → s1 → s2 → s4 → h2`.
- Configured the alternate route: `h1 → s1 → s3 → s4 → h2`.
- Prepared the topology so traffic could be tested on the primary route and moved to the alternate route when the primary route experienced a fault.
- Coordinated the data-plane setup with the SDN controller component developed by Member 3.

### 3. Network fault injection

- Tested packet-loss scenarios by applying loss to links in the Mininet topology.
- Included high-loss test cases such as 30% and 60% packet loss as part of the experiment plan.
- Tested link-failure scenarios by bringing a link down.
- Observed how traffic behaved when the primary path became unreliable or unavailable.

### 4. Experiment execution

- Used the Member 2 experiment runner (`member2/files/run_experiment.py`) to run network tests.
- Ran manual-baseline and controller-assisted experiment modes.
- Configured experiment parameters such as fault type, loss percentage, duration, fault injection time, and traffic rate.
- Used Mininet cleanup and repeatable run procedures between experiments.

### 5. Measurement and result collection

- Collected sender and receiver logs and experiment output files.
- Worked with CSV and JSON output, including sent/received packet records and event information.
- Tracked packet loss before, during, and after fault injection.
- Examined path-switch delay and recovery time to understand how quickly communication recovered.

### 6. Result analysis and comparison

- Used `analyze.py` to summarize an experiment and generate a packet-loss plot.
- Used `compare.py` to compare manual-baseline and controller-assisted runs.
- Used `probe.py` as a supporting network-testing utility.
- Reviewed the generated summaries and plots to evaluate the behavior of the simulated network.

## Tools and technologies

- **Mininet** — emulation of the network topology and hosts.
- **Open vSwitch (OVS)** — software switches used in the data plane.
- **Ubuntu/Linux** — environment for running Mininet experiments.
- **Python 3** — experiment automation and analysis scripts.
- **CSV, JSON and Matplotlib** — experiment logs, summaries and visualization.
- **OpenFlow 1.3 / Ryu integration** — interaction between the simulated data plane and the SDN controller (controller implementation belongs to Member 3).

## Files associated with the Member 2 work

| File | Purpose |
|---|---|
| `member2/files/run_experiment.py` | Runs Mininet experiments and configures test scenarios |
| `member2/files/analyze.py` | Analyzes an individual experiment and generates a loss plot |
| `member2/files/compare.py` | Compares manual and controller-mode experiment results |
| `member2/files/probe.py` | Supporting network probe/testing utility |

## Collaboration and module boundaries

- **Member 1:** UDP sender/receiver, sequence tracking, ACK/NACK, and retransmission.
- **Member 2 (my role):** Mininet topology, Open vSwitch data plane, fault injection, experiment execution, measurement, and result analysis.
- **Member 3:** Ryu SDN controller and dynamic flow-rule updates for rerouting.

My contribution focused on the network simulation and evaluation layer. The UDP application and Ryu controller are credited to their respective team members.

## Learning outcomes

Through this work, I gained practical experience with virtual network topology design, Mininet, Open vSwitch, network fault emulation, packet-loss measurement, experiment automation, and analysis of network recovery behavior.

---

*This document records the Member 2 responsibilities and the scripts present in the project repository. Include numerical performance claims only when they are supported by experiment results reproduced by the team.*
