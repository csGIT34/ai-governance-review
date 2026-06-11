from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobServiceClient

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


def upload_artifact(review_id: str, filename: str, data: bytes) -> str:
    blob_path = f"{review_id}/{filename}"
    _container_client().upload_blob(blob_path, data, overwrite=True)
    return blob_path


def download_artifact(blob_path: str) -> bytes:
    return _container_client().download_blob(blob_path).readall()
