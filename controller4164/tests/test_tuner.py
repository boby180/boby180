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
    with pytest.raises(ValueError, match="SDViewer"):
        load_recording(p)
