"""File parsing and code chunking (architecture.md Phase 2).

Splits a text file's content into overlapping line-window chunks ahead
of embedding generation (Day 14). Chunking is language-agnostic for now;
AST/syntax-aware chunking can replace this later without touching the
`code_chunks` schema.
"""

from __future__ import annotations

# Extensions that are never worth attempting to decode as text.
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg", ".tiff",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".zip", ".tar", ".gz", ".tgz", ".rar", ".7z", ".bz2", ".xz",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".class", ".jar",
    ".pyc", ".pyo", ".o", ".a", ".lib",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".mkv", ".flac",
    ".db", ".sqlite", ".sqlite3",
}


def is_likely_binary(file_path: str, raw: bytes) -> bool:
    import os

    _, ext = os.path.splitext(file_path)
    if ext.lower() in BINARY_EXTENSIONS:
        return True
    return b"\x00" in raw[:8192]


def decode_text(raw: bytes) -> str | None:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def chunk_text(text: str, max_lines: int, overlap_lines: int) -> list[dict]:
    if not text:
        return []

    lines = text.splitlines()
    if not lines:
        return []

    overlap_lines = min(overlap_lines, max_lines - 1) if max_lines > 1 else 0
    step = max(max_lines - overlap_lines, 1)

    chunks: list[dict] = []
    chunk_index = 0
    start = 0
    while start < len(lines):
        end = min(start + max_lines, len(lines))
        chunk_lines = lines[start:end]
        chunks.append(
            {
                "chunk_index": chunk_index,
                "content": "\n".join(chunk_lines),
                "start_line": start + 1,
                "end_line": end,
            }
        )
        chunk_index += 1
        if end == len(lines):
            break
        start += step

    return chunks


def chunk_file(file_path: str, raw: bytes, max_lines: int, overlap_lines: int) -> list[dict]:
    if is_likely_binary(file_path, raw):
        return []
    text = decode_text(raw)
    if text is None:
        return []
    return chunk_text(text, max_lines=max_lines, overlap_lines=overlap_lines)
