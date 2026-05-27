from radio_survey.p25 import parse_tsbk


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
