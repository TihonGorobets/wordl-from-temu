"""
build_wordlist.py
=================
Downloads the latest SCOWL (Spell Checker Oriented Word Lists),
extracts every word file, and produces a clean list of 5-letter
English words saved as both .txt and .json.

Requirements: Python 3.7+  (no third-party packages needed)
Usage:        python build_wordlist.py
"""

import json
import os
import re
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request

# ── Configuration ──────────────────────────────────────────────────────────────

# GitHub API endpoint to resolve the latest SCOWL release automatically
GITHUB_API  = "https://api.github.com/repos/en-us/scowl/releases/latest"

# Hard-coded fallback tarball URL (used when the API is unreachable)
FALLBACK_URL = "https://github.com/en-us/scowl/archive/refs/heads/master.tar.gz"

# Output file names (written next to this script)
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
OUT_TXT      = os.path.join(SCRIPT_DIR, "five_letter_words.txt")
OUT_JSON     = os.path.join(SCRIPT_DIR, "five_letter_words.json")

# SCOWL size levels to include (10–70 = common English; 80–95 = rare/technical)
# Increase MAX_LEVEL to 95 if you want a larger but noisier word list.
MIN_LEVEL = 10
MAX_LEVEL = 70

# ── Step 1: Resolve the download URL ───────────────────────────────────────────

