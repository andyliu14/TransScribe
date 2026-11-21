# Working on this arranger

Use `uv run` and maintain `uv.lock`. Restore the pinned upstream clone with `python3 scripts/bootstrap_vendor.py` before a fresh `uv sync`.

When the user raises a concern, make the fix generalizable: address the underlying policy or shared pipeline for all applicable inputs, not just the named score or generated artifact. Add regression coverage for the reported case and materially different inputs. Preserve a control, document remaining limitations, and regenerate affected examples to demonstrate the shared fix. Source-specific transcription recipes may remain as audited references, but must not substitute for a reusable ingestion capability.

For arranging tasks:
1. Inspect the input's actual instruments and measure count. Do not substitute the short piano Passacaglia for the full Halvorsen string duet.
2. Keep the input immutable and work at sounding pitch. Convert double bass to written pitch only at export, with octave transposition metadata preserved.
3. Generate a reference/control before more invasive changes. Retain a measure-level audit of removed chord tones, transpositions and register edits.
4. Critique each proposal. Compare ranges, difficult multiple stops, fast leaps, rhythm integrity and the rendered notation. Do not call a mechanically valid score performance-ready without musician review.
5. Preserve rhythms, repeats, ties and spanner endpoints. Do not use MIDI as an intermediate when the source contains notation.
6. Run appropriate unit/full-score tests and `scripts/smoke_mcp.py` when changing the tool interface. Inspect representative PDF pages after engraving changes.

`core.py` contains musical decisions; `render.py` contains engraving; `server.py` exposes the tools. No LLM credentials are needed for deterministic runs. Preserve source provenance and third-party licenses.
