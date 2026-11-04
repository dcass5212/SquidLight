import numpy as np

from rgbapp.engine.backend import DeviceInfo, FakeBackend, ZoneInfo
from rgbapp.engine.devices import build_groups


def test_fake_backend_mirrors_the_real_machine():
    devs = {d.id: d for d in FakeBackend().devices()}
    assert devs["fake-keyboard"].led_count == 115
    assert devs["fake-dram-0"].led_count == devs["fake-dram-1"].led_count == 10
    board = devs["fake-board"]
    assert len(board.zones) == 3 and board.zones[1].size > 0


def test_groups_merge_ram_and_keep_an_empty_board_with_resizable_zones():
    groups = {g.key: g for g in build_groups(FakeBackend(board_zone_sizes=(0, 0, 0)).devices())}
    assert set(groups) == {"keyboard", "memory", "mainboard"}  # empty board: its page sets the counts
    assert groups["mainboard"].led_count == 0
    assert groups["memory"].led_count == 20
    assert [m.offset for m in groups["memory"].members] == [0, 10]

    with_board = {g.key for g in build_groups(FakeBackend().devices())}
    assert with_board == {"keyboard", "memory", "mainboard"}


def test_group_split_covers_the_frame_exactly():
    g = {g.key: g for g in build_groups(FakeBackend().devices())}["memory"]
    frame = np.arange(20 * 3, dtype=np.uint8).reshape(20, 3)
    parts = list(g.split(frame))
    assert [p[0] for p in parts] == ["fake-dram-0", "fake-dram-1"]
    np.testing.assert_array_equal(np.concatenate([p[1] for p in parts]), frame)


def test_positions_are_normalized_and_cover_every_led():
    for g in build_groups(FakeBackend().devices()):
        p = g.positions
        assert p.shape == (g.led_count, 2)
        assert p.min() >= 0.0 and p.max() <= 1.0
        assert np.isfinite(p).all()


def test_keyboard_matrix_positions_follow_the_matrix():
    # 114 LEDs: generic names, so no layout file fits and the matrix map is used
    kb = {g.key: g for g in build_groups(FakeBackend(keyboard_leds=114).devices())}["keyboard"]
    assert kb.layout is None
    # LED 4 sits at row 0 col 4 of the fake 7x21 matrix, LED 25 at row 1 col 4
    assert kb.positions[4, 0] == kb.positions[25, 0]
    assert kb.positions[25, 1] > kb.positions[4, 1]
    # LEDs 0-3 are not in the matrix and must not be dropped or sit on top of each other
    assert len({tuple(p) for p in kb.positions[:4]}) == 4


def test_two_ram_sticks_are_stacked_not_overlapping():
    mem = {g.key: g for g in build_groups(FakeBackend().devices())}["memory"]
    assert not np.allclose(mem.positions[:10], mem.positions[10:])


def test_unwritable_devices_are_skipped():
    dev = DeviceInfo("x", "No Direct", "fan", ("a", "b"), (ZoneInfo("z", 0, 2),), writable=False)
    assert build_groups([dev]) == []


def test_repeated_non_merged_types_get_unique_keys():
    mk = lambda i: DeviceInfo(f"k{i}", "KB", "keyboard", ("a",), (ZoneInfo("z", 0, 1),))
    assert [g.key for g in build_groups([mk(1), mk(2)])] == ["keyboard", "keyboard2"]
