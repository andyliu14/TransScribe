# TransScribe

Turn an editable score into rehearsal parts for cello and double bass, or for alto saxophone, cello and double bass. TransScribe opens in your browser and keeps your scores on your computer. You can try the included duet without an account.

**See the result first:** [Open the example trio score (PDF)](https://github.com/andyliu14/TransScribe/releases/latest/download/Courtyard-Round.pdf) · [Download its parts, MIDI and MusicXML](https://github.com/andyliu14/TransScribe/releases/latest/download/Courtyard-Round.zip)

## Download and start

| Your computer | Download |
| --- | --- |
| Windows, Intel or AMD (64-bit) | [TransScribe for Windows](https://github.com/andyliu14/TransScribe/releases/latest/download/TransScribe-Windows-x64.zip) |
| Mac, Apple silicon (M-series), macOS 13+ | [TransScribe for Apple silicon](https://github.com/andyliu14/TransScribe/releases/latest/download/TransScribe-macOS-Apple-Silicon.zip) |
| Mac, Intel, macOS 13+ | [TransScribe for Intel Mac](https://github.com/andyliu14/TransScribe/releases/latest/download/TransScribe-macOS-Intel.zip) |
| Linux, glibc (64-bit) | [Intel / AMD](https://github.com/andyliu14/TransScribe/releases/latest/download/TransScribe-Linux-x64.zip) · [ARM64](https://github.com/andyliu14/TransScribe/releases/latest/download/TransScribe-Linux-arm64.zip) |

1. **Extract the whole ZIP** into a folder you can keep, then open **Start TransScribe.cmd** on Windows or **Start TransScribe.command** on macOS. On Linux, run `sh "Start TransScribe.command"` from the extracted folder.
2. Keep the launch window open. First launch downloads Python and the locked dependencies using the included uv runtime manager. When setup finishes, your browser opens automatically. The local address is also printed in the launch window.
3. Click **Create score and parts** to try *Courtyard Round*. Open **Download packet** for the conductor score, individual parts, MIDI and evaluation. Later, choose **Upload** for your own MusicXML or **PDMX catalog** to find a score.

PDF engraving uses system Cairo and Fontconfig. When those libraries are unavailable, the workbench creates MusicXML and MIDI. Open the MusicXML in your notation editor to print the score and parts. Keep the extracted folder to retain your packets under `runs/workbench/`.

## Run from source

For developers using a checkout or unpacked source release, with Python, Git and uv installed, run:

```bash
python3 scripts/launch_ui.py
```

The launcher restores the pinned vendor checkout, starts a loopback server, and opens your browser. After bootstrap, `uv run --locked transscribe` opens the workbench. Previous `arranger` and `arranger-mcp` commands remain available. Choose the example for a first run, search PDMX for an edition, or upload MusicXML/MXL. Select the target ensemble and policy, then build the packet. Completed packets remain in `runs/workbench/` across restarts. Each entry links to the ZIP, evaluation, and score PDF when engraving is enabled. **Copy review brief** prepares plain text you can paste into a free chat account. Copying the brief writes to your clipboard.

The UI, search, arranger and evaluator run independently of any AI account. Local Codex CLI access is currently included with ChatGPT Free subject to its usage limits. Anthropic lists Claude Code under paid plans. Claude Free users can paste a review brief into Claude chat. For a direct local tool connection, open **Connect Claude Desktop** in the workbench, download the machine-specific MCP settings and merge the server entry into the desktop developer configuration. Quit and reopen Claude Desktop to load it. Use absolute paths for files and output folders. Desktop extensions are available to all Claude Desktop users. [Claude connector availability](https://support.claude.com/en/articles/11176164-use-connectors-to-extend-claude-s-capabilities) · [Local MCP setup](https://py.sdk.modelcontextprotocol.io/get-started/real-host/). [Codex plans](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan) · [Claude availability](https://support.claude.com/en/articles/10065433-install-claude-desktop)

For a terminal-only machine use `--no-browser`. Change the listening port with `--port` and the output directory with `--workspace`. PDF rendering on Linux uses Cairo and Fontconfig. Uncheck engraving to build editable scores and MIDI without PDFs.

## Find and download a PDMX edition

```bash
uv run --locked transscribe pdmx-search "Passacaglia violin viola" --composer Halvorsen
uv run --locked transscribe ingest pdmx:mxl/2/32/QmcMF7zhLai37n5i1GK4uFimeeysZx7xV9K6conDJaX7Qr.mxl --output runs/halvorsen-source
uv run --locked transscribe packet runs/halvorsen-source --ensemble duo --output runs/halvorsen-duo
```

Search returns exact source IDs, instrument names derived from track programs, composer and edition license flags. Results follow catalog order. Use `--limit` and `--offset` to page. The MCP equivalents are `search_pdmx_scores` and `ingest_score_source`. Check the imported part names and measure counts before choosing an arrangement.

The first search caches the pinned 225 MB CSV. First acquisition caches the 1.9 GB MXL archive unless the selected edition already exists in an unpacked local tree. Set `PDMX_DIR` to a directory containing `PDMX.csv` and either `mxl.tar.gz` or the original `mxl/...` directory layout for offline use. The default cache is `~/.cache/music-arranger/pdmx/15571083` on Linux. Catalog and archive downloads are verified against Zenodo’s published checksums. Each source bundle preserves the original MXL, its SHA-256, catalog record ID, title and edition license metadata. Credits and catalog license flags require edition-specific review. [Pinned PDMX dataset](https://zenodo.org/records/15571083)

## Run a rehearsal packet

The source release includes an original four-bar violin and viola example. In the checkout or unpacked source release, run these commands from the project root with Python 3.12, Git and uv available. PDF output on Linux uses Cairo and Fontconfig (`libcairo2` and `libfontconfig1` on Ubuntu).

```bash
python3 scripts/bootstrap_vendor.py
uv sync --locked
uv run transscribe inspect examples/courtyard.musicxml
uv run transscribe packet examples/courtyard.musicxml --ensemble trio --output runs/courtyard
```

Open `runs/courtyard/rehearsal.zip`. The `sax-led/` folder holds the control and `relay-two/` holds the candidate. Each includes a conductor score, extracted parts, editable MusicXML, MIDI, a measure-level edit report and an independent evaluation. The root manifest records the source digest, instrument inspection, selected policies and SHA-256 of each payload file. Read eligibility alongside omitted pitches and breath/run findings. An ineligible candidate remains available for diagnosis. Use a new directory for each revision. Add `--no-render` for a notation-only review with MIDI.

For imported material, run `uv run transscribe ingest SOURCE --output sources/my-import` and pass that directory to `packet`. Acquisition reports whether the result is ready or needs transcription review. Choose `--ensemble duo` for cello and bass or `--ensemble trio` for alto sax, cello and bass. Use the [player guides](.claude/skills/transscribe/SKILL.md) for register, articulation, breath, bowing and hand-position review.

## Use with Codex or Claude Code

Start either client at the project root after bootstrap. Claude Code reads `.mcp.json`, imports the repository instructions through `CLAUDE.md` and discovers `/transscribe`. Codex reads the project MCP configuration from `.codex/config.toml` in a trusted checkout and discovers `$transscribe` under `.agents/skills`. Both the checkout and source release include these entries. Both clients use the same stdio tools and local filesystem. A first request to try is “Arrange examples/courtyard.musicxml for alto sax, cello and bass. Compare the two editions and give me the rehearsal packet.” Claude’s skill format and invocation are documented in the [Claude Code skill guide](https://code.claude.com/docs/en/skills).

## Extend and distribute

The [backend contracts](docs/backends.md) describe stage inputs, outputs and extension tests. Add a source provider in `acquisition/providers.py` to deliver original files and provenance. Add an arrangement policy in `core.py` and pair each new target with a control, default policy and independent evaluator in `workflow.ENSEMBLES`. Both CLI and MCP use that registry for packet generation. Engraving lives in `render.py`, MIDI validation in `playback.py`. Optional LibreScore conversion and Audiveris recognition have separate [setup instructions and format details](STUDIES.md#modular-source-ingestion).

Run `uv run --locked pytest -q` and `uv run --locked python scripts/smoke_mcp.py` for regression and stdio coverage. Build the **source release** with `uv build --sdist`. The allowlist includes the CLI, MCP servers’ setup, agent integrations, guides, tests, optional backend recipes and original example. Historical source scores, generated arrangements, installed third-party binaries and the vendor checkout stay outside the release. Bootstrap retrieves the pinned upstream package and its license. Third-party notices are under `tools/`. Review the rights of each source edition before distributing an arrangement.

The [arranging studies](STUDIES.md) document full-score experiments, backend setup, rule sources, and known limitations.

License selection for the original TransScribe code and guides is pending.
