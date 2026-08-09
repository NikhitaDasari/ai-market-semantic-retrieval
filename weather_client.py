"""
Client for the National Weather Service API.

NWS does not require an API key. The client accepts latitude/longitude
coordinates and uses the NWS /points endpoint to resolve forecast grid data.
"""

import os
from typing import Any

import requests
import hashlib

_NWS_BASE_URL = os.environ.get("NWS_API_BASE_URL", "https://api.weather.gov")


_DEFAULT_TIMEOUT = 30


class WeatherClient:
    """Thin wrapper around the NWS API for weather alerts and forecasts."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: int = _DEFAULT_TIMEOUT,
    ):
        self.base_url = (base_url or _NWS_BASE_URL).rstrip("/")
        self.timeout = timeout

        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": "databricks-weather-intelligence-homework",
                "Accept": "application/geo+json",
            }
        )

    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        resp = self._session.get(
            f"{self.base_url}{path}",
            params=params,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

   
    def get_point(self, latitude: float, longitude: float) -> dict:
        """
        Resolve latitude/longitude to an NWS forecast grid point.
        """
        return self.get(f"/points/{latitude},{longitude}")

    def get_forecast(self, latitude: float, longitude: float) -> dict:
        """
        Fetch the multi-day forecast for a latitude/longitude point.
        """
        point_data = self.get_point(latitude, longitude)
        forecast_url = point_data["properties"]["forecast"]

        resp = self._session.get(
            forecast_url,
            timeout=self.timeout,
        )
        resp.raise_for_status()

        return resp.json()

    def get_alerts(self, latitude: float, longitude: float) -> list[dict]:
        """
        Fetch active weather alerts for a latitude/longitude point.
        """
        data = self.get(
            "/alerts/active",
            params={"point": f"{latitude},{longitude}"},
        )

        return data.get("features", [])
    
    def get_weather_documents(
        self,
        location_name: str,
        latitude: float,
        longitude: float,
        limit: int = 50,
    ) -> list[dict]:
        """
        Fetch forecasts and active alerts for a location and normalize them
        into a common weather-document schema.
        """
        documents = []

        # -------------------------
        # Forecast documents
        # -------------------------
        forecast_data = self.get_forecast(latitude, longitude)
        forecast_properties = forecast_data.get("properties", {})
        periods = forecast_properties.get("periods", [])

        for period in periods:
            narrative_text = period.get("detailedForecast") or ""

            if not narrative_text.strip():
                continue

            start_time = period.get("startTime")

            raw_id = (
                f"{location_name}|forecast|"
                f"{period.get('number')}|{start_time}"
            )

            document_id = hashlib.sha256(
                raw_id.encode("utf-8")
            ).hexdigest()

            documents.append(
                {
                    "id": document_id,
                    "location": location_name,
                    "source_type": "forecast",
                    "headline": period.get("name"),
                    "narrative_text": narrative_text,
                    "issued_at": forecast_properties.get("generatedAt"),
                    "effective_at": start_time,
                    "payload": period,
                }
            )

        # -------------------------
        # Alert documents
        # -------------------------
        alerts = self.get_alerts(latitude, longitude)

        for alert in alerts:
            properties = alert.get("properties", {})

            description = properties.get("description") or ""
            instruction = properties.get("instruction") or ""

            narrative_parts = []

            if description.strip():
                narrative_parts.append(description.strip())

            if instruction.strip():
                narrative_parts.append(instruction.strip())

            narrative_text = "\n\n".join(narrative_parts)

            if not narrative_text:
                continue

            # NWS alerts normally provide a stable ID.
            alert_id = alert.get("id") or properties.get("id")

            if alert_id:
                document_id = str(alert_id)

            else:
                raw_id = (
                    f"{location_name}|alert|"
                    f"{properties.get('event')}|"
                    f"{properties.get('sent')}"
                )

                document_id = hashlib.sha256(
                    raw_id.encode("utf-8")
                ).hexdigest()

            documents.append(
                {
                    "id": document_id,
                    "location": location_name,
                    "source_type": "alert",
                    "headline": properties.get("event"),
                    "narrative_text": narrative_text,
                    "issued_at": properties.get("sent"),
                    "effective_at": properties.get("effective"),
                    "payload": alert,
                }
            )

        return documents[:limit]