"""Write-once file storage in Azure Blob.

Auth: BLOB_ACCOUNT_URL set -> managed identity / DefaultAzureCredential (production; the
identity needs 'Storage Blob Data Contributor'). Otherwise BLOB_CONNECTION_STRING (Azurite).
"""
import logging

from azure.core.exceptions import HttpResponseError, ResourceExistsError
from azure.storage.blob import BlobServiceClient, ContentSettings

from app import config

logger = logging.getLogger(__name__)
_container = None


def _client() -> BlobServiceClient:
    if config.BLOB_ACCOUNT_URL:
        from azure.identity import DefaultAzureCredential
        return BlobServiceClient(config.BLOB_ACCOUNT_URL, credential=DefaultAzureCredential())
    return BlobServiceClient.from_connection_string(config.BLOB_CONNECTION_STRING)


def _container_client():
    global _container
    if _container is None:
        service = _client()
        try:
            service.create_container(config.BLOB_CONTAINER)
        except ResourceExistsError:
            pass
        except HttpResponseError as err:  # e.g. 403: container is provisioned by IaC instead
            logger.warning("Could not create blob container %s (%s); assuming it exists",
                           config.BLOB_CONTAINER, err.status_code)
        _container = service.get_container_client(config.BLOB_CONTAINER)
    return _container


def upload(blob_path: str, data: bytes, content_type: str):
    """Write a new blob. overwrite=False: evidence is never replaced in place."""
    _container_client().upload_blob(
        blob_path, data, overwrite=False,
        content_settings=ContentSettings(content_type=content_type))


def download(blob_path: str) -> bytes:
    return _container_client().download_blob(blob_path).readall()
