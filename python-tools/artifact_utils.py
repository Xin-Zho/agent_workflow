"""Immutable artifact file utilities. Uses os.link() for atomic non-overwrite
publishing and SHA-256 for content-addressed deduplication."""

import hashlib
import os
import uuid


def sha256_file(path: str) -> str:
    """Return the SHA-256 hex digest of a file's contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_unique(content: str | bytes, final_path: str,
                        expected_sha256: str | None = None) -> str:
    """Write content to an immutable final path. Never overwrites.

    - UUID-named paths: os.link() fails with FileExistsError if target exists.
    - Content-addressed paths: if target exists, verify SHA-256. Reuse if match;
      raise FileExistsError if mismatch.
    - Temp file must be on the same filesystem as final_path for os.link() to work.
    """
    tmp_dir = os.path.dirname(final_path) or "."
    tmp_path = os.path.join(tmp_dir, f".tmp.{uuid.uuid4().hex}")

    mode = "wb" if isinstance(content, bytes) else "w"
    with open(tmp_path, mode) as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())

    # Verify temp file hash if expected
    if expected_sha256:
        actual = sha256_file(tmp_path)
        if actual != expected_sha256:
            os.unlink(tmp_path)
            raise ValueError(f"SHA-256 mismatch: expected {expected_sha256}, got {actual}")

    # Check if target already exists
    if os.path.exists(final_path):
        if expected_sha256:
            existing_hash = sha256_file(final_path)
            if existing_hash == expected_sha256:
                os.unlink(tmp_path)  # identical content -- reuse
                return final_path
        os.unlink(tmp_path)
        raise FileExistsError(f"Artifact already exists: {final_path}")

    # Atomic publish via hard link
    try:
        os.link(tmp_path, final_path)
    except FileExistsError:
        # Race: another process created it between our check and link
        os.unlink(tmp_path)
        if expected_sha256 and os.path.exists(final_path):
            if sha256_file(final_path) == expected_sha256:
                return final_path
        raise FileExistsError(f"Artifact already exists (race): {final_path}")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    return final_path
