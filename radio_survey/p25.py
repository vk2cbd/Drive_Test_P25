from __future__ import annotations

from dataclasses import dataclass


P25_FRAME_SYNC = int("5575F5FF77FF", 16)
P25_FRAME_SYNC_BITS = f"{P25_FRAME_SYNC:048b}"
P25_SYMBOL_RATE = 4800.0
P25_SYNC_MAX_BIT_ERRORS = 3


@dataclass(frozen=True)
class P25NeighborSite:
    system_id: str | None = None
    rfss_id: int | None = None
    site_id: int | None = None
    channel_id: int | None = None
    channel_number: int | None = None

    def display(self) -> str:
        parts: list[str] = []
        if self.system_id:
            parts.append(f"sys {self.system_id}")
        if self.rfss_id is not None and self.site_id is not None:
            parts.append(f"RFSS {self.rfss_id} site {self.site_id}")
        if self.channel_id is not None and self.channel_number is not None:
            parts.append(f"chan {self.channel_id}-{self.channel_number}")
        return ", ".join(parts) if parts else "unknown neighbour"


@dataclass(frozen=True)
class P25ControlStatus:
    wacn: str | None = None
    system_id: str | None = None
    rfss_id: int | None = None
    site_id: int | None = None
    neighbours: tuple[P25NeighborSite, ...] = ()
    frame_syncs: int = 0
    tsbks: int = 0
    frequency_offset_hz: float = 0.0
    channel_sample_rate_hz: float = 0.0
    best_sync_distance: int | None = None
    near_syncs: int = 0
    bit_buffer_length: int = 0
    message: str = "Waiting for P25 control channel"


@dataclass(frozen=True)
class P25Constellation:
    iq_points: tuple[tuple[float, float], ...] = ()
    symbol_points: tuple[tuple[float, float], ...] = ()
    samples_per_symbol: int = 0
    frequency_offset_hz: float = 0.0
    channel_sample_rate_hz: float = 0.0
    message: str = "No P25 constellation data"


