import math

import numpy as np
import pytest

from tune4164.advise import advise
from tune4164.analysis import Analysis, command_metrics, damping_from_overshoot, move_metrics, step_metrics
from tune4164.prm import load_prm, parse_prm, prm_text
from tune4164.recording import load_recording, parse_recording


def second_order(zeta, wn=2 * math.pi * 20, t_end=0.5, n=4000, amp=1000.0, delay=0.05):
    t = np.linspace(0, t_end, n)
    y = np.zeros_like(t)
    for i, ti in enumerate(t):
        s = ti - delay
        if s <= 0:
            continue
        if zeta < 1:
            wd = wn * math.sqrt(1 - zeta ** 2)
            y[i] = 1 - math.exp(-zeta * wn * s) * (math.cos(wd * s) + zeta / math.sqrt(1 - zeta ** 2) * math.sin(wd * s))
        else:
            y[i] = 1 - math.exp(-wn * s) * (1 + wn * s)
    ref = np.where(t > delay, amp, 0.0)
    return t, y * amp, ref


GAINS = {"KP": 100, "KI": 200, "KD": 80, "IS": 32767}


def test_damping_estimate_matches_known_zeta():
    for z in (0.2, 0.4, 0.6):
        t, y, ref = second_order(z)
        s = step_metrics(t, y, ref)
        assert s.ok and abs(s.damping - z) < 0.03
    assert damping_from_overshoot(0.0) is None


def test_position_step_underdamped_lowers_kp():
    t, y, ref = second_order(0.15)
    adv = advise("PIV", "position_step", GAINS, Analysis(step=step_metrics(t, y, ref)))
    assert adv.primary.param == "KP" and adv.primary.new == 75


def test_position_step_overdamped_raises_kp():
    t, y, ref = second_order(1.0, wn=2 * math.pi * 3)
    adv = advise("PIV", "position_step", GAINS, Analysis(step=step_metrics(t, y, ref)))
    assert adv.primary.param == "KP" and adv.primary.new == 120


def test_good_response_needs_no_change():
    t, y, ref = second_order(0.75)
    adv = advise("PIV", "position_step", GAINS, Analysis(step=step_metrics(t, y, ref)))
    assert adv.status == "ok" and not adv.changes


def test_velocity_step_overdamped_raises_ki_and_unstable_lowers_kd():
    t, y, ref = second_order(1.0, wn=2 * math.pi * 5)
    adv = advise("PIV", "velocity_step", {**GAINS, "KP": 0}, Analysis(step=step_metrics(t, y, ref)))
    assert adv.primary.param == "KI" and adv.primary.factor > 1
    t, y, ref = second_order(0.05)
    adv = advise("PIV", "velocity_step", {**GAINS, "KP": 0}, Analysis(step=step_metrics(t, y, ref)))
    assert adv.primary.param == "KD" and adv.primary.new == 48


def test_saturated_command_stops_advice():
    cmd = np.concatenate([np.full(100, 32767.0), np.linspace(32767, 0, 300)])
    t, y, ref = second_order(0.1, n=400)
    adv = advise("PIV", "position_step", GAINS, Analysis(step=step_metrics(t, y, ref), command=command_metrics(cmd)))
    assert adv.status == "stop" and not adv.changes


def test_move_with_ringing_lowers_kp():
    t = np.linspace(0, 1, 5000)
    pe = np.where(t < 0.4, 300 * np.sin(np.pi * t / 0.4), 0.0)
    ring = (t >= 0.4) * 150 * np.exp(-(t - 0.4) * 3) * np.sin(2 * np.pi * 15 * (t - 0.4))
    m = move_metrics(t, pe + ring, moving=t < 0.4)
    adv = advise("PIV", "move", GAINS, Analysis(move=m))
    assert adv.primary.param == "KP" and adv.primary.factor < 1


def test_prm_parse_and_write(tmp_path):
    p = tmp_path / "axis.prm"
    p.write_text("' uploaded\nXKP=120\r\nXKI=250\nXKD[1]=75 ' gain\nXCG=8\nYCG=0\nXFF[2]=10\n", encoding="cp1255")
    prm = load_prm(p)
    assert prm.get("X", "KP") == 120 and prm.get("X", "KD") == 75 and prm.get("X", "FF[2]") == 10
    assert prm.mode("X") == "PID" and prm.mode("Y") == "PIV" and prm.axes() == ["X", "Y"]
    assert "XKP=90" in prm_text({("X", "KP"): 90}, "test")


def test_recording_csv_with_header_and_ms_time(tmp_path):
    lines = ["Time[ms],X Desired Position,X Position,X Driver Command"]
    for i in range(200):
        lines.append(f"{i * 10},{1000 if i > 20 else 0},{min(1000, max(0, (i - 20) * 50))},{100}")
    p = tmp_path / "rec.csv"
    p.write_text("\n".join(lines))
    rec = load_recording(p)
    assert rec.time_column == 0 and abs(rec.time[-1] - 1.99) < 1e-9
    assert rec.guess("reference") == 1 and rec.guess("actual") == 2 and rec.guess("command") == 3


