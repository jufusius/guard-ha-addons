"""S-therm MQTT client — Cognito SRP auth + AWS IoT MQTT."""

import asyncio
import hashlib
import hmac
import json
import logging
import ssl
import time
import urllib.parse
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from .const import (
    AWS_REGION,
    COGNITO_CLIENT_ID,
    COGNITO_IDENTITY_POOL_ID,
    COGNITO_USER_POOL_ID,
    IOT_ENDPOINT,
    READ_PARAMS,
)

_LOGGER = logging.getLogger(__name__)


class SthermClient:
    """Client for S-therm heat pump via AWS IoT MQTT."""

    def __init__(self, username: str, password: str, installation_id: str) -> None:
        self._username = username
        self._password = password
        self._installation_id = installation_id
        self._component_id: str | None = None

        #CC- Auth state
        self._id_token: str | None = None
        self._identity_id: str | None = None
        self._access_key: str | None = None
        self._secret_key: str | None = None
        self._session_token: str | None = None

        #CC- MQTT state
        self._mqtt_client: mqtt.Client | None = None
        self._session_topic: str | None = None
        self._connected = False
        self._pending_response: asyncio.Future | None = None
        self._transaction_counter = 0

        #CC- Cached values
        self.values: dict[str, list[float]] = {}
        self.last_update: datetime | None = None

        #CC- Callbacks
        self._on_update: list = []

    @property
    def component_id(self) -> str | None:
        return self._component_id

    @component_id.setter
    def component_id(self, value: str) -> None:
        self._component_id = value

    def on_update(self, callback) -> None:
        """Register callback for data updates."""
        self._on_update.append(callback)

    # ==================== Authentication ====================

    async def authenticate(self) -> None:
        """Authenticate via Cognito SRP + get AWS IoT credentials."""
        import boto3
        from pycognito import Cognito

        _LOGGER.info("S-therm: Authenticating via Cognito SRP...")
        loop = asyncio.get_event_loop()

        #CC- Step 1: Cognito SRP auth
        u = Cognito(
            COGNITO_USER_POOL_ID,
            COGNITO_CLIENT_ID,
            username=self._username,
        )
        await loop.run_in_executor(None, u.authenticate, self._password)
        self._id_token = u.id_token
        _LOGGER.info("S-therm: Cognito auth successful")

        #CC- Step 2: Identity Pool → AWS credentials
        identity_client = boto3.client("cognito-identity", region_name=AWS_REGION)

        id_resp = await loop.run_in_executor(
            None,
            lambda: identity_client.get_id(
                IdentityPoolId=COGNITO_IDENTITY_POOL_ID,
                Logins={
                    f"cognito-idp.{AWS_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}": self._id_token
                },
            ),
        )
        self._identity_id = id_resp["IdentityId"]

        creds_resp = await loop.run_in_executor(
            None,
            lambda: identity_client.get_credentials_for_identity(
                IdentityId=self._identity_id,
                Logins={
                    f"cognito-idp.{AWS_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}": self._id_token
                },
            ),
        )
        c = creds_resp["Credentials"]
        self._access_key = c["AccessKeyId"]
        self._secret_key = c["SecretKey"]
        self._session_token = c["SessionToken"]
        _LOGGER.info("S-therm: AWS credentials obtained, identity=%s", self._identity_id)

    # ==================== MQTT ====================

    async def connect_mqtt(self) -> None:
        """Connect to AWS IoT MQTT and subscribe to topics."""
        ts = int(time.time() * 1000)
        self._session_topic = f"{self._installation_id}/{self._identity_id}-{ts}"
        client_id = f"{self._identity_id}-{ts}"

        ws_path = self._build_sigv4_ws_path()

        self._mqtt_client = mqtt.Client(
            client_id=client_id,
            transport="websockets",
            protocol=mqtt.MQTTv31,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        self._mqtt_client.tls_set(cert_reqs=ssl.CERT_NONE)
        self._mqtt_client.ws_set_options(
            path=ws_path,
            headers={"Sec-WebSocket-Protocol": "mqtt"},
        )

        loop = asyncio.get_event_loop()
        connect_future = loop.create_future()

        def on_connect(client, userdata, flags, reason_code, properties=None):
            _LOGGER.info("S-therm: MQTT connected (rc=%s)", reason_code)
            self._connected = True
            client.subscribe(f"{self._session_topic}/installationResponse", 0)
            client.subscribe(f"{self._installation_id}/installationNotifications", 0)
            if not connect_future.done():
                loop.call_soon_threadsafe(connect_future.set_result, True)

        def on_message(client, userdata, msg):
            try:
                data = json.loads(msg.payload)
                if msg.topic.endswith("/installationResponse"):
                    if self._pending_response and not self._pending_response.done():
                        loop.call_soon_threadsafe(self._pending_response.set_result, data)
                elif msg.topic.endswith("/installationNotifications"):
                    self._handle_params_update(data)
            except Exception as ex:
                _LOGGER.warning("S-therm: Error processing message: %s", ex)

        def on_disconnect(client, userdata, flags, reason_code, properties=None):
            _LOGGER.warning("S-therm: MQTT disconnected (rc=%s)", reason_code)
            self._connected = False

        self._mqtt_client.on_connect = on_connect
        self._mqtt_client.on_message = on_message
        self._mqtt_client.on_disconnect = on_disconnect

        await loop.run_in_executor(
            None, self._mqtt_client.connect, IOT_ENDPOINT, 443
        )
        self._mqtt_client.loop_start()

        await asyncio.wait_for(connect_future, timeout=15)

    def disconnect(self) -> None:
        """Disconnect MQTT."""
        if self._mqtt_client:
            self._mqtt_client.loop_stop()
            self._mqtt_client.disconnect()
            self._connected = False

    # ==================== Data Operations ====================

    async def discover_components(self) -> list[dict]:
        """Get components on the bus (discover heat pump)."""
        resp = await self._mqtt_request({
            "transactionId": str(self._next_tid()),
            "operations": [{"name": "GET_COMPONENTS_ON_BUS"}],
        })
        components = []
        for op in resp.get("operations", []):
            for target in op.get("targets", []):
                components.append({
                    "id": target["component"],
                    "name": target.get("parameters", {}).get("componentName", "?"),
                    "hardware": target.get("parameters", {}).get("hardwareVersion", "?"),
                    "software": target.get("parameters", {}).get("programSeries", "?"),
                })
        return components

    async def get_values(self, params: list[str] | None = None) -> dict[str, list[float]]:
        """Read parameter values from heat pump."""
        if not self._component_id:
            raise RuntimeError("Component ID not set")

        resp = await self._mqtt_request({
            "transactionId": str(self._next_tid()),
            "operations": [{
                "name": "GET_VALUES",
                "targets": [{
                    "component": self._component_id,
                    "parameters": params or READ_PARAMS,
                }],
            }],
        })

        for op in resp.get("operations", []):
            for target in op.get("targets", []):
                params_data = target.get("parameters", {})
                if isinstance(params_data, dict):
                    for k, v in params_data.items():
                        self.values[k] = v

        self.last_update = datetime.now(timezone.utc)
        self._notify_update()
        return self.values

    async def set_parameter(self, param_code: str, value: float) -> bool:
        """Write a parameter value to heat pump."""
        if not self._component_id:
            raise RuntimeError("Component ID not set")

        resp = await self._mqtt_request({
            "transactionId": str(self._next_tid()),
            "operations": [{
                "name": "PARAMS_MODIFICATION",
                "targets": [{
                    "component": self._component_id,
                    "parameters": {param_code: [value]},
                }],
            }],
        })

        status = resp.get("operations", [{}])[0].get("statusCode", -1)
        _LOGGER.info("S-therm: SetParameter %s=%s, status=%s", param_code, value, status)
        return status == 0

    async def async_setup(self) -> None:
        """Full setup: auth → MQTT → discover → initial read."""
        await self.authenticate()
        await self.connect_mqtt()

        #CC- Discover heat pump component
        components = await self.discover_components()
        for comp in components:
            if comp["name"] == "HEAT PUMP" or "-" in comp["id"]:
                self._component_id = comp["id"]
                _LOGGER.info("S-therm: Found heat pump: %s (%s)", comp["id"], comp["name"])
                break

        if not self._component_id:
            raise RuntimeError("No heat pump component found")

        #CC- Initial data read
        await self.get_values()

    # ==================== Internal ====================

    def _handle_params_update(self, data: dict) -> None:
        """Handle PARAMS_UPDATE notification (real-time push)."""
        for msg in data.get("messages", []):
            if msg.get("messageType") != "PARAMS_UPDATE":
                continue
            for target in msg.get("targets", []):
                params = target.get("parameters", {})
                for k, v in params.items():
                    self.values[k] = v
        self.last_update = datetime.now(timezone.utc)
        self._notify_update()

    def _notify_update(self) -> None:
        for cb in self._on_update:
            try:
                cb()
            except Exception:
                pass

    async def _mqtt_request(self, payload: dict, timeout: float = 15) -> dict:
        """Send MQTT request and wait for response."""
        loop = asyncio.get_event_loop()
        self._pending_response = loop.create_future()

        topic = f"{self._session_topic}/installationRequest"
        self._mqtt_client.publish(topic, json.dumps(payload))

        try:
            return await asyncio.wait_for(self._pending_response, timeout=timeout)
        except asyncio.TimeoutError:
            _LOGGER.warning("S-therm: MQTT request timeout")
            raise

    def _next_tid(self) -> int:
        self._transaction_counter += 1
        return self._transaction_counter

    def _build_sigv4_ws_path(self) -> str:
        """Build SigV4 presigned WebSocket path for AWS IoT."""
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        service = "iotdevicegateway"
        credential_scope = f"{date_stamp}/{AWS_REGION}/{service}/aws4_request"

        canonical_qs = f"X-Amz-Algorithm=AWS4-HMAC-SHA256"
        canonical_qs += f"&X-Amz-Credential={urllib.parse.quote(f'{self._access_key}/{credential_scope}', safe='')}"
        canonical_qs += f"&X-Amz-Date={amz_date}"
        canonical_qs += f"&X-Amz-Expires=86400"
        canonical_qs += f"&X-Amz-SignedHeaders=host"

        canonical_request = (
            f"GET\n/mqtt\n{canonical_qs}\nhost:{IOT_ENDPOINT}\n\nhost\n"
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )
        string_to_sign = (
            f"AWS4-HMAC-SHA256\n{amz_date}\n{credential_scope}\n"
            f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
        )

        def _sign(key, msg):
            return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

        signing_key = _sign(
            _sign(
                _sign(
                    _sign(f"AWS4{self._secret_key}".encode(), date_stamp),
                    AWS_REGION,
                ),
                service,
            ),
            "aws4_request",
        )
        signature = hmac.new(
            signing_key, string_to_sign.encode(), hashlib.sha256
        ).hexdigest()

        return (
            f"/mqtt?{canonical_qs}"
            f"&X-Amz-Signature={signature}"
            f"&X-Amz-Security-Token={urllib.parse.quote(self._session_token, safe='')}"
        )
