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


FULL_GAINS = {f"{k}[{i}]": v for k, v in (("KP", 20), ("KI", 50), ("KD", 8000)) for i in (1, 2, 3)} | {"AG": -17240, "AF": 11}
GAINS = {f"{k}[{i}]": v for k, v in (("KP", 100), ("KI", 200), ("KD", 80)) for i in (1, 2, 3)} | {"IS": 32767}


def test_damping_estimate_matches_known_zeta():
    for z in (0.2, 0.4, 0.6):
        t, y, ref = second_order(z)
        s = step_metrics(t, y, ref)
        assert s.ok and abs(s.damping - z) < 0.03
    assert damping_from_overshoot(0.0) is None


def test_position_step_underdamped_lowers_kp():
    t, y, ref = second_order(0.15)
    adv = advise("PIV", "position_step", GAINS, Analysis(step=step_metrics(t, y, ref)))
    assert adv.primary.param == "KP[1]" and adv.primary.new == 75


def test_position_step_overdamped_raises_kp():
    t, y, ref = second_order(1.0, wn=2 * math.pi * 3)
    adv = advise("PIV", "position_step", GAINS, Analysis(step=step_metrics(t, y, ref)))
    assert adv.primary.param == "KP[1]" and adv.primary.new == 120


def test_good_response_needs_no_change():
    t, y, ref = second_order(0.75)
    adv = advise("PIV", "position_step", GAINS, Analysis(step=step_metrics(t, y, ref)))
    assert adv.status == "ok" and not adv.changes


def test_velocity_step_overdamped_raises_ki_and_unstable_lowers_kd():
    t, y, ref = second_order(1.0, wn=2 * math.pi * 5)
    adv = advise("PIV", "velocity_step", {**GAINS, "KP[1]": 0}, Analysis(step=step_metrics(t, y, ref)))
    assert adv.primary.param == "KI[1]" and adv.primary.factor > 1
    t, y, ref = second_order(0.05)
    adv = advise("PIV", "velocity_step", {**GAINS, "KP[1]": 0}, Analysis(step=step_metrics(t, y, ref)))
    assert adv.primary.param == "KD[1]" and adv.primary.new == 48


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
    assert adv.primary.param == "KP[2]" and adv.primary.factor < 1


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
    adv = advise("PIV", "full", FULL_GAINS, an)
    assert [c.param for c in adv.changes] == ["AG"]                 # loop changes wait for the sensor fix
    assert any("אחרי תיקון החיישן" in f for f in adv.findings)
    assert abs(an.move.following_neg + 140) < 1 and abs(an.move.following_pos - 115) < 1
    assert abs(an.move.final_error + 16) < 2


def test_full_test_with_good_sensor_tunes_the_loop():
    from tune4164.analysis import tacho_metrics
    t, vref, pe, vel, tacho = _full_recording(1.0, 0.25)
    an = Analysis(tacho=tacho_metrics(vel, tacho), move=move_metrics(t, pe, np.abs(vref) > 0, vref=vref))
    adv = advise("PIV", "full", FULL_GAINS, an)
    params = [c.param for c in adv.changes]
    assert "AG" not in params and params[0] == "KP[2]" and "KI[2]" in params


def test_full_loop_change_carries_numbers():
    from tune4164.advise import Change
    c = Change("KP", 20, 1.2, "slow settle")
    assert (c.old, c.new) == (20, 24)
    assert Change("KI", 50, 1.25, "").new in (62, 63)  # 62.5 rounds to even


def test_after_stop_changes_sets_2_and_3_only():
    from tune4164.advise import STOP, gain_change
    c = gain_change({"KP[1]": 14, "KP[2]": 14, "KP[3]": 12}, "KP", STOP, 0.7, "")
    assert c.items() == [("KP[2]", 14, 10), ("KP[3]", 12, 8)] and c.complete


def test_slow_wobble_after_stop_points_to_ki():
    from tune4164.advise import Advice, _move_advice, gain_change
    from tune4164.analysis import MoveMetrics
    mv = MoveMetrics(ok=True, has_stop=True, stop_time=25.8, end_error_peak=73, settle_after_end=None,
                     final_error=-7, tolerance=45, residual_freq=0.1, oscillations=4, sustained=True)
    adv = Advice()
    g = {"KP[2]": 14, "KP[3]": 14, "KI[2]": 20, "KI[3]": 20}
    _move_advice(mv, adv, lambda p, sets, f, why: adv.changes.append(gain_change(g, p, sets, f, why)))
    assert [k for k, _, _ in adv.primary.items()] == ["KI[2]", "KI[3]"] and adv.primary.new == 14


