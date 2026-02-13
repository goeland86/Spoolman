"""NFC reader hardware abstraction service.

Provides a singleton NfcService wrapping the nfcpy library for
PN532/RC522 USB/UART and ACR122U NFC readers on Raspberry Pi and similar.
"""

import logging
import threading
from typing import Optional

from spoolman.env import get_nfc_device_path, get_nfc_reader_type

logger = logging.getLogger(__name__)


class NfcService:
    """NFC reader service for reading/writing NTAG213 tags."""

    def __init__(self) -> None:
        self._clf = None
        self._lock = threading.Lock()
        self._initialized = False
        self._status = "not_initialized"

    def initialize(self) -> None:
        """Initialize the NFC reader. Call once at startup."""
        reader_type = get_nfc_reader_type()
        device_path = get_nfc_device_path()

        if reader_type != "nfcpy":
            logger.warning("Unsupported NFC reader type: %s. Only 'nfcpy' is supported.", reader_type)
            self._status = "unsupported_reader"
            return

        try:
            import nfc  # noqa: PLC0415

            path = device_path or "usb"
            self._clf = nfc.ContactlessFrontend(path)
            self._initialized = True
            self._status = "connected"
            logger.info("NFC reader initialized successfully on %s", path)
        except ImportError:
            logger.warning(
                "nfcpy is not installed. Install it with: pip install nfcpy. "
                "NFC features will be unavailable.",
            )
            self._status = "nfcpy_not_installed"
        except Exception:
            logger.exception("Failed to initialize NFC reader")
            self._status = "error"

    def get_status(self) -> str:
        """Get the current status of the NFC reader.

        Returns:
            str: Status string ('connected', 'not_initialized', 'error', etc.)

        """
        return self._status

    def read_tag(self, timeout: float = 10.0) -> Optional[bytes]:
        """Read raw bytes from an NTAG213 tag.

        Reads pages 4-39 (144 bytes of user memory).

        Args:
            timeout: Timeout in seconds for waiting for a tag.

        Returns:
            Optional[bytes]: Raw tag data (144 bytes), or None if no tag found.

        """
        if not self._initialized or self._clf is None:
            logger.warning("NFC reader not initialized")
            return None

        with self._lock:
            try:
                import nfc  # noqa: PLC0415
                import nfc.tag  # noqa: PLC0415

                tag = self._clf.connect(
                    rdwr={"on-connect": lambda tag: False},
                    terminate=lambda: False,
                )

                if tag is None:
                    return None

                if not hasattr(tag, "read"):
                    logger.warning("Connected tag does not support read operations")
                    return None

                # Read pages 4-39 (NTAG213 user memory)
                data = bytearray()
                for page in range(4, 40):
                    page_data = tag.read(page)
                    if page_data is None:
                        logger.warning("Failed to read page %d", page)
                        return None
                    data.extend(page_data)

                return bytes(data[:144])

            except Exception:
                logger.exception("Failed to read NFC tag")
                return None

    def write_tag(self, data: bytes, timeout: float = 10.0) -> bool:
        """Write raw bytes to an NTAG213 tag.

        Writes to pages 4-39 (144 bytes of user memory).

        Args:
            data: Raw bytes to write (should be 144 bytes).
            timeout: Timeout in seconds for waiting for a tag.

        Returns:
            bool: True if write was successful, False otherwise.

        """
        if not self._initialized or self._clf is None:
            logger.warning("NFC reader not initialized")
            return False

        if len(data) != 144:
            logger.warning("Expected 144 bytes, got %d", len(data))
            return False

        with self._lock:
            try:
                import nfc  # noqa: PLC0415

                tag = self._clf.connect(
                    rdwr={"on-connect": lambda tag: False},
                    terminate=lambda: False,
                )

                if tag is None:
                    return False

                if not hasattr(tag, "write"):
                    logger.warning("Connected tag does not support write operations")
                    return False

                # Write pages 4-39 (4 bytes per page, 36 pages)
                for page_num in range(36):
                    page_offset = page_num * 4
                    page_data = data[page_offset : page_offset + 4]
                    success = tag.write(page_num + 4, page_data)
                    if not success:
                        logger.warning("Failed to write page %d", page_num + 4)
                        return False

                return True

            except Exception:
                logger.exception("Failed to write NFC tag")
                return False

    def close(self) -> None:
        """Close the NFC reader connection."""
        if self._clf is not None:
            try:
                self._clf.close()
            except Exception:
                logger.exception("Error closing NFC reader")
            finally:
                self._clf = None
                self._initialized = False
                self._status = "closed"


# Singleton instance
nfc_service = NfcService()
