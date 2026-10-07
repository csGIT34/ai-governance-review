from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobServiceClient, ContentSettings

from app import config

_service = None


def _container_client():
    global _service
    if _service is None:
        _service = BlobServiceClient.from_connection_string(config.BLOB_CONNECTION_STRING)
        try:
            _service.create_container(config.BLOB_CONTAINER)
        except ResourceExistsError:
            pass
    return _service.get_container_client(config.BLOB_CONTAINER)


def upload(blob_path: str, data: bytes, content_type: str):
    """Write a new blob. overwrite=False: evidence is never replaced in place."""
    _container_client().upload_blob(
        blob_path, data, overwrite=False,
        content_settings=ContentSettings(content_type=content_type))


def download(blob_path: str) -> bytes:
    return _container_client().download_blob(blob_path).readall()
