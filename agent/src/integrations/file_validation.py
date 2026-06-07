"""
Content-aware upload validation for finance-operations imports.

Detects file kinds from magic bytes and structure — not from the filename alone —
and returns precise, user-facing errors for unsupported, empty, corrupted,
encrypted, password-protected, or oversized uploads.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional

from src.integrations.models import SourceFormat

# Connector imports accept tabular/text exports and Excel workbooks.
CONNECTOR_UPLOAD_KINDS = frozenset({"csv", "json", "jsonl", "xlsx", "xls"})
WORKBOOK_UPLOAD_KINDS = frozenset({"xlsx", "xls"})

# Guardrail catalog — detected even when not accepted for import.
SNIFFABLE_KINDS = frozenset(
    {"csv", "json", "jsonl", "xlsx", "xls", "docx", "pdf", "txt", "md"}
)

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MiB


class UploadValidationCode(str, Enum):
    UNSUPPORTED_TYPE = "unsupported_type"
    EMPTY_FILE = "empty_file"
    CORRUPTED_FILE = "corrupted_file"
    ENCRYPTED_FILE = "encrypted_file"
    PASSWORD_PROTECTED = "password_protected"
    TOO_LARGE = "too_large"
    EXTENSION_MISMATCH = "extension_mismatch"


@dataclass(frozen=True)
class UploadValidationError(Exception):
    code: UploadValidationCode
    message: str
    detected_kind: Optional[str] = None
    allowed_kinds: tuple[str, ...] = ()
    max_bytes: Optional[int] = None
    size_bytes: Optional[int] = None

    def as_detail(self) -> dict[str, object]:
        payload: dict[str, object] = {"code": self.code.value, "message": self.message}
        if self.detected_kind:
            payload["detected_kind"] = self.detected_kind
        if self.allowed_kinds:
            payload["allowed_kinds"] = list(self.allowed_kinds)
        if self.max_bytes is not None:
            payload["max_bytes"] = self.max_bytes
        if self.size_bytes is not None:
            payload["size_bytes"] = self.size_bytes
        return payload


def _human_kinds(kinds: Iterable[str]) -> str:
    ordered = [k for k in ("csv", "json", "jsonl", "xlsx", "xls", "docx", "pdf", "txt", "md") if k in kinds]
    if not ordered:
        ordered = sorted(kinds)
    if len(ordered) == 1:
        return ordered[0].upper()
    return ", ".join(k.upper() for k in ordered[:-1]) + f", or {ordered[-1].upper()}"


def _extension_kind(filename: str) -> Optional[str]:
    suffix = Path(filename or "").suffix.lower()
    mapping = {
        ".csv": "csv",
        ".json": "json",
        ".jsonl": "jsonl",
        ".xlsx": "xlsx",
        ".xls": "xls",
        ".docx": "docx",
        ".pdf": "pdf",
        ".txt": "txt",
        ".md": "md",
        ".markdown": "md",
    }
    return mapping.get(suffix)


def _decode_text(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UploadValidationError(
            UploadValidationCode.CORRUPTED_FILE,
            "File is not valid UTF-8 text. Save CSV/JSON exports as UTF-8 and try again.",
        ) from exc


def _looks_like_jsonl(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 1:
        return False
    if len(lines) == 1:
        return False
    for line in lines[: min(12, len(lines))]:
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            return False
        if not isinstance(parsed, (dict, list)):
            return False
    return True


def _looks_like_json(text: str) -> bool:
    stripped = text.lstrip("\ufeff").strip()
    if not stripped:
        return False
    if stripped[0] not in "{[":
        return False
    try:
        json.loads(stripped)
    except json.JSONDecodeError:
        return False
    return True


def _looks_like_csv(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 1:
        return False
    sample = lines[: min(20, len(lines))]
    delimiter_counts = {",": 0, "\t": 0, ";": 0, "|": 0}
    for line in sample:
        for delim in delimiter_counts:
            delimiter_counts[delim] += line.count(delim)
    best = max(delimiter_counts.values())
    if best <= 0:
        return len(sample) >= 1 and len(sample[0].split()) >= 2
    return True


def _looks_like_markdown(text: str, filename: str) -> bool:
    if Path(filename or "").suffix.lower() in {".md", ".markdown"}:
        return True
    patterns = (
        r"^#{1,6}\s+\S",
        r"^\*\s+\S",
        r"^-\s+\S",
        r"^\d+\.\s+\S",
        r"\[[^\]]+\]\([^)]+\)",
        r"^>\s+\S",
    )
    hits = 0
    for line in text.splitlines()[:40]:
        if any(re.search(pattern, line.strip()) for pattern in patterns):
            hits += 1
    return hits >= 2


def _pdf_is_encrypted(raw: bytes) -> bool:
    head = raw[:65536]
    try:
        text = head.decode("latin-1", errors="ignore")
    except Exception:
        return False
    return "/Encrypt" in text


def _inspect_zip(raw: bytes) -> tuple[str, bool]:
    """Return (kind, encrypted) for OOXML/ZIP containers."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = set(archive.namelist())
    except zipfile.BadZipFile as exc:
        raise UploadValidationError(
            UploadValidationCode.CORRUPTED_FILE,
            "File looks like a ZIP archive but is corrupted or truncated.",
        ) from exc

    encrypted = "EncryptedPackage" in names or "encryptioninfo" in {n.lower() for n in names}
    if any(name.startswith("xl/") for name in names):
        return "xlsx", encrypted
    if any(name.startswith("word/") for name in names):
        return "docx", encrypted
    if "[Content_Types].xml" in names:
        return "docx", encrypted
    raise UploadValidationError(
        UploadValidationCode.UNSUPPORTED_TYPE,
        "ZIP archive is not a recognized Excel workbook (.xlsx) or Word document (.docx).",
        detected_kind="unknown",
    )


