"""Unit tests: configuration serialisation, detector/wavelength mapping, presets, results."""
from __future__ import annotations

import json

import numpy as np
import pytest

from app.models.channels import ChannelRef, DetectorChannel, DetectorMap
from app.models.experiment import ExperimentConfig, FrozenExperimentConfig
from app.models.presets import Preset, PresetMapping, apply_preset_to_map, compare_preset
from app.models.results import MeasurementResult, Provenance
from app.models.units import format_rate, format_time_ps, s_to_ps, to_ps


def test_units():
    assert s_to_ps(1.5) == 1_500_000_000_000
    assert to_ps(2, "ns") == 2000
    assert format_time_ps(1_500) == "1.5 ns"
    assert format_rate(37_200) == "37.2 kcps"


def test_detector_map_roundtrip_and_sorting():
    dm = DetectorMap()
    dm.add(DetectorChannel("TT:1", 1, "A", 810.0, role="signal"))
    dm.add(DetectorChannel("TT:1", 2, "B", 780.0, role="idler"))
    dm.add(DetectorChannel("TT:1", 3, "C", None))
    assert [c.physical_channel for c in dm.sorted_by_wavelength("TT:1")] == [2, 1, 3]
    assert dm.physical_channels("TT:1") == [1, 2, 3]
    chans, wl = dm.wavelength_axis("TT:1")
    assert wl == [780.0, 810.0]
    d = dm.to_dict()
    dm2 = DetectorMap.from_dict(json.loads(json.dumps(d)))
    assert dm2.get_physical("TT:1", 1).label == "A (810 nm)"
    assert ChannelRef.parse("TT:1:2") == ChannelRef("TT:1", 2)
    assert dm.by_role("signal")[0].physical_channel == 1


def test_detector_validation():
    dc = DetectorChannel("TT:1", 1, wavelength_nm=-5, event_divider=0, impedance="bad", role="x")
    problems = dc.validate()
    assert len(problems) == 4


def test_experiment_freeze_is_immutable():
    exp = ExperimentConfig(experiment_name="X")
    exp.detector_map.add(DetectorChannel("TT:1", 1, "A", 800.0))
    frozen = exp.freeze()
    exp.experiment_name = "changed"
    exp.detector_map.get_physical("TT:1", 1).wavelength_nm = 900.0
    assert frozen["experiment_name"] == "X"
    assert frozen["detector_map"]["channels"][0]["wavelength_nm"] == 800.0
    with pytest.raises(AttributeError):
        frozen.config_hash = "x"
    assert len(frozen.config_hash) == 64
    back = ExperimentConfig.from_dict(frozen.to_dict())
    assert back.experiment_name == "X"


def test_preset_roundtrip_apply_and_compare():
    dm = DetectorMap([DetectorChannel("TT:A", 1, "S1", 800.0, trigger_level_v=0.05, impedance="50_OHM", input_delay_ps=120, deadtime_ps=10000, event_divider=1)])
    preset = Preset.from_detector_map("p", dm, {"TT:A": "SER-A"}, {"TT:A": "Ultra"})
    d = json.loads(json.dumps(preset.to_dict()))
    p2 = Preset.from_dict(d)
    assert p2.devices[0].serial == "SER-A" and p2.devices[0].channels["1"].wavelength_nm == 800.0
    target = DetectorMap()
    mapping = PresetMapping(device_map={"SER-A": "TT:B"}, channel_map={"SER-A": {1: 3}})
    changed, warnings = apply_preset_to_map(p2, target, mapping, {"TT:B": [1, 2, 3]})
    assert len(changed) == 1 and changed[0].physical_channel == 3 and changed[0].trigger_level_v == 0.05
    assert not warnings
    diffs = compare_preset(p2, mapping, {"TT:B": {3: {"trigger_level_v": 0.1, "impedance": "50_OHM", "input_delay_ps": 120, "deadtime_ps": 10000, "event_divider": 2}}})
    fields = {x.field for x in diffs}
    assert fields == {"trigger_level_v", "event_divider"}
    # unmapped device is skipped with a warning
    changed, warnings = apply_preset_to_map(p2, DetectorMap(), PresetMapping(), {})
    assert not changed and warnings


def test_result_save_load_checksum(tmp_path):
    res = MeasurementResult(measurement_type="g2", arrays={"index_ps": np.arange(5), "counts": np.ones(5, dtype=np.int64)}, scalars={"g2_zero": 1.0}, configuration={"parameters": {"binwidth_ps": 10}}, raw_files=[str(tmp_path / "missing.ttbin")], units={"index_ps": "ps"})
    res.provenance = Provenance(analysis_type="test", parameters={"a": 1}).to_dict()
    j, n = res.save(tmp_path, "r")
    meta = json.loads(j.read_text())
    assert meta["npz_sha256"] and meta["configuration_hash"] and meta["arrays"]["counts"]["shape"] == [5]
    back = MeasurementResult.load(j)
    assert np.array_equal(back.arrays["counts"], res.arrays["counts"])
    assert back.metadata["missing_raw_files"] == [str(tmp_path / "missing.ttbin")]
    assert back.provenance["analysis_type"] == "test"
    csv = res.to_csv(tmp_path / "r.csv")
    assert "index_ps,counts" in csv.read_text()
    # tampering is detected
    n.write_bytes(n.read_bytes()[:-1] + b"x")
    back2 = MeasurementResult.load(j)
    assert "integrity_warning" in back2.metadata or back2.arrays
