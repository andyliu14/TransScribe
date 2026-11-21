"""Render existing MIDI to MP3 using TiMidity++ and FluidR3 GM.

Run with: uv run --with imageio-ffmpeg==0.6.0 python scripts/render_audio.py INPUT.mid
The extracted Ubuntu synth packages live in ~/.cache/music-arranger/audio.
Score/MIDI files are never modified. MP3 is a synthesized audition, not a
prediction of live instrument balance, bowing, tonguing or phrasing.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

from music_arranger.playback import audit_midi


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--title", help="Title embedded in the MP3 metadata")
    parser.add_argument(
        "--gain",
        type=int,
        default=15,
        help="TiMidity amplification percent; leave headroom for source fff dynamics",
    )
    parser.add_argument(
        "--synth-prefix", type=Path, default=Path.home() / ".cache/music-arranger/audio"
    )
    args = parser.parse_args()
    source = args.input.resolve()
    midi_audit = audit_midi(source)
    if not midi_audit["valid"]:
        raise ValueError(
            f"Refusing to render invalid MIDI note lifetimes: {midi_audit['errors'][:5]}"
        )
    output = (args.output or source.with_suffix(".mp3")).resolve()
    if source == output or output.suffix.lower() != ".mp3":
        raise ValueError("Choose a separate .mp3 output path")
    prefix = args.synth_prefix
    synth = prefix / "usr/bin/timidity"
    soundfont = prefix / "usr/share/sounds/sf2/FluidR3_GM.sf2"
    if not synth.is_file() or not soundfont.is_file():
        raise FileNotFoundError(
            f"Install/extract TiMidity++ and fluid-soundfont-gm under {prefix}"
        )
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        import imageio_ffmpeg

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = str(prefix / "usr/lib/x86_64-linux-gnu") + (
        ":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else ""
    )
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="arranger-audio-") as directory:
        directory = Path(directory)
        config = directory / "timidity.cfg"
        config.write_text(f'soundfont "{soundfont}"\n')
        wav = directory / "preview.wav"
        subprocess.run(
            [
                str(synth),
                "-c",
                str(config),
                "-Ow",
                "-s",
                "44100",
                "-A",
                str(args.gain),
                "-o",
                str(wav),
                str(source),
            ],
            env=env,
            check=True,
            capture_output=True,
        )
        with wave.open(str(wav)) as audio:
            duration = audio.getnframes() / audio.getframerate()
            if duration < 1:
                raise ValueError("Synthesizer produced an empty/short preview")
        subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-y",
                "-i",
                str(wav),
                "-codec:a",
                "libmp3lame",
                "-b:a",
                "192k",
                "-metadata",
                "title=" + (args.title or source.parent.name),
                str(output),
            ],
            check=True,
        )
    subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(output), "-f", "null", "-"], check=True
    )
    assert original_hash == hashlib.sha256(source.read_bytes()).hexdigest()
    report = {
        "midi": str(source),
        "midi_sha256": original_hash,
        "midi_note_lifetime_audit": midi_audit,
        "mp3": str(output),
        "mp3_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "duration_seconds": duration,
        "synthesizer": "TiMidity++ 2.14.0",
        "soundfont": "FluidR3 GM",
        "soundfont_sha256": hashlib.sha256(soundfont.read_bytes()).hexdigest(),
        "sample_rate": 44100,
        "bitrate": "192 kbps",
        "amplification_percent": args.gain,
        "decode_verified": True,
        "limitations": "General MIDI audition; does not reproduce live phrasing, extended techniques or acoustic balance.",
    }
    output.with_suffix(".audio.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
