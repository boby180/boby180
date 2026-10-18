from ctrl4164.model import HW_MARGIN, SimController, load_settings, save_settings


def ready(n=4):
    logs = []
    c = SimController(n, log=lambda kind, text: logs.append((kind, text)))
    c.connect("SIM", 115200)
    c.set_servo(True)
    return c, logs


def run(c, seconds, dt=0.01):
    for _ in range(int(seconds / dt)):
        c.tick(dt)


def test_move_reaches_target_and_reports_bits():
    c, logs = ready()
    c.axes[0].cfg.bits_per_deg = 2000
    assert c.move_to(0, 12.345)
    run(c, 5)
    assert c.axes[0].pos == 12.345
    assert c.axes[0].bits() == 24690
    assert any("IN POS 12.345 (24690 bits)" in t for _, t in logs)


def test_soft_limit_clamps_target():
    c, _ = ready()
    c.move_to(1, 500)
    assert c.axes[1].target == 200
    assert c.axes[1].soft_hit


def test_jog_stops_at_soft_limit():
    c, _ = ready()
    c.speed_pct = 100
    c.jog_start(2, -1)
    run(c, 5)
    assert c.axes[2].pos == -200
    assert c.axes[2].jog == 0
    assert not c.alarm


def test_hw_limit_raises_alarm_when_soft_limits_off():
    c, _ = ready()
    c.set_soft_limits(False)
    c.speed_pct = 100
    c.move_to(0, 300)
    run(c, 5)
    a = c.axes[0]
    assert a.hw_hit and a.at_hw_max and a.pos == 200 + HW_MARGIN
    assert c.alarm and not c.servo
    assert not c.move_to(0, 0)
    c.reset()
    c.set_servo(True)
    assert c.move_to(0, 0)


def test_estop_blocks_motion_and_sets_status_bits():
    c, _ = ready()
    c.emergency_stop()
    assert not c.jog_start(0, 1)
    word = c.status_word()
    assert word & (1 << 4)          # E-Stop
    assert not word & 1             # not Ready
    assert not c.inputs()[0]        # E-Stop OK input dropped


def test_step_accumulates_from_pending_target():
    c, _ = ready()
    c.step(3, +1, 1.0)
    c.step(3, +1, 1.0)
    assert c.axes[3].target == 2.0


def test_raw_move_command():
    c, _ = ready()
    c.send_raw("J2 MOVE -15.5")
    assert c.axes[1].target == -15.5
    c.send_raw("J9 MOVE 1")
    assert all(a.target in (None, -15.5) for a in c.axes)


def test_settings_round_trip(tmp_path):
    c, _ = ready()
    c.axes[2].cfg.bits_per_deg = 3600
    c.axes[2].cfg.min = -150
    path = tmp_path / "s.json"
    save_settings(path, c.axes)
    d, _ = ready()
    assert load_settings(path, d.axes)
    assert d.axes[2].cfg.bits_per_deg == 3600
    assert d.axes[2].cfg.min == -150
    assert d.axes[0].cfg.max == 200