def _inspect_ole(raw: bytes) -> tuple[str, bool]:
    """Return (kind, encrypted) for legacy OLE compound documents (.xls/.doc)."""
    if len(raw) < 512:
        raise UploadValidationError(
            UploadValidationCode.CORRUPTED_FILE,
            "Legacy Excel file (.xls) is truncated or corrupted.",
        )
    # Encrypted OLE workbooks often carry this well-known stream name near the header.
    header = raw[:8192]
    encrypted = b"EncryptedPackage" in header or b"EncryptionInfo" in header
    # xlrd/openpyxl distinguish xls from doc; for sniffing we treat OLE as xls when
    # the upload contract expects spreadsheets, and flag doc-like uploads separately.
    if b"Word.Document" in header or b"WordDocument" in header:
        return "docx", encrypted
    return "xls", encrypted


def sniff_upload_kind(raw: bytes, filename: str = "") -> str:
    """Detect the file kind from content. Raises UploadValidationError on empty input."""
    if not raw:
        raise UploadValidationError(
            UploadValidationCode.EMPTY_FILE,
            "Uploaded file is empty (0 bytes). Choose a non-empty export and try again.",
        )

    if raw.startswith(b"%PDF"):
        return "pdf"
    if raw.startswith(b"PK\x03\x04"):
        kind, _encrypted = _inspect_zip(raw)
        return kind
    if raw.startswith(b"\xd0\xcf\x11\xe0"):
        kind, _encrypted = _inspect_ole(raw)
        return kind

    text = _decode_text(raw)
    stripped = text.strip()
    if not stripped:
        raise UploadValidationError(
            UploadValidationCode.EMPTY_FILE,
            "Uploaded file contains no readable data.",
        )

    ext_kind = _extension_kind(filename)
    if ext_kind == "jsonl" or _looks_like_jsonl(text):
        return "jsonl"
    if ext_kind == "json" or _looks_like_json(text):
        return "json"
    if ext_kind == "csv" or _looks_like_csv(text):
        return "csv"
    if _looks_like_markdown(text, filename):
        return "md"
    return "txt"


def _assert_not_encrypted(kind: str, raw: bytes) -> None:
    encrypted = False
    if kind == "pdf":
        encrypted = _pdf_is_encrypted(raw)
    elif kind in {"xlsx", "docx"} and raw.startswith(b"PK\x03\x04"):
        _, encrypted = _inspect_zip(raw)
    elif kind == "xls" and raw.startswith(b"\xd0\xcf\x11\xe0"):
        _, encrypted = _inspect_ole(raw)

    if not encrypted:
        return

    label = kind.upper()
    raise UploadValidationError(
        UploadValidationCode.PASSWORD_PROTECTED,
        f"{label} file appears encrypted or password-protected. Remove protection and upload an unencrypted export.",
        detected_kind=kind,
    )


