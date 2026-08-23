"""Unit tests for app.services.resume — pure byte-parsing / mock helpers only.

extract_text_from_pdf/docx (need libs) and extract_profile_from_text (calls Claude)
are NOT tested here. Multipart bodies are built as raw bytes.
"""
from app.services import resume


# --------------------------------------------------------------------------- #
# extract_multipart_file — quoted filename, \r\n\r\n header split, standard case
# --------------------------------------------------------------------------- #
def test_multipart_quoted_filename_crlf():
    boundary = b"BND"
    raw = (b"--BND\r\n"
           b'Content-Disposition: form-data; name="file"; filename="resume.pdf"\r\n'
           b"Content-Type: application/pdf\r\n"
           b"\r\n"
           b"HELLOFILE\r\n"
           b"--BND--\r\n")
    got = resume.extract_multipart_file(raw, boundary)
    assert got == ("resume.pdf", b"HELLOFILE")


def test_multipart_unquoted_filename():
    # First (quoted) regex fails; second regex filename=([^;\r\n\s]+) matches.
    boundary = b"BND"
    raw = (b"--BND\r\n"
           b"Content-Disposition: form-data; filename=noquotes.txt\r\n"
           b"\r\n"
           b"DATA\r\n"
           b"--BND--\r\n")
    got = resume.extract_multipart_file(raw, boundary)
    assert got == ("noquotes.txt", b"DATA")


def test_multipart_lf_only_header_split():
    # No \r\n\r\n present -> falls back to \n\n split (file_start + 2).
    boundary = b"BND"
    raw = (b"--BND\n"
           b'Content-Disposition: form-data; filename="b.txt"\n'
           b"\n"
           b"BODYBYTES\n"
           b"--BND--")
    got = resume.extract_multipart_file(raw, boundary)
    assert got == ("b.txt", b"BODYBYTES")


def test_multipart_trailing_boundary_dashes_trimmed():
    # Data region ending in "--" hits the endswith(b"--") trim branch.
    boundary = b"BND"
    raw = (b"--BND\r\n"
           b'Content-Disposition: form-data; filename="a.txt"\r\n'
           b"\r\n"
           b"CONTENT--\r\n")
    got = resume.extract_multipart_file(raw, boundary)
    assert got == ("a.txt", b"CONTENT")


def test_multipart_no_filename_returns_none():
    boundary = b"BND"
    raw = (b"--BND\r\n"
           b'Content-Disposition: form-data; name="notafile"\r\n'
           b"\r\n"
           b"stuff\r\n"
           b"--BND--\r\n")
    assert resume.extract_multipart_file(raw, boundary) is None


def test_multipart_filename_key_but_unparseable_returns_none():
    # b"filename=" present so the part is considered, but neither regex matches
    # (empty value, no quotes) -> continue -> no other part -> None.
    boundary = b"BND"
    raw = (b"--BND\r\n"
           b"Content-Disposition: form-data; filename=\r\n"
           b"\r\n"
           b"stuff\r\n"
           b"--BND--\r\n")
    assert resume.extract_multipart_file(raw, boundary) is None


def test_multipart_empty_returns_none():
    assert resume.extract_multipart_file(b"", b"BND") is None


def test_multipart_no_header_separator_returns_none():
    # filename present but neither \r\n\r\n nor \n\n separates headers from body
    # -> file_start == -1 twice -> continue -> None.
    raw = b'--BND filename="x.txt" no-blank-line-anywhere --BND--'
    assert resume.extract_multipart_file(raw, b"BND") is None


# --------------------------------------------------------------------------- #
# fallback_extract_text — utf-8 ignore-errors decode + 5000 truncation
# --------------------------------------------------------------------------- #
def test_fallback_decode_plain():
    assert resume.fallback_extract_text(b"hello world", "pdf") == "hello world"


def test_fallback_truncates_at_5000():
    out = resume.fallback_extract_text(b"a" * 6000, "pdf")
    assert len(out) == 5000


def test_fallback_ignores_bad_bytes():
    # Invalid utf-8 byte 0xff is dropped (errors='ignore'), not raised.
    out = resume.fallback_extract_text(b"ab\xffcd", "pdf")
    assert out == "abcd"


def test_fallback_empty_bytes():
    assert resume.fallback_extract_text(b"", "pdf") == ""


# --------------------------------------------------------------------------- #
# mock_extract_profile — constant text per source
# --------------------------------------------------------------------------- #
def test_mock_extract_profile_resume():
    out = resume.mock_extract_profile("resume", "ignored text")
    assert "Python and JavaScript" in out
    # deterministic / constant
    assert out == resume.mock_extract_profile("resume", "different text")


def test_mock_extract_profile_linkedin_branch():
    # Any source != "resume" hits the else branch.
    out = resume.mock_extract_profile("linkedin", "ignored")
    assert "computer science and artificial intelligence" in out
    assert out != resume.mock_extract_profile("resume", "x")
