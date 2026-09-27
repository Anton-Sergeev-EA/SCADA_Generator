import math

import pytest

from scada_core.config.loader import TagConfig
from scada_core.engine.codec import decode, encode, plan_blocks


@pytest.mark.parametrize(
    ("dtype", "value"),
    [("uint16", 65535), ("int16", -1234), ("uint32", 4_000_000_000), ("int32", -2_000_000_000)],
)
def test_integer_roundtrip(dtype: str, value: int) -> None:
    tag = TagConfig(name="x", address=0, type=dtype)
    assert decode(tag, encode(tag, value)) == value


def test_float32_and_scaling() -> None:
    tag = TagConfig(name="x", address=0, type="float32", scale=2.0, offset=1.0)
    assert math.isclose(decode(tag, encode(tag, 12.5)), 12.5, rel_tol=1e-6)


def test_scale_offset_uint16() -> None:
    tag = TagConfig(name="t", address=0, scale=0.1, offset=-50)
    assert decode(tag, [1234]) == pytest.approx(73.4)
    assert encode(tag, 73.4) == [1234]


def test_int16_negative_register() -> None:
    tag = TagConfig(name="t", address=0, type="int16", scale=0.1)
    assert decode(tag, [0xFFF6]) == pytest.approx(-1.0)


def test_out_of_range_rejected() -> None:
    with pytest.raises(ValueError):
        encode(TagConfig(name="t", address=0), 70000)
    with pytest.raises(ValueError):
        encode(TagConfig(name="t", address=0), float("nan"))


def test_bits() -> None:
    tag = TagConfig(name="c", address=0, function="coil")
    assert decode(tag, [True]) == 1.0
    assert encode(tag, 0) == [0]


def test_block_planning_merges_neighbours() -> None:
    tags = [
        TagConfig(name="a", address=0),
        TagConfig(name="b", address=1),
        TagConfig(name="c", address=7, type="float32"),
        TagConfig(name="far", address=500),
        TagConfig(name="coil", address=0, function="coil"),
    ]
    blocks = plan_blocks(tags)
    regs = [b for b in blocks if b["function"] == "holding_register"]
    assert [(b["start"], b["end"]) for b in regs] == [(0, 9), (500, 501)]
    assert sum(len(b["tags"]) for b in blocks) == len(tags)
