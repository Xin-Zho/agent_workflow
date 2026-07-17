"""Tests for the immutable artifact write utilities."""

import hashlib
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "python-tools"))

import pytest  # noqa: E402
from artifact_utils import atomic_write_unique, sha256_file  # noqa: E402


def sha256_file_content(data: bytes) -> str:
    """Return the SHA-256 digest of an in-memory byte string (helper)."""
    return hashlib.sha256(data).hexdigest()


class TestSha256File:
    def test_computes_digest_of_file(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "f.dat")
            with open(path, "wb") as f:
                f.write(b"hello world")
            expected = hashlib.sha256(b"hello world").hexdigest()
            assert sha256_file(path) == expected
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class TestAtomicWriteUnique:
    def test_creates_file(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "test.txt")
            result = atomic_write_unique("hello world", path)
            assert result == path
            assert os.path.exists(path)
            with open(path) as f:
                assert f.read() == "hello world"
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_refuses_overwrite(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "test.txt")
            atomic_write_unique("first", path)
            with pytest.raises(FileExistsError):
                atomic_write_unique("second", path)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_content_hash_reuse(self):
        """Identical content with matching expected hash reuses target."""
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "data.bin")
            h = sha256_file_content(b"same content")
            result1 = atomic_write_unique(b"same content", path, expected_sha256=h)
            result2 = atomic_write_unique(b"same content", path, expected_sha256=h)
            assert result1 == result2
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_existing_file_hash_mismatch_raises(self):
        """Writing to an occupied path with a wrong hash raises FileExistsError."""
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "data.bin")
            # Write content A first
            atomic_write_unique(b"content A", path)
            # Try to write content B claiming its own hash; existing file has
            # content A's hash, so reuse is denied.
            h_b = sha256_file_content(b"content B")
            with pytest.raises(FileExistsError):
                atomic_write_unique(b"content B", path, expected_sha256=h_b)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_expected_hash_raises_value_error(self):
        """Claiming a hash that doesn't match the written content raises ValueError."""
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "data.bin")
            wrong_hash = sha256_file_content(b"wrong")
            with pytest.raises(ValueError, match="SHA-256 mismatch"):
                atomic_write_unique(b"actual content", path,
                                    expected_sha256=wrong_hash)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_writes_bytes_and_string(self):
        """Both str and bytes content are accepted."""
        tmp = tempfile.mkdtemp()
        try:
            path_s = os.path.join(tmp, "s.txt")
            atomic_write_unique("text content", path_s)
            with open(path_s) as f:
                assert f.read() == "text content"

            path_b = os.path.join(tmp, "b.bin")
            atomic_write_unique(b"binary content", path_b)
            with open(path_b, "rb") as f:
                assert f.read() == b"binary content"
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
