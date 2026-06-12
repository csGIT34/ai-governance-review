import os

# Cosmos DB (defaults target the local emulator; override in Azure)
COSMOS_ENDPOINT = os.environ.get("COSMOS_ENDPOINT", "http://localhost:8081")
COSMOS_KEY = os.environ.get(
    "COSMOS_KEY",
    # Well-known Cosmos DB emulator key (not a secret)
    "C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw==",
)
COSMOS_DATABASE = os.environ.get("COSMOS_DATABASE", "governance")
COSMOS_CONTAINER = os.environ.get("COSMOS_CONTAINER", "reviews")

# Blob storage (defaults target Azurite; override in Azure)
BLOB_CONNECTION_STRING = os.environ.get(
    "BLOB_CONNECTION_STRING",
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
    # Well-known Azurite key (not a secret)
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://localhost:10000/devstoreaccount1;",
)
BLOB_CONTAINER = os.environ.get("BLOB_CONTAINER", "artifacts")

# Enrichment (optional)
AZURE_SUBSCRIPTION_ID = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
GOOGLE_CLOUD_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")

# Per-CSP reference docs to snapshot (URLs, item mappings, standard positions).
# Point at a custom copy to change a doc URL or add docs without a code change.
REFERENCE_DOCS_PATH = os.environ.get(
    "REFERENCE_DOCS_PATH",
    os.path.join(os.path.dirname(__file__), "enrichment", "reference_docs.json"),
)
