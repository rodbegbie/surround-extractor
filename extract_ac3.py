#!/usr/bin/env python3
"""Extract the 5.1 surround AC3 mix from a DVD-Video album release.

Uses HandBrakeCLI (which bundles its own DVD chapter/cell navigation) to find
the main "play the album" title and cut it into one AC3 file per chapter.
Track/artist/album metadata is optionally sourced from a MusicBrainz release
and written as ID3v2 tags on each output file.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from mutagen.id3 import ID3, TALB, TDRC, TIT2, TPE1, TRCK, ID3NoHeaderError

MUSICBRAINZ_API = "https://musicbrainz.org/ws/2/release/{mbid}?inc=recordings+artist-credits&fmt=json"
USER_AGENT = "sfa-extractor/0.1 (personal use script)"

REQUIRED_TOOLS = ("HandBrakeCLI", "ffmpeg", "ffprobe")


class ExtractionError(RuntimeError):
    pass


@dataclass
class Chapter:
    number: int
    length_seconds: float


@dataclass
class AudioTrack:
    index: int
    codec: str
    channels: int


@dataclass
class Title:
    number: int
    length_seconds: float
    chapters: list[Chapter] = field(default_factory=list)
    audio_tracks: list[AudioTrack] = field(default_factory=list)

    def surround_ac3_track(self) -> AudioTrack | None:
        for track in self.audio_tracks:
            if track.codec == "AC3" and track.channels == 6:
                return track
        return None


def check_tools() -> None:
    missing = [tool for tool in REQUIRED_TOOLS if shutil.which(tool) is None]
    if missing:
        raise ExtractionError(
            f"Required tool(s) not found on PATH: {', '.join(missing)}. "
            "Install with: brew install handbrake ffmpeg"
        )


def run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise ExtractionError(f"Command failed ({' '.join(cmd)}):\n{result.stderr[-2000:]}")
    return result.stdout + result.stderr


def find_video_ts(dvd_path: Path) -> Path:
    if dvd_path.name.upper() == "VIDEO_TS":
        return dvd_path
    candidate = dvd_path / "VIDEO_TS"
    if candidate.is_dir():
        return candidate
    raise ExtractionError(f"No VIDEO_TS folder found under {dvd_path}")


def parse_hms(text: str) -> float:
    hours, minutes, seconds = text.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds)


TITLE_RE = re.compile(r"^\+ title (\d+):$")
DURATION_RE = re.compile(r"^\s*\+ duration: (\d+:\d+:\d+)$")
CHAPTER_RE = re.compile(r"^\s*\+ (\d+): duration (\d+:\d+:\d+)$")
AUDIO_RE = re.compile(r"^\s*\+ (\d+), \S+ \((\w+), (\d+)\.(\d+) ch")


def parse_scan(scan_output: str) -> list[Title]:
    """Parse HandBrakeCLI's `+ title N:` human-readable scan summary.

    Chapter and audio-track lines have distinct, specific shapes ("N: duration
    H:M:S" vs "N, Lang (CODEC, C.C ch, ...)"), so no per-line section-tracking
    is needed beyond attributing each line to the current title block.
    """
    titles: list[Title] = []
    current: Title | None = None

    for line in scan_output.splitlines():
        if match := TITLE_RE.match(line):
            if current is not None:
                titles.append(current)
            current = Title(int(match.group(1)), 0.0)
            continue
        if current is None:
            continue
        if match := DURATION_RE.match(line):
            current.length_seconds = parse_hms(match.group(1))
        elif match := CHAPTER_RE.match(line):
            current.chapters.append(Chapter(int(match.group(1)), parse_hms(match.group(2))))
        elif match := AUDIO_RE.match(line):
            channels = int(match.group(3)) + int(match.group(4))
            current.audio_tracks.append(AudioTrack(int(match.group(1)), match.group(2), channels))

    if current is not None:
        titles.append(current)
    return titles


def scan_disc(dvd_path: Path) -> list[Title]:
    output = run(["HandBrakeCLI", "-i", str(dvd_path), "--scan", "-t", "0"])
    titles = parse_scan(output)
    if not titles:
        raise ExtractionError("HandBrakeCLI scan returned no titles")
    return titles


def pick_album_title(titles: list[Title]) -> Title:
    candidates = [t for t in titles if len(t.chapters) > 1 and t.surround_ac3_track() is not None]
    if not candidates:
        raise ExtractionError("No multi-chapter title with a 5.1 AC3 track found")
    return max(candidates, key=lambda t: t.length_seconds)


@dataclass
class AlbumMetadata:
    title: str
    artist: str
    date: str | None
    track_titles: list[str]


def _format_artist_credit(artist_credit: list[dict]) -> str:
    return "".join(credit["name"] + credit.get("joinphrase", "") for credit in artist_credit)


def fetch_musicbrainz_release(mbid: str) -> AlbumMetadata:
    url = MUSICBRAINZ_API.format(mbid=mbid)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=15) as response:
        data = json.load(response)
    medium = data["media"][0]
    artist = _format_artist_credit(data.get("artist-credit", [])) or "Unknown Artist"
    return AlbumMetadata(
        title=data["title"],
        artist=artist,
        date=data.get("date"),
        track_titles=[track["title"] for track in medium["tracks"]],
    )


def sanitize_filename(name: str) -> str:
    return re.sub(r'[/:\\?*"<>|]', "-", name).strip()


def track_filenames(count: int, track_titles: list[str] | None) -> list[str]:
    if track_titles is not None and len(track_titles) == count:
        return [
            f"{i:02d} - {sanitize_filename(title)}.ac3"
            for i, title in enumerate(track_titles, start=1)
        ]
    return [f"track{i:02d}.ac3" for i in range(1, count + 1)]


def extract_chapter(
    dvd_path: Path,
    title: Title,
    audio_track: AudioTrack,
    chapter_number: int,
    out_path: Path,
    tmp_path: Path,
) -> None:
    run(
        [
            "HandBrakeCLI",
            "-i",
            str(dvd_path),
            "-t",
            str(title.number),
            "--chapters",
            f"{chapter_number}-{chapter_number}",
            "-a",
            str(audio_track.index),
            "-E",
            "copy:ac3",
            "-e",
            "x264",
            "--encoder-preset",
            "ultrafast",
            "-q",
            "51",
            "-o",
            str(tmp_path),
        ]
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(tmp_path),
            "-vn",
            "-map",
            "0:a:0",
            "-c:a",
            "copy",
            "-f",
            "ac3",
            str(out_path),
        ]
    )
    tmp_path.unlink(missing_ok=True)


def write_id3_tags(
    path: Path,
    title: str,
    artist: str,
    album: str,
    track_number: int,
    track_total: int,
    date: str | None,
) -> None:
    """Prepend an ID3v2 tag to a raw AC3 file.

    Raw AC3 has no native metadata container (ffmpeg's `ac3` muxer silently
    drops `-metadata`), but ID3v2 tags are just a header block prepended to
    the file, so they attach fine to any file type -- this is the same trick
    id3v2-aware players already use for tagging bare AC3/DTS rips.
    """
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    tags.setall("TIT2", [TIT2(encoding=3, text=title)])
    tags.setall("TPE1", [TPE1(encoding=3, text=artist)])
    tags.setall("TALB", [TALB(encoding=3, text=album)])
    tags.setall("TRCK", [TRCK(encoding=3, text=f"{track_number}/{track_total}")])
    if date:
        tags.setall("TDRC", [TDRC(encoding=3, text=date)])
    tags.save(path, v2_version=3)


def extract_album(
    dvd_path: Path,
    output_dir: Path,
    musicbrainz_release: str | None,
    title_override: int | None,
    confirm: bool,
) -> None:
    check_tools()
    find_video_ts(dvd_path)  # validates the path early

    titles = scan_disc(dvd_path)
    if title_override is not None:
        matches = [t for t in titles if t.number == title_override]
        if not matches:
            raise ExtractionError(f"Title {title_override} not found on disc")
        album_title = matches[0]
    else:
        album_title = pick_album_title(titles)

    audio_track = album_title.surround_ac3_track()
    if audio_track is None:
        raise ExtractionError(f"Title {album_title.number} has no 5.1 AC3 track")

    album_metadata = fetch_musicbrainz_release(musicbrainz_release) if musicbrainz_release else None
    if album_metadata is not None and len(album_metadata.track_titles) != len(album_title.chapters):
        print(
            f"Warning: MusicBrainz release has {len(album_metadata.track_titles)} tracks but "
            f"the DVD title has {len(album_title.chapters)} chapters. Falling back to generic "
            "naming, no ID3 tags will be written.",
            file=sys.stderr,
        )
        album_metadata = None

    track_titles = album_metadata.track_titles if album_metadata is not None else None
    filenames = track_filenames(len(album_title.chapters), track_titles)
    track_total = len(filenames)

    print(
        f"Title {album_title.number}: {album_title.length_seconds:.0f}s, "
        f"{len(album_title.chapters)} chapters"
    )
    print(f"Audio track {audio_track.index}: {audio_track.codec} {audio_track.channels}ch")
    print(f"ID3 tags: {'yes, from MusicBrainz' if album_metadata is not None else 'no'}")
    for chapter, filename in zip(album_title.chapters, filenames):
        print(f"  {chapter.number:02d}  {chapter.length_seconds:7.1f}s  -> {filename}")

    if not confirm:
        print("\nDry run only (pass --yes to actually extract). No files written.")
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = output_dir / ".chapter_tmp.mkv"
    for i, (chapter, filename) in enumerate(zip(album_title.chapters, filenames), start=1):
        out_path = output_dir / filename
        extract_chapter(dvd_path, album_title, audio_track, chapter.number, out_path, tmp_path)
        if album_metadata is not None:
            write_id3_tags(
                out_path,
                title=album_metadata.track_titles[i - 1],
                artist=album_metadata.artist,
                album=album_metadata.title,
                track_number=i,
                track_total=track_total,
                date=album_metadata.date,
            )
        print(f"Wrote {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dvd_path", type=Path, help="Path to a .dvdmedia bundle or VIDEO_TS folder")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Where to write .ac3 files (default: ./output/<dvd folder name>)",
    )
    parser.add_argument(
        "--musicbrainz-release",
        metavar="MBID",
        help="MusicBrainz release ID to source track names from",
    )
    parser.add_argument(
        "--title",
        type=int,
        default=None,
        help="Force a specific DVD title number instead of auto-detecting the album title",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually write files (default is a dry run that just prints the plan)",
    )
    args = parser.parse_args()

    output_dir = args.output_dir or Path("output") / args.dvd_path.stem
    try:
        extract_album(args.dvd_path, output_dir, args.musicbrainz_release, args.title, args.yes)
    except ExtractionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
