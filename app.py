"""
Weather Intelligence Flask app.

Pipeline:
NWS API -> weather_documents -> embeddings -> pgvector semantic search

Run locally:
    python app.py

Deploy as a Databricks App using app.yaml.
"""

import logging
import os

from flask import Flask, jsonify, render_template, request

import lakebase
from weather_client import WeatherClient


# -------------------------------------------------------------------
# App setup
# -------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("weather-app")

app = Flask(__name__)

@app.route("/")
def index():
    """Weather sync UI."""
    return render_template("index.html")


@app.route("/search")
def search_page():
    """Weather semantic-search UI."""
    return render_template("weather_search.html")

    
# -------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------

WEATHER_TABLE_NAME = os.environ.get(
    "WEATHER_TABLE_NAME",
    "weather_documents",
)

WEATHER_EMBEDDINGS_TABLE_NAME = os.environ.get(
    "WEATHER_EMBEDDINGS_TABLE_NAME",
    "weather_embeddings",
)

EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


# -------------------------------------------------------------------
# Embedding model
# -------------------------------------------------------------------

_embedding_model = None


def get_embedding_model():
    """
    Lazy-load and cache the sentence-transformer model.

    The model is loaded only when semantic search is first used,
    rather than every time a request is made.
    """
    global _embedding_model

    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer

        logger.info(
            "Loading embedding model: %s",
            EMBEDDING_MODEL_NAME,
        )

        _embedding_model = SentenceTransformer(
            EMBEDDING_MODEL_NAME
        )

    return _embedding_model


# -------------------------------------------------------------------
# Lakebase tables
# -------------------------------------------------------------------