def test_prm_reads_three_gain_sets():
    prm = parse_prm("XKP[1]=20\nXKP[2]=24\nXKP[3]=30\n")
    assert [prm.get("X", f"KP[{i}]") for i in (1, 2, 3)] == [20, 24, 30]


def test_tuning_keys_keep_limits():
    from tune4164.prm import TUNING_KEYS
    for k in ("IS", "ER", "TL", "AG", "AF", "CG", "KP[3]", "KI[3]", "KD[3]"):
        assert k in TUNING_KEYS


def _friction_move(jump_over=False):
    t = np.arange(0, 6, 0.005)
    moving = t < 2
    pos = np.where(moving, t * 1000, 2037.0)                 # overshoots the 2000 target by 37 and sticks
    brk = (t >= 3.5)
    pos = np.where(brk, 2037 - np.clip((t - 3.5) * 200, 0, 37 + (60 if jump_over else 0)), pos)
    pe = np.where(moving, 0.0, 2000 - pos)
    cmd = np.where(moving, 1300.0, np.clip(300 - (t - 2) * 550, -520, 300))
    return t, pe, moving, pos, cmd


def test_stick_slip_after_stop_raises_ki():
    from tune4164.advise import Advice, _move_advice, gain_change
    t, pe, moving, pos, cmd = _friction_move()
    mv = move_metrics(t, pe, moving, pos=pos, cmd=cmd)
    assert mv.stick and mv.stuck_error == -37 and 1.3 < mv.stuck_time < 1.7 and not mv.stick_jump_over
    adv = Advice()
    g = {"KI[2]": 20, "KI[3]": 20}
    _move_advice(mv, adv, lambda p, sets, f, why: adv.changes.append(gain_change(g, p, sets, f, why)))
    assert adv.primary.items() == [("KI[2]", 20, 26), ("KI[3]", 20, 26)]


def test_stick_slip_jumping_over_lowers_ki():
    t, pe, moving, pos, cmd = _friction_move(jump_over=True)
    mv = move_metrics(t, pe, moving, pos=pos, cmd=cmd)
    assert mv.stick and mv.stick_jump_over


def test_moving_from_velocity_closes_short_gaps():
    from tune4164.analysis import moving_from_velocity
    t = np.arange(0, 1, 0.01)
    v = np.where((t < 0.4) | ((t > 0.45) & (t < 0.7)), 1000.0, 0.0)
    m = moving_from_velocity(t, v)
    assert m[42] and not m[90]


def _entry(rec, gains, metrics, advice=None, test="full"):
    return {"axis": "Y", "test": test, "rec_key": rec, "recording": rec, "time": "t", "gains": gains,
            "metrics": metrics, "advice": advice or [], "top_speed": 10000}


def test_history_compare_better_and_applied():
    from tune4164.history import compare
    prev = _entry("roll1", {"KI[2]": 20, "KI[3]": 20}, {"settle": 2.6, "stuck_time": 2.5, "end_peak": 37},
                  [{"param": "KI[2]", "old": 20, "new": 26}, {"param": "KI[3]", "old": 20, "new": 26}])
    cur = _entry("roll2", {"KI[2]": 26, "KI[3]": 26}, {"settle": 1.5, "stuck_time": 1.2, "end_peak": 36})
    c = compare(prev, cur)
    assert c.verdict == "better" and c.applied is True
    assert c.changed == [("KI[2]", 20, 26), ("KI[3]", 20, 26)]


def test_history_worse_steps_back_half_way():
    from tune4164.history import compare, step_back
    prev = _entry("roll3", {"KI[2]": 26, "KI[3]": 26}, {"settle": 1.0, "stuck_time": 0.5, "oscillations": 0},
                  [{"param": "KI[2]", "old": 26, "new": 34}, {"param": "KI[3]", "old": 26, "new": 34}])
    cur = _entry("roll4", {"KI[2]": 34, "KI[3]": 34}, {"settle": "inf", "stuck_time": 0.5, "oscillations": 5})
    c = compare(prev, cur)
    assert c.verdict == "worse"
    back = step_back(c, cur["gains"])
    assert back.items() == [("KI[2]", 34, 30), ("KI[3]", 34, 30)]


