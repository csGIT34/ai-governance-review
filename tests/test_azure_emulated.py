"""Managed identity + Blob over OAuth against the Floci AZ emulator, through the real app.

Skipped unless FLOCI_AZ_URL is set. Run with `make test-azure-emulated` (starts Floci in TLS
mode: the Azure SDK only sends bearer tokens over HTTPS).
"""
import os
import re
import subprocess
import uuid
from urllib.parse import urlparse

import pytest

from app import blob, config
from app.blob import download as real_download, upload as real_upload

FLOCI = os.environ.get("FLOCI_AZ_URL", "")
pytestmark = pytest.mark.skipif(not FLOCI, reason="set FLOCI_AZ_URL (see make test-azure-emulated)")


@pytest.fixture
def floci(monkeypatch, tmp_path):
    u = urlparse(FLOCI)
    out = subprocess.run(["openssl", "s_client", "-connect", f"{u.hostname}:{u.port}",
                          "-servername", u.hostname, "-showcerts"],
                         input=b"", capture_output=True, timeout=20).stdout
    chain = re.findall(rb"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", out, re.S)
    bundle = tmp_path / "floci-chain.pem"  # the whole self-signed chain, not just the leaf
    bundle.write_bytes(b"\n".join(chain) + b"\n")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(bundle))
    # azure-identity's IMDS (VM-style managed identity) endpoint override
    monkeypatch.setenv("AZURE_POD_IDENTITY_AUTHORITY_HOST", FLOCI.replace("https://", "http://"))
    monkeypatch.setattr(config, "BLOB_ACCOUNT_URL", f"{FLOCI}/devstoreaccount1")
    monkeypatch.setattr(config, "BLOB_CONTAINER", f"it-{uuid.uuid4().hex[:8]}")
    monkeypatch.setattr(blob, "_container", None)
    monkeypatch.setattr(blob, "upload", real_upload)  # undo conftest's in-memory blobs
    monkeypatch.setattr(blob, "download", real_download)
    yield
    blob._container = None


def test_artifact_roundtrip_with_managed_identity(client, floci):
    resp = client.post("/assessments/ai", data={"csp": "azure", "service_name": "Azure OpenAI",
                                                "model_name": "gpt-4.1"}, follow_redirects=False)
    aid = resp.headers["location"].rsplit("/", 1)[-1]
    client.post(f"/assessments/{aid}/artifacts",
                files={"file": ("pricing.txt", b"stored via managed identity", "text/plain")})
    assert client.get("/artifacts/1").content == b"stored via managed identity"
    with pytest.raises(Exception):  # write-once holds against the real SDK too
        blob.upload(blob._container_client().list_blob_names().next(), b"x", "text/plain")
