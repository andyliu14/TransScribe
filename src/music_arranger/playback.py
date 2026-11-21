"""MIDI serialization and independent note-lifetime validation."""

import hashlib
import tempfile
from collections import defaultdict, deque
from pathlib import Path

from music21 import midi


def audit_midi(path):
    """Match positive-velocity note-ons to later offs in actual file order.

    Validate across tracks because MIDI channels, not tracks, own note state.
    Report track polyphony as an additional diagnostic, not a musical grade.
    """
    mf = midi.MidiFile()
    mf.open(path)
    try:
        mf.read()
    finally:
        mf.close()
    events = []
    summaries = []
    for track_index, track in enumerate(mf.tracks):
        tick = 0
        summaries.append(
            {
                "track": track_index,
                "note_ons": 0,
                "max_active_notes": 0,
                "longest_note_quarters": 0.0,
            }
        )
        for order, event in enumerate(track.events):
            if isinstance(event, midi.DeltaTime):
                tick += event.time
            else:
                events.append((tick, track_index, order, event))
    active = defaultdict(deque)
    counts = defaultdict(int)
    errors = []
    pedals = {}
    tempos = []
    for tick, track_index, _, event in sorted(events, key=lambda x: x[:3]):
        kind = event.type
        if kind == midi.MetaEvents.SET_TEMPO:
            microseconds = int.from_bytes(event.data, "big")
            if microseconds <= 0:
                errors.append({"type": "invalid_tempo", "tick": tick})
            elif not tempos or tempos[-1]["microseconds_per_quarter"] != microseconds:
                tempos.append(
                    {
                        "quarter_offset": tick / mf.ticksPerQuarterNote,
                        "microseconds_per_quarter": microseconds,
                        "quarter_bpm": 60_000_000 / microseconds,
                    }
                )
        if (
            kind == midi.ChannelVoiceMessages.CONTROLLER_CHANGE
            and event.parameter1 == 64
        ):
            pedals[event.channel] = event.parameter2 >= 64
        on = kind == midi.ChannelVoiceMessages.NOTE_ON and event.velocity > 0
        off = kind == midi.ChannelVoiceMessages.NOTE_OFF or (
            kind == midi.ChannelVoiceMessages.NOTE_ON and event.velocity == 0
        )
        if not (on or off):
            continue
        key = (event.channel, event.pitch)
        if on:
            active[key].append((tick, track_index))
            counts[track_index] += 1
            summary = summaries[track_index]
            summary["note_ons"] += 1
            summary["max_active_notes"] = max(
                summary["max_active_notes"], counts[track_index]
            )
        elif not active[key]:
            errors.append(
                {"type": "orphan_note_off", "tick": tick, "channel_pitch": key}
            )
        else:
            start, owner = active[key].popleft()
            counts[owner] -= 1
            if tick <= start:
                errors.append(
                    {
                        "type": "nonpositive_note_duration",
                        "tick": tick,
                        "channel_pitch": key,
                    }
                )
            summaries[owner]["longest_note_quarters"] = max(
                summaries[owner]["longest_note_quarters"],
                (tick - start) / mf.ticksPerQuarterNote,
            )
    for key, pending in active.items():
        for start, owner in pending:
            errors.append(
                {
                    "type": "unclosed_note",
                    "tick": start,
                    "track": owner,
                    "channel_pitch": key,
                }
            )
    for channel, held in pedals.items():
        if held:
            errors.append({"type": "sustain_left_on", "channel": channel})
    return {
        "valid": not errors,
        "errors": errors,
        "ticks_per_quarter": mf.ticksPerQuarterNote,
        "tracks": [t for t in summaries if t["note_ons"]],
        "tempo_events": tempos,
    }


def write_midi_preview(score, path, *, tempo_mode="encoded"):
    from .core import interpret_playback_tempos, prepare_midi_playback

    path = Path(path)
    playback, changes = prepare_midi_playback(score)
    tempo_interpretation = interpret_playback_tempos(playback, tempo_mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="arranger-midi-", dir=path.parent) as temp:
        candidate = Path(temp) / "preview.mid"
        playback.write("midi", fp=candidate)
        audit = audit_midi(candidate)
        if not audit["valid"]:
            raise ValueError(
                f"MIDI note-lifetime validation failed: {audit['errors'][:5]}"
            )
        candidate.replace(path)
    return {
        "policy": "On-beat grace groups borrow at most 1/4 quarter and half the anchor; "
        "each grace at most 1/8 quarter. Notation unchanged.",
        "grace_realizations": changes,
        "tempo_interpretation": tempo_interpretation,
        "audit": audit,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
