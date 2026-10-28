import os
import io
import json
import logging
import tempfile
from typing import Optional, BinaryIO, Tuple
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


class StorageService:
    """
    Enterprise Storage Service supporting Google Cloud Storage (GCS) as the primary
    remote object store, with graceful fallback to local storage if credentials
    are not yet configured.
    """

    def __init__(self):
        self.client = None
        self.bucket = None
        self.is_gcs_active = False
        self._init_gcs()

    def _init_gcs(self):
        bucket_name = (settings.GCS_BUCKET_NAME or "").strip()
        if not bucket_name:
            logger.info("GCS_BUCKET_NAME not configured; using local file storage.")
            return

        try:
            from google.cloud import storage
            from google.oauth2 import service_account

            creds = None
            # 1. From raw JSON string in environment variable
            if settings.GCS_CREDENTIALS_JSON:
                try:
                    info = json.loads(settings.GCS_CREDENTIALS_JSON)
                    creds = service_account.Credentials.from_service_account_info(info)
                    logger.info("Loaded GCS credentials from GCS_CREDENTIALS_JSON.")
                except Exception as e:
                    logger.warning(f"Failed to parse GCS_CREDENTIALS_JSON: {e}")

            # 2. From Service Account JSON file path
            if not creds and settings.GCS_CREDENTIALS_FILE:
                cred_file = os.path.expanduser(settings.GCS_CREDENTIALS_FILE)
                if os.path.exists(cred_file):
                    creds = service_account.Credentials.from_service_account_file(cred_file)
                    logger.info(f"Loaded GCS credentials from file: {cred_file}")
                else:
                    logger.warning(f"GCS_CREDENTIALS_FILE not found: {cred_file}")

            # 3. Initialize storage client
            project = settings.GCS_PROJECT_ID or None
            if creds:
                self.client = storage.Client(credentials=creds, project=project or creds.project_id)
            else:
                self.client = storage.Client(project=project)

            self.bucket = self.client.bucket(bucket_name)
            self.is_gcs_active = True
            logger.info(f"Google Cloud Storage initialized successfully. Active bucket: gs://{bucket_name}")

        except Exception as e:
            logger.error(f"Failed to initialize Google Cloud Storage ({e}). Falling back to local storage.")
            self.is_gcs_active = False

    def get_blob_name(self, gcs_path: str) -> str:
        """Strips gs://<bucket_name>/ prefix if present."""
        if gcs_path.startswith("gs://"):
            parts = gcs_path[5:].split("/", 1)
            return parts[1] if len(parts) > 1 else parts[0]
        return gcs_path

    def upload_file(
        self,
        file_obj: BinaryIO | bytes | str,
        destination_path: str,
        content_type: Optional[str] = None,
    ) -> str:
        """
        Uploads a file to Google Cloud Storage or local disk.
        Returns the persistent URI: gs://<bucket>/<path> or local file path.
        """
        if self.is_gcs_active and self.bucket:
            try:
                blob_name = self.get_blob_name(destination_path)
                blob = self.bucket.blob(blob_name)

                if isinstance(file_obj, bytes):
                    blob.upload_from_string(file_obj, content_type=content_type)
                elif isinstance(file_obj, str) and os.path.exists(file_obj):
                    blob.upload_from_filename(file_obj, content_type=content_type)
                else:
                    if hasattr(file_obj, "seek"):
                        file_obj.seek(0)
                    blob.upload_from_file(file_obj, content_type=content_type)

                gcs_uri = f"gs://{settings.GCS_BUCKET_NAME}/{blob_name}"
                logger.info(f"Successfully uploaded file to GCS: {gcs_uri}")
                return gcs_uri
            except Exception as e:
                logger.error(f"GCS upload failed ({e}); falling back to local disk.")

        # Local fallback
        dest_local = os.path.join(settings.UPLOAD_DIR, os.path.basename(destination_path))
        os.makedirs(os.path.dirname(dest_local) or settings.UPLOAD_DIR, exist_ok=True)

        if isinstance(file_obj, bytes):
            with open(dest_local, "wb") as f:
                f.write(file_obj)
        elif isinstance(file_obj, str) and os.path.exists(file_obj) and file_obj != dest_local:
            import shutil
            shutil.copyfile(file_obj, dest_local)
        elif hasattr(file_obj, "read"):
            if hasattr(file_obj, "seek"):
                file_obj.seek(0)
            with open(dest_local, "wb") as f:
                f.write(file_obj.read())

        return dest_local

    def download_to_temp(self, file_path_or_uri: str) -> str:
        """
        Ensures the file is locally available on disk for extraction parsers
        (pypdf, python-docx, openpyxl, Pillow).
        Returns a local absolute file path.
        """
        # If it's already a local file that exists, return directly
        if not file_path_or_uri.startswith("gs://") and os.path.exists(file_path_or_uri):
            return file_path_or_uri

        # If it's stored in GCS
        if self.is_gcs_active and self.bucket:
            blob_name = self.get_blob_name(file_path_or_uri)
            blob = self.bucket.blob(blob_name)
            suffix = Path(blob_name).suffix
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            blob.download_to_filename(temp_file.name)
            logger.info(f"Downloaded GCS blob {blob_name} to temporary file: {temp_file.name}")
            return temp_file.name

        # Fallback check inside settings.UPLOAD_DIR
        local_candidate = os.path.join(settings.UPLOAD_DIR, os.path.basename(file_path_or_uri))
        if os.path.exists(local_candidate):
            return local_candidate

        return file_path_or_uri

    def get_file_bytes(self, file_path_or_uri: str) -> Tuple[bytes, str]:
        """
        Retrieves file bytes and content type for streaming responses.
        """
        if file_path_or_uri.startswith("gs://") and self.is_gcs_active and self.bucket:
            blob_name = self.get_blob_name(file_path_or_uri)
            blob = self.bucket.blob(blob_name)
            data = blob.download_as_bytes()
            content_type = blob.content_type or "application/octet-stream"
            return data, content_type

        # Local file
        local_path = file_path_or_uri
        if not os.path.exists(local_path):
            local_path = os.path.join(settings.UPLOAD_DIR, os.path.basename(file_path_or_uri))

        if os.path.exists(local_path):
            with open(local_path, "rb") as f:
                data = f.read()
            import mimetypes
            content_type, _ = mimetypes.guess_type(local_path)
            return data, content_type or "application/octet-stream"

        raise FileNotFoundError(f"File not found: {file_path_or_uri}")

    def generate_signed_url(self, file_path_or_uri: str, expiration_minutes: int = 15) -> Optional[str]:
        """
        Generates a secure time-limited Signed URL for direct browser viewing.
        """
        if file_path_or_uri.startswith("gs://") and self.is_gcs_active and self.bucket:
            try:
                import datetime
                blob_name = self.get_blob_name(file_path_or_uri)
                blob = self.bucket.blob(blob_name)
                url = blob.generate_signed_url(
                    version="v4",
                    expiration=datetime.timedelta(minutes=expiration_minutes),
                    method="GET",
                )
                return url
            except Exception as e:
                logger.warning(f"Failed to generate GCS signed URL ({e})")
        return None

    def delete_file(self, file_path_or_uri: str) -> bool:
        """Deletes a file from GCS or local disk."""
        deleted = False
        if file_path_or_uri.startswith("gs://") and self.is_gcs_active and self.bucket:
            try:
                blob_name = self.get_blob_name(file_path_or_uri)
                blob = self.bucket.blob(blob_name)
                blob.delete()
                deleted = True
                logger.info(f"Deleted GCS blob: {blob_name}")
            except Exception as e:
                logger.warning(f"Failed to delete GCS blob ({e})")

        # Also clean up local file if present
        local_path = file_path_or_uri
        if not os.path.exists(local_path):
            local_path = os.path.join(settings.UPLOAD_DIR, os.path.basename(file_path_or_uri))
        if os.path.exists(local_path):
            try:
                os.remove(local_path)
                deleted = True
            except Exception:
                pass

        return deleted


storage_service = StorageService()
