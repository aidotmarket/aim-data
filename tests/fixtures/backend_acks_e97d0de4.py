"""e97d0de4: fulfillment_listener_service.py:451; trust_websocket.py:1332-1344.
TrustActionResponse defaults from app/schemas/trust.py:314-323.
Synthetic token values; the envelope and result fields are server-exact.
"""


def complete_ack(request_id):
    return {
        "request_id": request_id,
        "success": True,
        "data": {"success": True, "token_id": "00000000-0000-4000-8000-000000000001",
                 "download_token": "synthetic-download-token"},
        "error": None,
        "needs_confirmation": False,
        "confirmation_prompt": None,
        "audit_log_id": None,
    }


def response_ack(request_id):
    """e97d0de4 fulfillment_service.py:280, wrapped by TrustActionResponse."""
    return {**complete_ack(request_id), "data": {"success": True, "status": "delivered"}}
