import logging
import os
from pathlib import Path
from typing import Optional
from google.cloud import storage

logger = logging.getLogger(__name__)


class GCSStorageManager:
    """Manages synchronization between local extended memory directory and Google Cloud Storage,
    partitioned by synthetic identity subfolders."""

    def __init__(
        self,
        bucket_name: Optional[str] = None,
        local_base_dir: str = "/tmp/atlas_brain",
        identity_slug: str = "default",
    ):
        self.bucket_name = bucket_name or os.environ.get("GCS_MEMORY_BUCKET", "")
        self.identity_slug = self._sanitize_slug(identity_slug)
        self.local_base_dir = Path(local_base_dir) / self.identity_slug
        self.local_base_dir.mkdir(parents=True, exist_ok=True)
        self._client: Optional[storage.Client] = None

    @staticmethod
    def _sanitize_slug(val: str) -> str:
        clean = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in str(val).lower().strip())
        return clean or "default"

    def set_identity(self, identity_name_or_email: str) -> None:
        """Dynamically updates the active identity folder."""
        self.identity_slug = self._sanitize_slug(identity_name_or_email)
        self.local_base_dir = self.local_base_dir.parent / self.identity_slug
        self.local_base_dir.mkdir(parents=True, exist_ok=True)

    @property
    def client(self) -> Optional[storage.Client]:
        if not self.bucket_name:
            return None
        if not self._client:
            try:
                self._client = storage.Client()
            except Exception as e:
                logger.warning(f"Could not initialize GCS client: {e}")
        return self._client

    def _get_identity_prefix(self, base_prefix: str = "identities") -> str:
        """Returns the GCS path prefix for the current synthetic identity: e.g. identities/atlas/memory/"""
        clean_base = base_prefix.strip("/")
        return f"{clean_base}/{self.identity_slug}/memory/"

    def sync_from_gcs(self, prefix: Optional[str] = None) -> None:
        """Downloads conversation memory files from the identity's GCS subfolder to local directory."""
        if not self.client or not self.bucket_name:
            logger.info("GCS memory bucket not configured. Operating with local memory.")
            return

        target_prefix = prefix or self._get_identity_prefix()
        try:
            bucket = self.client.bucket(self.bucket_name)
            blobs = bucket.list_blobs(prefix=target_prefix)
            count = 0
            for blob in blobs:
                if blob.name.endswith("/"):
                    continue
                rel_path = blob.name[len(target_prefix):] if blob.name.startswith(target_prefix) else blob.name
                dest_file = self.local_base_dir / rel_path
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                blob.download_to_filename(str(dest_file))
                count += 1
                logger.debug(f"Downloaded memory file {blob.name} -> {dest_file}")
            logger.info(f"Synchronized {count} memory file(s) from GCS ({target_prefix}).")
        except Exception as e:
            logger.error(f"Failed to sync memory from GCS ({target_prefix}): {e}")

    def sync_to_gcs(self, prefix: Optional[str] = None) -> None:
        """Uploads updated conversation memory files from local directory to the identity's GCS subfolder."""
        if not self.client or not self.bucket_name:
            return

        target_prefix = prefix or self._get_identity_prefix()
        try:
            bucket = self.client.bucket(self.bucket_name)
            count = 0
            for path in self.local_base_dir.rglob("*"):
                if path.is_file():
                    rel_path = path.relative_to(self.local_base_dir)
                    blob_name = f"{target_prefix.rstrip('/')}/{rel_path}"
                    blob = bucket.blob(blob_name)
                    blob.upload_from_filename(str(path))
                    count += 1
                    logger.debug(f"Uploaded memory file {path} -> {blob_name}")
            logger.info(f"Synchronized {count} memory file(s) to GCS ({target_prefix}).")
        except Exception as e:
            logger.error(f"Failed to sync memory to GCS ({target_prefix}): {e}")
