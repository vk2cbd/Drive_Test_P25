from radio_survey.p25 import parse_tsbk
from radio_survey.p25 import P25_FRAME_SYNC_BITS, channelize_p25, make_constellation
from radio_survey.p25 import _sync_quality


def _block(opcode: int, fields: tuple[tuple[int, int, int], ...]) -> bytes:
    bits = ["0"] * 96
    header = f"{opcode & 0x3F:08b}" + "00000000"
    bits[:16] = header
    for start, length, value in fields:
        encoded = f"{value:0{length}b}"
        bits[start : start + length] = encoded
    return int("".join(bits), 2).to_bytes(12, "big")


def test_parse_network_status_broadcast() -> None:
    status = parse_tsbk(_block(0x3B, ((16, 20, 0xBEE00), (36, 12, 0x123))))

    assert status is not None
    assert status.wacn == "BEE00"
    assert status.system_id == "123"


def test_parse_rfss_status_broadcast() -> None:
    status = parse_tsbk(_block(0x3A, ((16, 12, 0x321), (28, 8, 4), (36, 8, 12))))

    assert status is not None
    assert status.system_id == "321"
    assert status.rfss_id == 4
    assert status.site_id == 12


def test_parse_adjacent_site_broadcast() -> None:
    status = parse_tsbk(_block(0x3C, ((16, 12, 0x321), (28, 8, 5), (36, 8, 14), (56, 4, 1), (60, 12, 77))))

    assert status is not None
    assert len(status.neighbours) == 1
    neighbour = status.neighbours[0]
    assert neighbour.system_id == "321"
    assert neighbour.rfss_id == 5
    assert neighbour.site_id == 14
    assert neighbour.channel_id == 1
    assert neighbour.channel_number == 77


def test_channelizer_estimates_frequency_offset() -> None:
    import numpy as np

    sample_rate_hz = 96_000.0
    offset_hz = 4_200.0
    t = np.arange(8192, dtype=np.float32) / sample_rate_hz
    samples = np.exp(1j * 2.0 * np.pi * offset_hz * t).astype(np.complex64)

    channel, channel_rate_hz, estimate_hz = channelize_p25(samples, sample_rate_hz)

    assert len(channel) > 100
    assert channel_rate_hz <= sample_rate_hz
    assert abs(estimate_hz - offset_hz) < 250.0


def test_constellation_reports_channelized_signal() -> None:
    import numpy as np

    t = np.arange(4096)
    samples = np.exp(1j * 0.05 * np.sin(t / 8)).astype(np.complex64)

    constellation = make_constellation(samples, 96_000.0)

    assert constellation.iq_points
    assert constellation.symbol_points
    assert constellation.channel_sample_rate_hz > 0.0


def test_sync_quality_reports_best_distance_and_near_hits() -> None:
    noisy_sync = P25_FRAME_SYNC_BITS[:10] + ("1" if P25_FRAME_SYNC_BITS[10] == "0" else "0") + P25_FRAME_SYNC_BITS[11:]
    best, near = _sync_quality("0" * 20 + noisy_sync + "1" * 20, P25_FRAME_SYNC_BITS, 3)

    assert best == 1
    assert near >= 1
