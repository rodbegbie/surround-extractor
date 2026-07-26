# sfa-extractor

Rips the 5.1 surround mix off a DVD-Video album release (the kind of
DVD-Audio-era disc that plays the whole album as one title with chapter
markers per song) and splits it into one file per track — AC3 always, and
DTS too when the disc has a 5.1 DTS track.

Auto-detects the "play the album" title and its surround audio track;
there's no per-disc configuration. Optionally tags each file with track,
artist, album, and date pulled from a MusicBrainz release.

## Requirements

- Python 3.13+ and [uv](https://docs.astral.sh/uv/)
- `HandBrakeCLI` and `ffmpeg`/`ffprobe` on `PATH`:

  ```bash
  brew install handbrake ffmpeg
  ```

`HandBrakeCLI` does the actual DVD navigation — it bundles its own
libdvdread/libdvdnav, which correctly handles chapter/cell boundaries in a
way plain ffmpeg concatenation cannot be relied on to (see `CLAUDE.md` for
why). ffmpeg is only used afterward to strip the throwaway video track.

## Usage

```bash
uv sync
```

Point it at a `.dvdmedia` bundle (or any folder containing `VIDEO_TS`):

```bash
uv run extract_ac3.py "/path/to/Some Album.dvdmedia" \
  --musicbrainz-release <MusicBrainz release ID>
```

By default this is a **dry run** — it prints the detected title, chapter
list, audio tracks, and planned filenames, but writes nothing. Add `--yes`
once the plan looks right:

```bash
uv run extract_ac3.py "/path/to/Some Album.dvdmedia" \
  --musicbrainz-release <MusicBrainz release ID> --yes
```

`--musicbrainz-release` is optional; without it, files are named
`track01.ac3`, `track02.ac3`, etc. and no ID3 tags are written. If the
MusicBrainz release's track count doesn't match the DVD's chapter count,
the script falls back to generic naming and skips tagging automatically.

### Other options

```text
--output-dir DIR   Where to write files (default: ./output/<dvd folder name>)
--title N          Force a specific DVD title instead of auto-detecting it
```

## Output

```text
output/
  Some Album/            <- AC3 files
  Some Album DTS/        <- DTS files, only created if the disc has a 5.1 DTS track
```

Each file is a raw elementary AC3/DTS stream (not a video container) with
ID3v2 tags prepended when MusicBrainz metadata was available.

## Development

```bash
uv run pytest -q       # unit tests — fixture-driven, no disc or external tools needed
uv run ruff check .
uv run ruff format .
```

See `CLAUDE.md` for the extraction pipeline architecture and the specific
gotchas (ffmpeg VOB-concat stream duplication, MusicBrainz `inc=` params,
`ffprobe` duration estimates on raw AC3/DTS) that shaped the design.
