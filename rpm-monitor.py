#!/usr/bin/env python

# Author: Yudai Yamagishi <yyamagishi@juniper.net>
# This script is provided without any support nor warranty.

import argparse
import ipaddress
import time
import jcs
import jnpr.junos
import jnpr.junos.utils.config
from lxml import etree

DEFAULT_INTERVAL = 5

# JunosDevice Class
class JunosDevice(object):
    @property
    def dev(self):
        if not self.junos_dev.connected:
            self.junos_dev.open()
        return self.junos_dev
    
    @dev.setter
    def dev(self, junos_dev):
        self.junos_dev = junos_dev

    def __init__(self):
        self.junos_dev = jnpr.junos.Device(gather_facts=False)
        self.current_state = dict()

    def check_state(self):
        # get RPM results
        rpm_failed = dict()
        try:
            rpm_fails = self.dev.rpc.get_rpm_probe_results(status="FAIL")
            for result in rpm_fails.findall("./probe-test-results"):
                target_addr = result.findtext("./target-address")
                rpm_failed[target_addr] = False
                jcs.syslog("external.debug", f"rpm-monitor: test {target_addr} is failing")
        except:
            pass

        rpm_passed = dict()
        try:
            rpm_passes = self.dev.rpc.get_rpm_probe_results(status="PASS")
            for result in rpm_passes.findall("./probe-test-results"):
                target_addr = result.findtext("./target-address")
                rpm_passed[target_addr] = False
                jcs.syslog("external.debug", f"rpm-monitor: test {target_addr} is passing")
        except:
            pass 

        # get config
        cfg = self.dev.rpc.get_config(options={"database": "committed"})

        # check if we need to disable per apply-macro
        for elem in cfg.findall("services/monitoring/rpm"):
            for owner_elem in elem.findall("./owner"):
                for test_elem in owner_elem.findall("./test"):
                    # get target address
                    target_addr = test_elem.findtext("./target/address")
                    valid_addr = True
                    try:
                        ip = ipaddress.ip_address(target_addr)
                    except:
                        valid_addr = False
                        jcs.syslog("external.notice", f"rpm-monitor: {target_addr} is not valid IP address")

                    if valid_addr is False:
                        continue

                    # check if any action is needed for this rpm test
                    rpm_monitor_enabled = False
                    for macro_elem in test_elem.findall("./apply-macro"):
                        macro_name = macro_elem.findtext("./name")
                        if macro_name == "disable-static-on-fail":
                            rpm_monitor_enabled = True
                            jcs.syslog("external.debug", f"rpm-monitor: static route check is expected for next-hop {target_addr}")
                        else:
                            jcs.syslog("external.debug", f"rpm-monitor: unknown key {macro_name} for next-hop {target_addr}")

                    if rpm_monitor_enabled is False:
                        continue

                    # find in results dict and change flag
                    if target_addr in rpm_failed:
                        rpm_failed[target_addr] = True
                        jcs.syslog("external.debug", f"rpm-monitor: next-hop {target_addr} is failing")
                    elif target_addr in rpm_passed:
                        rpm_passed[target_addr] = True
                        jcs.syslog("external.debug", f"rpm-monitor: next-hop {target_addr} is alive")
                    else:
                        # BUG
                        jcs.syslog("external.notice", f"rpm-monitor: {target_addr} not found in results dictionary")
                        continue

        # find routes associated 
        config_changes = []
        for elem in cfg.findall("routing-options/rib"):
            rib_name = elem.findtext("./name")
            jcs.syslog("external.debug", f"rpm-monitor: checking rib {rib_name}")
            static_cfg = elem.find("./static")
            for static_elem in static_cfg.findall("./route"):
                route = static_elem.findtext("./name")
                next_hop = static_elem.findtext("./next-hop")
                jcs.syslog("external.debug", f"rpm-monitor: check route {route} next-hop {next_hop}")
                if next_hop in rpm_failed and rpm_failed[next_hop] is True and static_elem.get("inactive") is None:
                    config_changes.append(f"deactivate routing-options rib {rib_name} static route {route}")
                elif next_hop in rpm_passed and rpm_passed[next_hop] is True and static_elem.get("inactive") is not None:
                    config_changes.append(f"activate routing-options rib {rib_name} static route {route}")
                else:
                    jcs.syslog("external.debug", f"rpm-monitor: route {route} next-hop {next_hop} does not need config change")

        # change config
        if len(config_changes) < 1:
            jcs.syslog("external.notice", f"rpm-monitor: check yielded 0 config changes")
            return

        with jnpr.junos.utils.config.Config(self.dev) as cu:
            for change in config_changes:
                jcs.syslog("external.notice", f"rpm-monitor: applying config \"{change}\"")
                try:
                    cu.load(change, format="set")
                except jnpr.junos.utils.config.ConfigLoadError as err:
                    jcs.syslog("external.notice", f"rpm-monitor: failed to load config \"{change}\"")
                    pass

            cu.commit()
            jcs.syslog("external.notice", f"rpm-monitor: commit complete")

        return

def main():
    # Read Arguments
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--interval", help="Interval to monitor RPM health", type=int)
    args, unknown = parser.parse_known_args()

    interval = args.interval or DEFAULT_INTERVAL

    # Main
    jdev = JunosDevice()
    while True:
        try:
            jdev.check_state()
        except Exception as e:
            jcs.syslog("external.notice", f"check_state() call raised exception: {e}")
        time.sleep(interval)

    return

if __name__ == '__main__':
    main()