def test_recording_whitespace_without_header_uses_sample_time():
    text = "\n".join(f"{i % 7} {i * 2}" for i in range(50))
    rec = parse_recording(text, dt=0.001)
    assert rec.time_column is None and abs(rec.time[1] - 0.001) < 1e-12 and rec.data.shape == (50, 2)


def test_binary_recording_is_refused_with_explanation(tmp_path):
    p = tmp_path / "rec.sdv"
    p.write_bytes(b"SDV\x00\x01\x02" * 100)
    with pytest.raises(ValueError, match="SDV"):
        load_recording(p)


def _sdv_bytes(vectors):
    import struct
    out = b""
    for name, values in vectors:
        nm = name.encode() + b"\0"
        out += struct.pack("<4I", 0, 1, len(values), 0) + struct.pack("<I", len(nm)) + nm
        out += np.asarray(values, "<f8").tobytes()
    return out + b"\x33\x00\x00\x00\x78\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00PropMotionRecorder\r\n..."


def test_sdv_file_is_read_with_time_in_seconds(tmp_path):
    # The Shell's Time vector is in seconds: RG=64 gives 64 x 61.035us = 3.906 ms per sample.
    n = 500
    t_ms = np.arange(n) * 0.0039059
    pos = 1000 * np.sin(t_ms / 50)
    p = tmp_path / "rec.sdv"
    p.write_bytes(_sdv_bytes([("YPos", pos), ("YAnlgInp", pos * 0.5), ("YVeldt", np.gradient(pos)), ("Time", t_ms)]))
    rec = load_recording(p)
    assert rec.names == ["YPos", "YAnlgInp", "YVeldt", "Time"] and rec.data.shape == (n, 4)
    assert rec.time_column == 3 and abs(rec.time[1] - 0.0039059) < 1e-12
    assert rec.guess("actual") == 0 and rec.guess("tacho") == 1 and rec.guess("velocity") == 2


def test_tacho_gain_off_by_5_percent_suggests_new_ag():
    from tune4164.analysis import tacho_metrics
    t = np.linspace(0, 2, 4000)
    vel = 12000 * np.sin(2 * np.pi * 1.5 * t)
    tacho = 1.05 * vel + np.random.default_rng(1).normal(0, 20, len(t))
    an = Analysis(tacho=tacho_metrics(vel, tacho))
    adv = advise("PIV", "tacho", {"AG": -48388, "AF": 11}, an)
    assert abs(an.tacho.gain - 1.05) < 0.005 and an.tacho.correlation > 0.999
    assert adv.primary.param == "AG" and abs(adv.primary.new - (-48388 / an.tacho.gain)) <= 1


def test_tacho_within_2_percent_needs_no_change():
    from tune4164.analysis import tacho_metrics
    t = np.linspace(0, 2, 4000)
    vel = 12000 * np.sin(2 * np.pi * 1.5 * t)
    adv = advise("PIV", "tacho", {"AG": -48388, "AF": 11}, Analysis(tacho=tacho_metrics(vel, 1.0024 * vel - 4)))
    assert adv.status == "ok" and not adv.changes



def _full_recording(tacho_gain, settle_tau):
    """Down at constant speed, stop for 3 s (PE decays with settle_tau and a small offset), up again."""
    t = np.arange(0, 30, 0.004)
    vref = np.where(t < 12, -6400.0, np.where(t < 15, 0.0, 6400.0))
    pe = np.where(t < 12, -140.0, np.where(t < 15, 40 * np.exp(-(t - 12) / settle_tau) - 16, 115.0))
    vel = vref + np.random.default_rng(2).normal(0, 30, len(t))
    return t, vref, pe, vel, tacho_gain * vel


def test_full_test_fixes_the_speed_sensor_first():
    from tune4164.analysis import tacho_metrics
    t, vref, pe, vel, tacho = _full_recording(0.90, 0.25)
    an = Analysis(tacho=tacho_metrics(vel, tacho), move=move_metrics(t, pe, np.abs(vref) > 0, vref=vref))
    adv = advise("PIV", "full", {"KP": 20, "KI": 50, "KD": 8000, "AG": -17240, "AF": 11}, an)
    assert [c.param for c in adv.changes] == ["AG"]                 # loop changes wait for the sensor fix
    assert any("אחרי תיקון החיישן" in f for f in adv.findings)
    assert abs(an.move.following_neg + 140) < 1 and abs(an.move.following_pos - 115) < 1
    assert abs(an.move.final_error + 16) < 2


def test_full_test_with_good_sensor_tunes_the_loop():
    from tune4164.analysis import tacho_metrics
    t, vref, pe, vel, tacho = _full_recording(1.0, 0.25)
    an = Analysis(tacho=tacho_metrics(vel, tacho), move=move_metrics(t, pe, np.abs(vref) > 0, vref=vref))
    adv = advise("PIV", "full", {"KP": 20, "KI": 50, "KD": 8000, "AG": -17240, "AF": 11}, an)
    params = [c.param for c in adv.changes]
    assert "AG" not in params and params[0] == "KP" and "KI" in params
