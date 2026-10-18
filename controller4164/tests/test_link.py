import time

from ctrl4164.link import LinkController
from ctrl4164.model import make_axes
from ctrl4164.protocol import LanTransport, parse_lan_response, parse_reply

from fake_nt import FakeNT


def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_parse_manual_example():
    replies = parse_lan_response(["XSP=90000", "YSP", "XAC=200", "BRP"], ">;50000>;?>;2000,3000>")
    assert [r.ok for r in replies] == [True, True, False, True]
    assert replies[1].value == 50000
    assert replies[3].values == [2000, 3000]
    assert not parse_reply("XAC=200", "?        >").ok


def test_transport_round_trip():
    nt = FakeNT()
    t = LanTransport("127.0.0.1", nt.port)
    t.open()
    nt.p["Y"]["PS"] = -1234
    r = t.transact(["XSP=500", "YPS", "XQQ"])
    assert [x.ok for x in r] == [True, True, False]
    assert r[1].value == -1234
    t.close()


def connected_link():
    nt = FakeNT()
    logs = []
    axes = make_axes()
    link = LinkController(axes, LanTransport("127.0.0.1", nt.port), log=lambda k, t: logs.append((k, t)))
    link.start()
    assert wait_for(lambda: link.connected and link.axes[0].ms is not None)
    return nt, link, logs


def test_poll_converts_counts_to_degrees_and_reads_io():
    nt, link, _ = connected_link()
    link.axes[1].cfg.bits_per_deg = 2000
    nt.p["Y"]["PS"] = 25000
    assert wait_for(lambda: abs(link.axes[1].pos - 12.5) < 1e-9)
    assert link.inputs()[0] and not link.inputs()[1] and link.inputs()[2]
    assert link.outputs[1] and not link.outputs[0]
    link.close()


def test_servo_and_point_to_point_move():
    nt, link, _ = connected_link()
    assert not link.move_to(0, 10)          # motors off
    link.set_servo(True)
    assert wait_for(lambda: link.servo)
    link.speed_pct = 50
    assert link.move_to(2, 12.345)
    assert wait_for(lambda: abs(link.axes[2].pos - 12.345) < 1e-9)
    assert "ZAP=12345" in nt.received and "ZBG" in nt.received and "ZMM=0" in nt.received
    assert "ZSP=60000" in nt.received        # 120 deg/s * 50% * 1000 bits/deg
    link.close()


def test_jog_and_stop_and_estop():
    nt, link, _ = connected_link()
    link.set_servo(True)
    assert wait_for(lambda: link.servo)
    assert link.jog_start(0, -1)
    assert wait_for(lambda: link.axes[0].moving)
    assert "XMM=1" in nt.received and "XSP=-48000" in nt.received
    link.jog_stop(0)
    assert wait_for(lambda: not link.axes[0].moving)
    link.emergency_stop()
    assert wait_for(lambda: not link.servo)
    assert "XAB" in nt.received and "WMO=0" in nt.received
    assert not link.move_to(0, 1)
    link.close()


def test_soft_limit_clamps_before_sending():
    nt, link, _ = connected_link()
    link.set_servo(True)
    assert wait_for(lambda: link.servo)
    link.move_to(3, 999)
    assert wait_for(lambda: "WAP=200000" in nt.received)
    link.close()


def test_read_and_write_limits():
    nt, link, _ = connected_link()
    nt.p["X"]["LL"], nt.p["X"]["HL"] = -150000, 175000
    link.read_limits()
    assert wait_for(lambda: link.limits_read)
    assert link.axes[0].cfg.min == -150 and link.axes[0].cfg.max == 175
    link.axes[1].cfg.max = 190.5
    link.write_limits()
    assert wait_for(lambda: nt.p["Y"]["HL"] == 190500)
    link.close()


def test_lost_connection_is_reported():
    nt, link, logs = connected_link()
    nt.srv.close()
    link.transport.sock.close()
    assert wait_for(lambda: not link.connected)
    link.close()


def test_outputs_use_os_and_oc_counted_from_one():
    nt, link, _ = connected_link()
    link.set_output(2, True)
    assert wait_for(lambda: nt.op == 0b110)
    assert wait_for(lambda: link.outputs[2])
    link.set_output(1, False)
    assert wait_for(lambda: nt.op == 0b100)
    assert "XOS,3" in nt.received and "XOC,2" in nt.received
    link.close()


def test_limit_switches_faults_and_abort_from_ip():
    nt, link, logs = connected_link()
    nt.ip = (1 << 0) | (1 << 19) | (1 << 22) | (1 << 25)   # Din1, Y FLS, W RLS, Y driver fault
    nt.p["Y"]["MF"] = 1
    assert wait_for(lambda: link.axes[1].fls)
    y, w = link.axes[1], link.axes[3]
    assert y.at_hw_max and not y.at_hw_min and w.at_hw_min
    assert y.drv_fault and link.alarm and y.mf == 1
    assert link.inputs()[0] and not any(link.inputs()[1:])
    assert "תקלת דרייבר" in link.why_cant_move()
    nt.ip = 1 << 28
    assert wait_for(lambda: link.abort_in)
    assert "ABORT" in link.why_cant_move()
    nt.ip = 0
    assert wait_for(lambda: not link.alarm)
    link.close()


def test_in_target_from_sr():
    nt, link, _ = connected_link()
    nt.p["X"]["SR"] = 0x20
    assert wait_for(lambda: link.axes[0].in_target)
    link.close()


def test_version_pe_and_macro_status():
    nt, link, logs = connected_link()
    assert link.firmware == "SC-4M_P,300,4,300,250"
    nt.p["Z"]["PE"] = -7
    nt.qf = {1: 0b0101, 3: 0b0100}
    assert wait_for(lambda: link.axes[2].pe == -7)
    assert link.axes[0].macro_running and not link.axes[1].macro_running
    assert link.axes[2].macro_rte and not link.axes[0].macro_rte
    link.close()


def test_home_macro_per_axis_servo_and_flash():
    nt, link, _ = connected_link()
    link.home_macro = "#HOME_{L}"
    assert link.home(1)
    assert wait_for(lambda: "YQE,#HOME_Y" in nt.received)
    assert not link.run_macro(0, "#TOOLONGNAME")
    link.set_axis_servo(3, True)
    assert wait_for(lambda: link.axes[3].motor_on)
    assert not link.axes[0].motor_on
    assert link.move_to(3, 1.0) and not link.move_to(0, 1.0)
    link.kill_macros()
    link.save_flash()
    assert wait_for(lambda: "XQK" in nt.received and "XSV" in nt.received)
    link.close()