class P25ControlChannelDecoder:
    """Experimental P25 Phase 1 control-channel front end.

    This provides the in-app C4FM discriminator, frame-sync search, and a
    TSBK parser for common system and adjacent-site broadcasts. Full P25 TSBK
    recovery still depends on the trellis/deinterleave stage being completed,
    so the GUI reports lock state separately from decoded system fields.
    """

    def __init__(self) -> None:
        self._status = P25ControlStatus()
        self._bit_buffer = ""
        self._frequency_offset_hz = 0.0

    @property
    def status(self) -> P25ControlStatus:
        return self._status

    def update(self, samples: object, sample_rate_hz: float, auto_frequency_correction: bool = True) -> P25ControlStatus:
        try:
            channel, channel_rate_hz, offset_hz = channelize_p25(
                samples,
                sample_rate_hz,
                self._frequency_offset_hz if auto_frequency_correction else 0.0,
                auto_frequency_correction=auto_frequency_correction,
            )
            self._frequency_offset_hz = offset_hz
            bits = self._demodulate_bits(channel, channel_rate_hz)
        except Exception as exc:
            self._status = P25ControlStatus(message=f"P25 demod error: {exc}")
            return self._status

        self._bit_buffer = (self._bit_buffer + bits)[-24000:]
        best_distance, near_syncs = _sync_quality(self._bit_buffer, P25_FRAME_SYNC_BITS, max(P25_SYNC_MAX_BIT_ERRORS * 2, 8))
        sync_offsets = _find_sync_offsets(self._bit_buffer, P25_FRAME_SYNC_BITS, P25_SYNC_MAX_BIT_ERRORS)
        if not sync_offsets:
            self._status = P25ControlStatus(
                frequency_offset_hz=self._frequency_offset_hz,
                channel_sample_rate_hz=channel_rate_hz,
                best_sync_distance=best_distance,
                near_syncs=near_syncs,
                bit_buffer_length=len(self._bit_buffer),
                message="No P25 frame sync",
            )
            return self._status

        parsed = self._status
        tsbk_count = 0
        for offset in sync_offsets[-8:]:
            raw_payload = _remove_status_symbols(self._bit_buffer[offset + 48 + 64 : offset + 48 + 64 + 220])
            for block in _candidate_tsbks(raw_payload):
                message = parse_tsbk(block)
                if message is None:
                    continue
                tsbk_count += 1
                parsed = _merge_status(parsed, message)

        message = "P25 sync, waiting for decodable TSBK"
        if tsbk_count:
            message = f"P25 control channel decoded ({tsbk_count} TSBK)"
        self._status = P25ControlStatus(
            wacn=parsed.wacn,
            system_id=parsed.system_id,
            rfss_id=parsed.rfss_id,
            site_id=parsed.site_id,
            neighbours=parsed.neighbours,
            frame_syncs=len(sync_offsets),
            tsbks=tsbk_count,
            frequency_offset_hz=self._frequency_offset_hz,
            channel_sample_rate_hz=channel_rate_hz,
            best_sync_distance=best_distance,
            near_syncs=near_syncs,
            bit_buffer_length=len(self._bit_buffer),
            message=message,
        )
        return self._status

    def _demodulate_bits(self, samples: object, sample_rate_hz: float) -> str:
        import numpy as np

        iq = np.asarray(samples, dtype=np.complex64)
        if iq.size < 128:
            return ""
        iq = iq - np.mean(iq)
        discriminator = np.angle(iq[1:] * np.conj(iq[:-1]))
        discriminator = discriminator - np.mean(discriminator)
        samples_per_symbol = max(1, int(round(float(sample_rate_hz) / P25_SYMBOL_RATE)))
        best_bits = ""
        best_score = -1
        for phase in range(min(samples_per_symbol, 48)):
            symbols = discriminator[phase::samples_per_symbol]
            if symbols.size < 32:
                continue
            scale = float(np.percentile(np.abs(symbols), 90)) or 1.0
            normalized = symbols / scale
            dibits = [_slice_c4fm_symbol(float(value)) for value in normalized]
            for mapping in _DIBIT_MAPPINGS:
                bits = "".join(mapping[dibit] for dibit in dibits)
                score = bits.count(P25_FRAME_SYNC_BITS)
                if score > best_score:
                    best_score = score
                    best_bits = bits
        return best_bits