def _assert_parseable(kind: str, raw: bytes) -> None:
    if kind == "xlsx":
        try:
            from openpyxl import load_workbook

            load_workbook(io.BytesIO(raw), read_only=True, data_only=True).close()
        except Exception as exc:
            message = str(exc).lower()
            if "encrypt" in message or "password" in message:
                raise UploadValidationError(
                    UploadValidationCode.PASSWORD_PROTECTED,
                    "Excel workbook is password-protected. Remove protection and try again.",
                    detected_kind=kind,
                ) from exc
            raise UploadValidationError(
                UploadValidationCode.CORRUPTED_FILE,
                "Excel workbook (.xlsx) is corrupted or not readable.",
                detected_kind=kind,
            ) from exc
    elif kind == "xls":
        try:
            import xlrd

            xlrd.open_workbook(file_contents=raw)
        except Exception as exc:
            message = str(exc).lower()
            if "encrypt" in message or "password" in message:
                raise UploadValidationError(
                    UploadValidationCode.PASSWORD_PROTECTED,
                    "Legacy Excel workbook (.xls) is password-protected. Remove protection and try again.",
                    detected_kind=kind,
                ) from exc
            raise UploadValidationError(
                UploadValidationCode.CORRUPTED_FILE,
                "Legacy Excel workbook (.xls) is corrupted or not readable.",
                detected_kind=kind,
            ) from exc
    elif kind == "json":
        try:
            json.loads(_decode_text(raw))
        except json.JSONDecodeError as exc:
            raise UploadValidationError(
                UploadValidationCode.CORRUPTED_FILE,
                f"JSON file is malformed near character {exc.pos}: {exc.msg}.",
                detected_kind=kind,
            ) from exc
    elif kind == "jsonl":
        text = _decode_text(raw)
        for index, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError as exc:
                raise UploadValidationError(
                    UploadValidationCode.CORRUPTED_FILE,
                    f"JSONL file has invalid JSON on line {index}: {exc.msg}.",
                    detected_kind=kind,
                ) from exc


def validate_upload(
    raw: bytes,
    *,
    filename: str = "",
    content_type: Optional[str] = None,
    allowed_kinds: Iterable[str],
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> tuple[str, SourceFormat]:
    """Validate an upload and return (detected_kind, SourceFormat)."""
    allowed = frozenset(k.lower() for k in allowed_kinds)
    size = len(raw or b"")
    if size > max_bytes:
        raise UploadValidationError(
            UploadValidationCode.TOO_LARGE,
            f"File is too large ({size:,} bytes). Maximum upload size is {max_bytes:,} bytes ({max_bytes // (1024 * 1024)} MiB).",
            max_bytes=max_bytes,
            size_bytes=size,
        )

    detected = sniff_upload_kind(raw, filename)
    ext_kind = _extension_kind(filename)

    if detected not in SNIFFABLE_KINDS:
        raise UploadValidationError(
            UploadValidationCode.UNSUPPORTED_TYPE,
            "Could not identify a supported file type from the file contents.",
            detected_kind=detected,
            allowed_kinds=tuple(sorted(allowed)),
        )

    if detected not in allowed:
        raise UploadValidationError(
            UploadValidationCode.UNSUPPORTED_TYPE,
            f"Unsupported file type: detected {detected.upper()} content"
            + (f" ({filename})" if filename else "")
            + f". Upload {_human_kinds(allowed)}.",
            detected_kind=detected,
            allowed_kinds=tuple(sorted(allowed)),
        )

    if ext_kind and ext_kind != detected and ext_kind in SNIFFABLE_KINDS and detected in SNIFFABLE_KINDS:
        # Content wins, but surface a precise mismatch to help mislabeled exports.
        raise UploadValidationError(
            UploadValidationCode.EXTENSION_MISMATCH,
            f"File extension suggests {ext_kind.upper()}, but contents look like {detected.upper()}. "
            f"Rename the file or export the correct format ({_human_kinds(allowed)}).",
            detected_kind=detected,
            allowed_kinds=tuple(sorted(allowed)),
        )

    _assert_not_encrypted(detected, raw)
    _assert_parseable(detected, raw)

    return detected, kind_to_source_format(detected)


def kind_to_source_format(kind: str) -> SourceFormat:
    if kind == "csv":
        return SourceFormat.CSV
    if kind in {"json", "jsonl"}:
        return SourceFormat.JSON
    if kind == "xlsx":
        return SourceFormat.XLSX
    if kind == "xls":
        return SourceFormat.XLS
    raise UploadValidationError(
        UploadValidationCode.UNSUPPORTED_TYPE,
        f"Cannot map detected kind {kind!r} to an import parser.",
        detected_kind=kind,
    )


def accepted_types_label(allowed_kinds: Iterable[str]) -> str:
    return _human_kinds(allowed_kinds)