def get_download_url() -> str:
    """
    Ask the GitHub Releases API for the latest SCOWL tarball URL.
    Falls back to FALLBACK_URL if the request fails.
    """
    print("[1/5] Resolving latest SCOWL release …")
    try:
        req = urllib.request.Request(
            GITHUB_API,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "WordlistBuilder/1.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())

        # The release may ship the tarball as an asset, or we use tarball_url
        tarball_url: str = data.get("tarball_url", "")
        if tarball_url:
            print(f"    Latest release tag : {data.get('tag_name', 'unknown')}")
            print(f"    Tarball URL        : {tarball_url}")
            return tarball_url

    except (urllib.error.URLError, json.JSONDecodeError, KeyError) as exc:
        print(f"    ⚠  GitHub API unavailable ({exc}). Using fallback URL.")

    print(f"    Fallback URL: {FALLBACK_URL}")
    return FALLBACK_URL


# ── Step 2: Download the tarball ───────────────────────────────────────────────

def download_tarball(url: str, dest: str) -> None:
    """
    Stream-download the tarball at `url` into `dest`, printing progress.
    Raises SystemExit on failure so the user gets a clear error message.
    """
    print(f"[2/5] Downloading SCOWL …")
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "WordlistBuilder/1.0"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp, \
             open(dest, "wb") as out_file:

            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 64 * 1024  # 64 KB chunks

            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                out_file.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded / total * 100
                    print(f"\r    {downloaded:,} / {total:,} bytes  ({pct:.1f}%)",
                          end="", flush=True)
            print()  # newline after progress bar

    except urllib.error.URLError as exc:
        print(f"\n✖  Download failed: {exc}", file=sys.stderr)
        print("   Check your internet connection and try again.", file=sys.stderr)
        sys.exit(1)

    print(f"    Saved to: {dest}")


# ── Step 3: Extract word files ─────────────────────────────────────────────────

# SCOWL word-file names look like:
#   english-words.10, english-words.20, american-words.35, …
# The numeric suffix is the "size level" (10 = most common, 95 = most obscure).
WORD_FILE_PATTERN = re.compile(
    r"(english|american|british|canadian|australian|variant|."
    r"*-words?)\."
    r"(\d+)$",
    re.IGNORECASE,
)

def extract_words(tarball_path: str) -> list[str]:
    """
    Open the tarball, iterate every member whose name matches the SCOWL
    word-file pattern and whose size level falls within [MIN_LEVEL, MAX_LEVEL],
    and collect all lines as raw word candidates.
    """
    print(f"[3/5] Extracting word files (levels {MIN_LEVEL}–{MAX_LEVEL}) …")
    raw_words: list[str] = []
    files_read = 0

    try:
        with tarfile.open(tarball_path, "r:gz") as tar:
            for member in tar.getmembers():
                filename = os.path.basename(member.name)
                match = WORD_FILE_PATTERN.match(filename)
                if not match:
                    continue

                level = int(match.group(2))
                if not (MIN_LEVEL <= level <= MAX_LEVEL):
                    continue

                # Read the file member as text
                fobj = tar.extractfile(member)
                if fobj is None:
                    continue

                try:
                    text = fobj.read().decode("utf-8", errors="ignore")
                except Exception:
                    continue

                lines = text.splitlines()
                raw_words.extend(lines)
                files_read += 1

    except tarfile.TarError as exc:
        print(f"\n✖  Failed to read tarball: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"    {files_read} word files read → {len(raw_words):,} raw lines")
    return raw_words


# ── Step 4 & 5: Filter, clean, deduplicate, sort ──────────────────────────────

def process_words(raw_words: list[str]) -> list[str]:
    """
    From the raw lines, keep only tokens that are:
      • Exactly 5 characters long
      • Composed solely of ASCII letters (a–z / A–Z)
    Then lowercase, deduplicate, and sort alphabetically.
    """
    print("[4/5] Filtering, deduplicating, and sorting …")

    seen:  set[str]  = set()
    clean: list[str] = []

    for line in raw_words:
        # A single SCOWL line may contain one word (possibly with trailing
        # whitespace, a possessive marker, etc.).  Strip and take the bare token.
        word = line.strip()

        # Skip blank lines and comment lines
        if not word or word.startswith("#"):
            continue

        # Keep only purely alphabetic characters (rejects apostrophes, hyphens,
        # digits, accented letters, etc.)
        if not word.isalpha():
            continue

        # Enforce ASCII-only (no accented / unicode letters)
        try:
            word.encode("ascii")
        except UnicodeEncodeError:
            continue

        # Exactly 5 letters
        if len(word) != 5:
            continue

        word_lower = word.lower()

        # Deduplicate
        if word_lower in seen:
            continue
        seen.add(word_lower)
        clean.append(word_lower)

    # Sort alphabetically
    clean.sort()

    print(f"    {len(clean):,} unique 5-letter words retained")
    return clean


# ── Step 6: Save outputs ───────────────────────────────────────────────────────

def save_outputs(words: list[str]) -> None:
    """
    Write the final word list to:
      - five_letter_words.txt  (one word per line, UTF-8)
      - five_letter_words.json (JSON array, pretty-printed, UTF-8)
    """
    print("[5/5] Saving output files …")

    # Plain text — one word per line
    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(words) + "\n")
    print(f"    ✔  {OUT_TXT}  ({len(words):,} words)")

    # JSON array
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(words, f, indent=2, ensure_ascii=True)
        f.write("\n")
    print(f"    ✔  {OUT_JSON}  ({os.path.getsize(OUT_JSON):,} bytes)")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("  SCOWL → 5-Letter Word List Builder")
    print("=" * 60)

    # Work inside a temporary directory so we never leave partial downloads
    with tempfile.TemporaryDirectory(prefix="scowl_") as tmp_dir:
        tarball_path = os.path.join(tmp_dir, "scowl.tar.gz")

        # 1. Resolve URL
        url = get_download_url()

        # 2. Download
        download_tarball(url, tarball_path)

        # 3. Extract
        raw = extract_words(tarball_path)

    # 4 & 5. Filter + sort
    words = process_words(raw)

    if not words:
        print("\n✖  No words found. The SCOWL archive layout may have changed.")
        print("   Adjust WORD_FILE_PATTERN or MIN/MAX_LEVEL and retry.")
        sys.exit(1)

    # 6. Write files
    save_outputs(words)

    print()
    print("Done! Sample words:", ", ".join(words[:5]), "…", ", ".join(words[-5:]))
    print("=" * 60)


if __name__ == "__main__":
    main()
