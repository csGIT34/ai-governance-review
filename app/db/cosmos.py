import logging
import time

from azure.cosmos import CosmosClient, PartitionKey, exceptions

from app import config

logger = logging.getLogger(__name__)

_container = None


def ensure_resources(retries: int = 30, delay: float = 3.0):
    """Create database/container if missing. Retries because the Cosmos
    emulator can take a while to accept connections after compose up."""
    global _container
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            # enable_endpoint_discovery=False: the Cosmos emulator advertises
            # 127.0.0.1 as its location, which is unreachable from inside the
            # app container; pin the SDK to the configured endpoint instead.
            client = CosmosClient(
                config.COSMOS_ENDPOINT,
                credential=config.COSMOS_KEY,
                enable_endpoint_discovery=False,
            )
            db = client.create_database_if_not_exists(config.COSMOS_DATABASE)
            _container = db.create_container_if_not_exists(
                id=config.COSMOS_CONTAINER, partition_key=PartitionKey(path="/csp")
            )
            logger.info("Cosmos DB ready at %s", config.COSMOS_ENDPOINT)
            return
        except Exception as err:
            last_err = err
            logger.warning("Cosmos not ready (attempt %d/%d): %s", attempt, retries, err)
            time.sleep(delay)
    raise RuntimeError(f"Could not reach Cosmos DB at {config.COSMOS_ENDPOINT}") from last_err


def container():
    if _container is None:
        ensure_resources(retries=1, delay=0)
    return _container


def list_reviews():
    return list(
        container().query_items(
            "SELECT * FROM c WHERE c.csp != '_settings' ORDER BY c.created_at DESC",
            enable_cross_partition_query=True,
        )
    )


def get_review(review_id: str):
    items = list(
        container().query_items(
            "SELECT * FROM c WHERE c.id = @id",
            parameters=[{"name": "@id", "value": review_id}],
            enable_cross_partition_query=True,
        )
    )
    return items[0] if items else None


def save_review(doc: dict):
    container().upsert_item(doc)


# App settings live in the same container under the reserved partition
# "_settings" (excluded from list_reviews above).

def get_settings(settings_id: str):
    items = list(
        container().query_items(
            "SELECT * FROM c WHERE c.id = @id",
            parameters=[{"name": "@id", "value": settings_id}],
            partition_key="_settings",
        )
    )
    return items[0] if items else None


def save_settings(doc: dict):
    doc["csp"] = "_settings"
    container().upsert_item(doc)


def delete_settings(settings_id: str):
    try:
        container().delete_item(item=settings_id, partition_key="_settings")
    except exceptions.CosmosResourceNotFoundError:
        pass