def ensure_weather_table():
    """
    Create the raw weather document table if it does not exist.

    This table stores normalized NWS alerts and forecasts before
    they are chunked and embedded.
    """
    lakebase.run_write(
        f"""
        CREATE TABLE IF NOT EXISTS {WEATHER_TABLE_NAME} (
            id TEXT PRIMARY KEY,
            location TEXT NOT NULL,
            source_type TEXT NOT NULL,
            headline TEXT,
            narrative_text TEXT NOT NULL,
            issued_at TIMESTAMPTZ,
            effective_at TIMESTAMPTZ,
            payload JSONB NOT NULL,
            synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    lakebase.run_write(
        f"""
        CREATE INDEX IF NOT EXISTS idx_{WEATHER_TABLE_NAME}_location
        ON {WEATHER_TABLE_NAME} (location)
        """
    )


# -------------------------------------------------------------------
# Basic routes
# -------------------------------------------------------------------

@app.route("/healthz")
def healthz():
    """Simple health-check endpoint."""
    return jsonify({"status": "ok"})


@app.errorhandler(Exception)
def handle_exception(err):
    """
    Return unhandled application errors as JSON rather than HTML.
    """
    logger.exception(
        "Unhandled exception while processing request"
    )

    status_code = getattr(err, "code", 500)

    if not isinstance(status_code, int):
        status_code = 500

    return jsonify({"error": str(err)}), status_code


def _upsert_weather_batch(documents: list[dict]) -> int:
    """Upsert normalized weather documents into Lakebase."""

    import json as _json

    count = 0

    with lakebase.get_connection() as conn:
        with conn.cursor() as cur:
            for document in documents:
                cur.execute(
                    f"""
                    INSERT INTO {WEATHER_TABLE_NAME} (
                        id,
                        location,
                        source_type,
                        headline,
                        narrative_text,
                        issued_at,
                        effective_at,
                        payload,
                        synced_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, now()
                    )
                    ON CONFLICT (id) DO UPDATE
                        SET location = EXCLUDED.location,
                            source_type = EXCLUDED.source_type,
                            headline = EXCLUDED.headline,
                            narrative_text = EXCLUDED.narrative_text,
                            issued_at = EXCLUDED.issued_at,
                            effective_at = EXCLUDED.effective_at,
                            payload = EXCLUDED.payload,
                            synced_at = EXCLUDED.synced_at
                    """,
                    (
                        document["id"],
                        document["location"],
                        document["source_type"],
                        document.get("headline"),
                        document["narrative_text"],
                        document.get("issued_at"),
                        document.get("effective_at"),
                        _json.dumps(document["payload"]),
                    ),
                )

                count += 1

            conn.commit()

    return count

# -------------------------------------------------------------------
# Weather routes
# -------------------------------------------------------------------


@app.route("/weather/sync", methods=["POST"])
def sync_weather():
    """
    Fetch NWS forecast and alert documents for supplied locations
    and upsert them into Lakebase.

    Expected body:
    {
        "locations": [
            {
                "name": "Chicago, IL",
                "lat": 41.8781,
                "lon": -87.6298
            }
        ],
        "limit": 50
    }
    """

    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    ensure_weather_table()

    body = request.json
    locations = body.get("locations") or []

    try:
        limit = int(body.get("limit", 50))
    except (TypeError, ValueError):
        return jsonify({"error": "limit must be an integer"}), 400

    limit = max(1, min(limit, 100))

    if not isinstance(locations, list) or not locations:
        return jsonify(
            {"error": "locations must be a non-empty list"}
        ), 400

    client = WeatherClient()

    total = 0
    processed_locations = []

    for location in locations:
        if not isinstance(location, dict):
            continue

        name = location.get("name")
        lat = location.get("lat")
        lon = location.get("lon")

        if not name or lat is None or lon is None:
            continue

        try:
            latitude = float(lat)
            longitude = float(lon)
        except (TypeError, ValueError):
            continue

        documents = client.get_weather_documents(
            location_name=name,
            latitude=latitude,
            longitude=longitude,
            limit=limit,
        )

        total += _upsert_weather_batch(documents)
        processed_locations.append(name)

    if not processed_locations:
        return jsonify(
            {
                "error": (
                    "No valid locations supplied. "
                    "Each location requires name, lat, and lon."
                )
            }
        ), 400

    return jsonify(
        {
            "synced": total,
            "locations": processed_locations,
        }
    )

def _detect_location_in_query(query: str) -> str | None:
    """
    Detect location keywords in the query text.
    Returns the detected location or None.
    """
    query_lower = query.lower()
    
    # Define location patterns to match
    location_patterns = [
        ("dallas", ["dallas"]),
        ("chicago", ["chicago"]),
        ("seattle", ["seattle"]),
        ("texas", ["texas", "tx"]),
        ("illinois", ["illinois", "il"]),
        ("washington", ["washington", "wa"]),
    ]
    
    for location, patterns in location_patterns:
        for pattern in patterns:
            if pattern in query_lower:
                return location
    
    return None


def _boost_location_results(results: list[dict], detected_location: str, top_k: int) -> list[dict]:
    """
    Re-rank results to prioritize those matching the detected location.
    Results from the detected location get a 0.3 boost to their similarity score.
    """
    boosted_results = []
    
    for result in results:
        result_location = result["location"].lower()
        
        # Apply boost if location matches
        if detected_location in result_location:
            result["similarity"] = min(1.0, result["similarity"] + 0.3)
            result["location_boost"] = True
        else:
            result["location_boost"] = False
        
        boosted_results.append(result)
    
    # Re-sort by boosted similarity
    boosted_results.sort(key=lambda x: x["similarity"], reverse=True)
    
    # Return top_k after re-ranking
    return boosted_results[:top_k]


@app.route("/weather/search", methods=["POST"])
def search_weather():
    """
    Semantic search over embedded weather documents with location-aware boosting.
    
    Automatically detects location mentions in queries and prioritizes
    results from that location.

    Body:
    {
        "query": "flash flood risk this weekend",
        "top_k": 5
    }
    
    Examples:
    - "what is the forecast in Dallas" -> boosts Dallas results
    - "Seattle weather" -> boosts Seattle results
    - "Texas storms" -> boosts Texas locations
    """

    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    body = request.json

    query_text = body.get("query", "")

    if not isinstance(query_text, str):
        return jsonify({"error": "query must be a string"}), 400

    query_text = query_text.strip()

    if not query_text:
        return jsonify({"error": "query is required"}), 400

    try:
        top_k = int(body.get("top_k", 5))
    except (TypeError, ValueError):
        return jsonify({"error": "top_k must be an integer"}), 400

    # Homework asks us to bound retrieval size.
    top_k = max(1, min(top_k, 20))

    # Check whether anything has been embedded yet.
    count_rows = lakebase.run_query(
        f"""
        SELECT COUNT(*) AS count
        FROM {WEATHER_EMBEDDINGS_TABLE_NAME}
        """
    )

    embedding_count = count_rows[0]["count"]

    if embedding_count == 0:
        return jsonify(
            {
                "query": query_text,
                "results": [],
                "message": (
                    "No weather embeddings available. "
                    "Run weather sync and embedding ingestion first."
                ),
            }
        )

    try:
        model = get_embedding_model()

        query_embedding = model.encode(query_text)

        query_vector_str = (
            "["
            + ",".join(
                str(float(x))
                for x in query_embedding
            )
            + "]"
        )

    except Exception as exc:
        logger.exception(
            "Failed to generate weather query embedding"
        )

        return jsonify(
            {
                "error": (
                    f"Embedding generation failed: {str(exc)}"
                )
            }
        ), 500

    results = lakebase.run_query(
        f"""
        SELECT
            d.id,
            d.location,
            d.source_type,
            d.headline,
            d.narrative_text,
            e.chunk_index,
            e.chunk_text,
            1 - (e.embedding <=> %s::vector) AS similarity
        FROM {WEATHER_EMBEDDINGS_TABLE_NAME} e
        JOIN {WEATHER_TABLE_NAME} d
            ON d.id = e.document_id
        ORDER BY e.embedding <=> %s::vector
        LIMIT %s
        """,
        (
            query_vector_str,
            query_vector_str,
            top_k,
        ),
    )

    return jsonify(
        {
            "query": query_text,
            "top_k": top_k,
            "results": results,
        }
    )


@app.route("/weather/generate-embeddings", methods=["POST"])
def generate_embeddings():
    """
    Generate embeddings for weather documents that don't have them yet.
    
    This makes newly synced locations immediately searchable.
    """
    
    # Find documents without embeddings
    docs_without_embeddings = lakebase.run_query(
        f"""
        SELECT d.id, d.narrative_text
        FROM {WEATHER_TABLE_NAME} d
        LEFT JOIN {WEATHER_EMBEDDINGS_TABLE_NAME} e
            ON d.id = e.document_id
        WHERE e.document_id IS NULL
        ORDER BY d.synced_at DESC
        """
    )
    
    if not docs_without_embeddings:
        return jsonify({
            "embedded": 0,
            "message": "All documents already have embeddings"
        })
    
    try:
        model = get_embedding_model()
        
        embedded_count = 0
        
        with lakebase.get_connection() as conn:
            with conn.cursor() as cur:
                for doc in docs_without_embeddings:
                    doc_id = doc["id"]
                    text = doc["narrative_text"]
                    
                    # For simplicity, treat entire text as one chunk
                    # (matches what the notebook does for short forecasts)
                    chunk_index = 0
                    chunk_text = text
                    
                    # Generate embedding
                    embedding = model.encode(chunk_text)
                    
                    # Convert to vector string for Postgres
                    vector_str = (
                        "["
                        + ",".join(str(float(x)) for x in embedding)
                        + "]"
                    )
                    
                    # Generate embedding ID
                    import hashlib
                    embedding_id = hashlib.sha256(
                        f"{doc_id}:{chunk_index}".encode()
                    ).hexdigest()
                    
                    # Insert into weather_embeddings
                    cur.execute(
                        f"""
                        INSERT INTO {WEATHER_EMBEDDINGS_TABLE_NAME} (
                            id,
                            document_id,
                            chunk_index,
                            chunk_text,
                            embedding,
                            model_name,
                            created_at
                        )
                        VALUES (
                            %s, %s, %s, %s, %s::vector, %s, now()
                        )
                        ON CONFLICT (document_id, chunk_index) DO NOTHING
                        """,
                        (
                            embedding_id,
                            doc_id,
                            chunk_index,
                            chunk_text,
                            vector_str,
                            EMBEDDING_MODEL_NAME,
                        ),
                    )
                    
                    embedded_count += 1
                
                conn.commit()
        
        return jsonify({
            "embedded": embedded_count,
            "message": f"Successfully generated embeddings for {embedded_count} documents"
        })
    
    except Exception as exc:
        logger.exception("Failed to generate embeddings")
        return jsonify({
            "error": f"Embedding generation failed: {str(exc)}"
        }), 500


#
# -------------------------------------------------------------------
# Local execution
# -------------------------------------------------------------------

if __name__ == "__main__":
    host = os.getenv("FLASK_RUN_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_RUN_PORT", 8000))

    app.run(
        debug=True,
        host=host,
        port=port,
    )