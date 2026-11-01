from temporalio.client import Client

from company_check.config import get_settings

_client: Client | None = None


async def get_temporal_client() -> Client:
    """Process-wide Temporal client (connection is reused; connect lazily)."""
    global _client
    if _client is None:
        s = get_settings()
        _client = await Client.connect(s.temporal_address, namespace=s.temporal_namespace)
    return _client