def channelize_p25(
    samples: object,
    sample_rate_hz: float,
    previous_offset_hz: float = 0.0,
    auto_frequency_correction: bool = True,
    search_hz: float = 25_000.0,
    channel_hz: float = 12_500.0,
    target_rate_hz: float = 48_000.0,
) -> tuple[object, float, float]:
    import numpy as np

    iq = np.asarray(samples, dtype=np.complex64)
    if iq.size < 128:
        return iq, float(sample_rate_hz), previous_offset_hz

    iq = iq - np.mean(iq)
    offset_hz = previous_offset_hz
    if auto_frequency_correction:
        estimate_hz = _estimate_frequency_offset(iq, sample_rate_hz, search_hz)
        offset_hz = previous_offset_hz * 0.85 + estimate_hz * 0.15 if abs(previous_offset_hz) > 1e-9 else estimate_hz

    time_index = np.arange(iq.size, dtype=np.float32)
    shifted = iq * np.exp(-1j * 2.0 * np.pi * float(offset_hz) * time_index / float(sample_rate_hz))
    spectrum = np.fft.fftshift(np.fft.fft(shifted))
    offsets = np.fft.fftshift(np.fft.fftfreq(shifted.size, d=1.0 / float(sample_rate_hz)))
    mask = np.abs(offsets) <= channel_hz / 2.0
    if mask.any():
        spectrum = spectrum * mask
        shifted = np.fft.ifft(np.fft.ifftshift(spectrum)).astype(np.complex64)

    decimation = max(1, int(float(sample_rate_hz) // target_rate_hz))
    if decimation > 1:
        shifted = shifted[::decimation]
    return shifted.astype(np.complex64), float(sample_rate_hz) / float(decimation), float(offset_hz)


def make_constellation(
    samples: object,
    sample_rate_hz: float,
    max_points: int = 420,
    frequency_offset_hz: float = 0.0,
) -> P25Constellation:
    import numpy as np

    iq, channel_rate_hz, offset_hz = channelize_p25(
        samples,
        sample_rate_hz,
        previous_offset_hz=frequency_offset_hz,
        auto_frequency_correction=False,
    )
    iq = np.asarray(iq, dtype=np.complex64)
    if iq.size < 128:
        return P25Constellation(message="Not enough IQ samples")

    iq = iq - np.mean(iq)
    rms = float(np.sqrt(np.mean(np.abs(iq) ** 2))) or 1.0
    iq = iq / rms
    iq_step = max(1, iq.size // max_points)
    iq_points = tuple((float(value.real), float(value.imag)) for value in iq[::iq_step][:max_points])

    discriminator = np.angle(iq[1:] * np.conj(iq[:-1]))
    discriminator = discriminator - np.mean(discriminator)
    samples_per_symbol = max(1, int(round(float(channel_rate_hz) / P25_SYMBOL_RATE)))
    phase = _best_symbol_phase(discriminator, samples_per_symbol)
    symbols = discriminator[phase::samples_per_symbol]
    scale = float(np.percentile(np.abs(symbols), 90)) if symbols.size else 1.0
    if abs(scale) < 1e-9:
        scale = 1.0
    symbols = np.clip(symbols / scale, -1.5, 1.5)
    symbol_step = max(1, symbols.size // max_points)
    selected = symbols[::symbol_step][:max_points]
    denominator = max(len(selected) - 1, 1)
    symbol_points = tuple((index / denominator, float(value)) for index, value in enumerate(selected))
    return P25Constellation(
        iq_points=iq_points,
        symbol_points=symbol_points,
        samples_per_symbol=samples_per_symbol,
        frequency_offset_hz=offset_hz,
        channel_sample_rate_hz=channel_rate_hz,
        message=f"{len(iq_points)} IQ pts, {len(symbol_points)} C4FM symbols, AFC {offset_hz:+.0f} Hz",
    )


def _estimate_frequency_offset(iq: object, sample_rate_hz: float, search_hz: float) -> float:
    import numpy as np

    fft_size = min(int(len(iq)), 16384)
    if fft_size < 128:
        return 0.0
    block = np.asarray(iq[-fft_size:], dtype=np.complex64)
    window = np.hanning(block.size).astype(np.float32)
    spectrum = np.fft.fftshift(np.fft.fft(block * window))
    offsets = np.fft.fftshift(np.fft.fftfreq(block.size, d=1.0 / float(sample_rate_hz)))
    mask = np.abs(offsets) <= float(search_hz)
    if not mask.any():
        return 0.0
    search_offsets = offsets[mask]
    search_power = np.abs(spectrum[mask]) ** 2
    if search_power.size == 0:
        return 0.0
    peak = int(np.argmax(search_power))
    left = max(0, peak - 2)
    right = min(search_power.size, peak + 3)
    weights = search_power[left:right]
    if float(np.sum(weights)) <= 0.0:
        return float(search_offsets[peak])
    return float(np.sum(search_offsets[left:right] * weights) / np.sum(weights))


def parse_tsbk(block: bytes) -> P25ControlStatus | None:
    if len(block) < 10:
        return None
    opcode = block[0] & 0x3F
    mfid = block[1]
    if mfid not in (0x00, 0x90):
        return None

    bits = "".join(f"{value:08b}" for value in block[:10])
    if opcode == 0x3B:
        wacn = _read_bits(bits, 16, 20)
        system = _read_bits(bits, 36, 12)
        return P25ControlStatus(wacn=f"{wacn:05X}", system_id=f"{system:03X}", tsbks=1, message="Network status broadcast")
    if opcode == 0x3A:
        system = _read_bits(bits, 16, 12)
        rfss = _read_bits(bits, 28, 8)
        site = _read_bits(bits, 36, 8)
        return P25ControlStatus(system_id=f"{system:03X}", rfss_id=rfss, site_id=site, tsbks=1, message="RFSS status broadcast")
    if opcode == 0x3C:
        system = _read_bits(bits, 16, 12)
        rfss = _read_bits(bits, 28, 8)
        site = _read_bits(bits, 36, 8)
        channel_id = _read_bits(bits, 56, 4)
        channel_number = _read_bits(bits, 60, 12)
        neighbour = P25NeighborSite(f"{system:03X}", rfss, site, channel_id, channel_number)
        return P25ControlStatus(neighbours=(neighbour,), tsbks=1, message="Adjacent site broadcast")
    return None


def _slice_c4fm_symbol(value: float) -> int:
    if value <= -0.5:
        return 0
    if value <= 0.0:
        return 1
    if value <= 0.5:
        return 2
    return 3


def _best_symbol_phase(discriminator: object, samples_per_symbol: int) -> int:
    best_phase = 0
    best_spread = -1.0
    for phase in range(min(samples_per_symbol, 48)):
        symbols = discriminator[phase::samples_per_symbol]
        if len(symbols) < 16:
            continue
        spread = float(abs(max(symbols) - min(symbols)))
        if spread > best_spread:
            best_spread = spread
            best_phase = phase
    return best_phase


_DIBIT_MAPPINGS = (
    ("00", "01", "10", "11"),
    ("01", "00", "11", "10"),
    ("11", "10", "01", "00"),
    ("10", "11", "00", "01"),
)


def _find_sync_offsets(value: str, pattern: str, max_errors: int) -> tuple[int, ...]:
    exact = _find_all(value, pattern)
    if exact:
        return exact
    pattern_length = len(pattern)
    offsets: list[int] = []
    for start in range(0, max(len(value) - pattern_length + 1, 0)):
        if _hamming_distance(value[start : start + pattern_length], pattern) <= max_errors:
            offsets.append(start)
    return tuple(offsets)


def _sync_quality(value: str, pattern: str, near_threshold: int) -> tuple[int | None, int]:
    pattern_length = len(pattern)
    if len(value) < pattern_length:
        return None, 0
    best: int | None = None
    near = 0
    for start in range(0, len(value) - pattern_length + 1):
        distance = _hamming_distance(value[start : start + pattern_length], pattern)
        if best is None or distance < best:
            best = distance
        if distance <= near_threshold:
            near += 1
    return best, near


def _find_all(value: str, pattern: str) -> tuple[int, ...]:
    offsets: list[int] = []
    start = value.find(pattern)
    while start >= 0:
        offsets.append(start)
        start = value.find(pattern, start + 1)
    return tuple(offsets)


def _hamming_distance(left: str, right: str) -> int:
    return sum(1 for a, b in zip(left, right) if a != b) + abs(len(left) - len(right))


def _remove_status_symbols(bits: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(bits):
        output.append(bits[index : index + 70])
        index += 72
    return "".join(output)


def _candidate_tsbks(bits: str) -> tuple[bytes, ...]:
    blocks: list[bytes] = []
    for start in range(0, min(40, max(len(bits) - 96, 0)), 2):
        chunk = bits[start : start + 96]
        if len(chunk) == 96:
            blocks.append(int(chunk, 2).to_bytes(12, "big"))
    return tuple(blocks)


def _merge_status(current: P25ControlStatus, update: P25ControlStatus) -> P25ControlStatus:
    neighbours = {neighbour.display(): neighbour for neighbour in current.neighbours}
    for neighbour in update.neighbours:
        neighbours[neighbour.display()] = neighbour
    return P25ControlStatus(
        wacn=update.wacn or current.wacn,
        system_id=update.system_id or current.system_id,
        rfss_id=update.rfss_id if update.rfss_id is not None else current.rfss_id,
        site_id=update.site_id if update.site_id is not None else current.site_id,
        neighbours=tuple(neighbours.values()),
        frame_syncs=current.frame_syncs + update.frame_syncs,
        tsbks=current.tsbks + update.tsbks,
        message=update.message or current.message,
    )


def _read_bits(bits: str, start: int, length: int) -> int:
    return int(bits[start : start + length], 2)