def test_history_file_round_trip(tmp_path):
    from tune4164.history import DEFAULT_UNIT, History
    h = History(tmp_path / "h.json")
    h.put(_entry("a", {"KP[1]": 1}, {"settle": "inf"}))
    h.put(_entry("b", {"KP[1]": 2}, {"settle": 0.4}))
    h.put(_entry("b", {"KP[1]": 3}, {"settle": 0.3}))          # analysing the same recording again replaces it
    h2 = History(tmp_path / "h.json")
    assert [e["recording"] for e in h2.for_axis(DEFAULT_UNIT, "Y")] == ["a", "b"]
    assert h2.previous(DEFAULT_UNIT, "Y", "b")["recording"] == "a"
    h2.clear(DEFAULT_UNIT, "Y")
    assert History(tmp_path / "h.json").for_axis(DEFAULT_UNIT, "Y") == []


def test_units_keep_separate_histories(tmp_path):
    from tune4164.history import History
    h = History(tmp_path / "h.json")
    h.put({**_entry("r1", {}, {}), "unit": "SN100"})
    h.add_unit("SN200")
    h.put({**_entry("r1", {}, {}), "unit": "SN200"})            # same file name, other rotator: both kept
    h.put({**_entry("r2", {}, {}), "unit": "SN200"})
    h2 = History(tmp_path / "h.json")
    assert [e["recording"] for e in h2.for_axis("SN100", "Y")] == ["r1"]
    assert [e["recording"] for e in h2.for_axis("SN200", "Y")] == ["r1", "r2"]
    assert h2.previous("SN100", "Y", "r9")["recording"] == "r1"
    h2.clear("SN200", "Y")
    assert [e["recording"] for e in History(tmp_path / "h.json").for_axis("SN100", "Y")] == ["r1"]
    assert "SN200" in History(tmp_path / "h.json").units()


def test_old_history_without_units_goes_to_default(tmp_path):
    import json
    from tune4164.history import DEFAULT_UNIT, History
    (tmp_path / "h.json").write_text(json.dumps({"entries": [_entry("old", {}, {})]}), encoding="utf-8")
    assert [e["recording"] for e in History(tmp_path / "h.json").for_axis(DEFAULT_UNIT, "Y")] == ["old"]


def test_fingerprint_spots_another_controller():
    from tune4164.history import fingerprint, other_unit
    a = fingerprint(parse_prm("YPA[43]=8000000\nYPA[52]=-9600\nYHL=2000000000\n"), "Y")
    b = fingerprint(parse_prm("YPA[43]=12000000\nYPA[52]=-14400\nYHL=2000000000\n"), "Y")
    assert other_unit(a, a) == [] and other_unit(a, b) == ["PA[43]", "PA[52]"]


def test_run_file_round_trip_restores_exactly(tmp_path):
    import os
    from tune4164.history import History, extract_run, load_run, run_document
    rec = tmp_path / "src" / "roll9.sdv"
    rec.parent.mkdir()
    rec.write_bytes(bytes(range(256)) * 50)
    os.utime(rec, (1_700_000_000, 1_700_000_000))
    prm = "' test\r\nYKP[1]=14\r\nYKI[2]=26\r\n"
    h = History(tmp_path / "h.json")
    e = {**_entry("roll9.sdv|12800|1700000000", {"KI[2]": 26}, {}), "unit": "SN1", "recording": "roll9.sdv"}
    h.save_run(e, run_document(e, "cba.prm", prm, "roll9.sdv", rec.read_bytes(), rec.stat().st_mtime))
    h.put(e)
    doc = load_run(History(tmp_path / "h.json").run_path(e))
    assert doc["prm"]["text"] == prm and doc["entry"]["gains"] == {"KI[2]": 26}
    p, r = extract_run(doc, tmp_path / "out")
    assert p.read_bytes() == prm.encode() and r.read_bytes() == rec.read_bytes()
    assert int(r.stat().st_mtime) == 1_700_000_000           # same history key when analysed again
    h.clear("SN1", "Y")
    assert not (tmp_path / "archive" / e["archive"]).exists()


def test_reanalysing_keeps_order_and_predecessor(tmp_path):
    from tune4164.history import History
    h = History(tmp_path / "h.json")
    for k in ("r1", "r2", "r3"):
        h.put({**_entry(k, {}, {}), "unit": "U"})
    h.put({**_entry("r1", {"KP[1]": 9}, {}), "unit": "U"})       # restored and analysed again
    assert [e["rec_key"] for e in h.for_axis("U", "Y")] == ["r1", "r2", "r3"]
    assert h.previous("U", "Y", "r1") is None and h.previous("U", "Y", "r3")["rec_key"] == "r2"
    assert h.previous("U", "Y", "r4")["rec_key"] == "r3"
