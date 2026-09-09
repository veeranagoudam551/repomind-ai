"""Unit tests for app.services.code_chunking.reconstruct_file_content.

`chunk_text` (Day 8) produces overlapping windows so a boundary never cuts
off context; `reconstruct_file_content` (Day 22) needs to undo exactly that
overlap and recover the original text. The strongest check for that is a
round-trip: chunk real text with the real chunker, then reconstruct it and
compare against the original - not just structurally similar assertions.
"""

from types import SimpleNamespace

from app.services.code_chunking import chunk_text, reconstruct_file_content


def _as_chunks(chunk_dicts: list[dict]) -> list[SimpleNamespace]:
    # Production code passes CodeChunk ORM rows, which have these same
    # attributes; SimpleNamespace stands in for that here without needing
    # a database.
    return [SimpleNamespace(**d) for d in chunk_dicts]


def test_reconstruct_round_trips_with_overlapping_chunks():
    text = "\n".join(f"line {i}" for i in range(1, 251))
    chunk_dicts = chunk_text(text, max_lines=100, overlap_lines=15)
    assert len(chunk_dicts) > 1  # sanity: this text actually needed multiple chunks

    assert reconstruct_file_content(_as_chunks(chunk_dicts)) == text


def test_reconstruct_round_trips_with_no_overlap():
    text = "\n".join(f"line {i}" for i in range(1, 251))
    chunk_dicts = chunk_text(text, max_lines=100, overlap_lines=0)

    assert reconstruct_file_content(_as_chunks(chunk_dicts)) == text


def test_reconstruct_single_chunk_file():
    text = "just a few lines\nof a small file\n"
    chunk_dicts = chunk_text(text, max_lines=100, overlap_lines=15)
    assert len(chunk_dicts) == 1

    assert reconstruct_file_content(_as_chunks(chunk_dicts)) == text.rstrip("\n")


def test_reconstruct_handles_out_of_order_chunks():
    text = "\n".join(f"line {i}" for i in range(1, 251))
    chunk_dicts = chunk_text(text, max_lines=100, overlap_lines=15)
    shuffled = list(reversed(chunk_dicts))

    assert reconstruct_file_content(_as_chunks(shuffled)) == text


def test_reconstruct_empty_list_returns_empty_string():
    assert reconstruct_file_content([]) == ""
