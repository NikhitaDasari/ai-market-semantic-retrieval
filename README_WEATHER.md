# Weather Intelligence: Unstructured Weather Data to Lakebase Vector Search

## Overview

This project extends the Databricks Lakebase application pattern from the Day 2 reference application to a new unstructured data source: weather.

The pipeline ingests narrative weather data from the National Weather Service API, stores the normalized documents in Databricks Lakebase, generates vector embeddings for the weather text using Sentence Transformers, and exposes semantic search through a Flask REST API.

The end-to-end flow is:

```text
National Weather Service API
        ↓
weather_client.py
        ↓
POST /weather/sync
        ↓
weather_documents
        ↓
ingest_weather_embeddings.py
        ↓
weather_embeddings
        ↓
POST /weather/search
        ↓
Semantic weather search results
```

A user can search using natural-language queries such as:

```text
flash flood risk this weekend
```

and retrieve the weather document chunks that are most semantically similar to that query.

---

## Data Source

### National Weather Service API

I chose the National Weather Service API (`api.weather.gov`) because it:

* Is free and does not require an API key
* Provides structured weather metadata along with rich free-text narratives
* Provides both forecast and active alert data
* Is well suited for demonstrating embedding and semantic retrieval over unstructured text

The application currently ingests:

* Multi-day forecast periods using the NWS forecast endpoint
* Active weather alerts for a geographic point

For forecasts, the `detailedForecast` field is used as the main narrative text.

For alerts, the `description` and `instruction` fields are combined into a single narrative so that both the weather condition and recommended actions are searchable.

### Location Input

The application accepts location name and latitude/longitude pairs, for example:

```json
{
  "name": "Chicago, IL",
  "lat": 41.8781,
  "lon": -87.6298
}
```

The NWS `/points/{lat},{lon}` endpoint is then used to resolve the corresponding NWS forecast grid.

Latitude/longitude input was used instead of adding a geocoding dependency so the homework pipeline remains focused on weather ingestion, vectorization, and retrieval.

---

## Project Structure

```text
app.py
weather_client.py
lakebase.py
requirements.txt
app.yaml

templates/
    index.html
    weather_search.html

notebooks/
    ingest_weather_embeddings.py

README_WEATHER.md
```

### `weather_client.py`

Handles communication with the National Weather Service API.

Responsibilities include:

* Resolving latitude/longitude to an NWS forecast grid
* Fetching multi-day forecasts
* Fetching active alerts
* Normalizing raw API responses into a common weather-document schema

### `app.py`

Contains the Flask REST application.

Main endpoints:

```text
POST /weather/sync
POST /weather/search
```

It also provides simple browser pages for syncing and searching weather data.

### `lakebase.py`

Provides the shared Lakebase/Postgres connection utilities using `psycopg2`.

### `ingest_weather_embeddings.py`

Reads unembedded weather documents, chunks the narrative text, generates embeddings, and writes them to Lakebase using `psycopg2`.

No Spark JDBC write path is used.

---

# Database Schema

## `weather_documents`

Stores normalized weather information before embedding.

```text
id
location
source_type
headline
narrative_text
issued_at
effective_at
payload
synced_at
```

### Schema decisions

`id`

A stable deduplication key.

NWS alert IDs are reused when available. Forecast IDs are generated deterministically from location and forecast-period information.

`location`

Human-readable location such as:

```text
Chicago, IL
```

`source_type`

Identifies the type of weather document:

```text
forecast
alert
```

`headline`

Examples include:

```text
Monday
Monday Night
Flash Flood Warning
Severe Thunderstorm Warning
```

`narrative_text`

The primary unstructured text used for embeddings.

For forecasts this is the NWS `detailedForecast`.

For alerts this combines the alert description and instruction text.

`payload`

The original NWS JSON object is stored as JSONB for provenance and future debugging or reprocessing.

---

## `weather_embeddings`

Stores chunk-level vector embeddings.

```text
id
document_id
chunk_index
chunk_text
embedding
model_name
created_at
```

The `document_id` column references:

```text
weather_documents.id
```

This creates a one-to-many relationship:

```text
weather document
    ↓
chunk 0
chunk 1
chunk 2
...
```

---

# Embedding Model

The project uses:

```text
sentence-transformers/all-MiniLM-L6-v2
```

Embedding dimension:

```text
384
```

The Lakebase column therefore uses:

```sql
VECTOR(384)
```

The same model is used for both document ingestion and search-query embeddings so the vectors remain compatible.

---

# Chunking Strategy

The weather narrative is split using a sliding-window chunking approach.

```text
CHUNK_SIZE = 800 characters
CHUNK_OVERLAP = 100 characters
```

For example:

```text
Chunk 0: characters 0-799
Chunk 1: characters 700-1499
Chunk 2: characters 1400-...
```

The overlap helps preserve context that might otherwise be divided across chunk boundaries.

Most NWS forecast narratives are short enough to fit in a single chunk.

Longer alert descriptions and instructions may produce multiple chunks.

---

# Vector Search

The application uses the pgvector cosine-distance operator:

```sql
<=>
```

Search results are ordered by cosine distance.

Similarity is returned as:

```sql
1 - (embedding <=> query_vector)
```

so higher values indicate greater semantic similarity.

The embedding table uses an HNSW index with cosine-vector operations:

```sql
CREATE INDEX IF NOT EXISTS idx_weather_embeddings_hnsw
ON weather_embeddings
USING hnsw (embedding vector_cosine_ops);
```

---

# REST API

## Sync Weather

### Endpoint

```text
POST /weather/sync
```

### Example request

```json
{
  "locations": [
    {
      "name": "Chicago, IL",
      "lat": 41.8781,
      "lon": -87.6298
    },
    {
      "name": "Austin, TX",
      "lat": 30.2672,
      "lon": -97.7431
    }
  ],
  "limit": 50
}
```

### Example response

```json
{
  "synced": 20,
  "locations": [
    "Chicago, IL",
    "Austin, TX"
  ]
}
```

The endpoint:

1. Calls the National Weather Service API
2. Fetches forecasts and active alerts
3. Normalizes the records
4. Upserts them into `weather_documents`

Existing document IDs are updated rather than duplicated.

---

## Semantic Weather Search

### Endpoint

```text
POST /weather/search
```

### Example request

```json
{
  "query": "flash flood risk this weekend",
  "top_k": 5
}
```

### Example response structure

```json
{
  "query": "flash flood risk this weekend",
  "top_k": 5,
  "results": [
    {
      "location": "Chicago, IL",
      "source_type": "forecast",
      "headline": "Sunday",
      "chunk_text": "Weather narrative...",
      "similarity": 0.62
    }
  ]
}
```

`top_k` is constrained to a reasonable range of 1–20.

The endpoint also validates:

* Missing query
* Empty query
* Invalid `top_k`
* Empty embedding table

---

# Running the Pipeline

## 1. Install dependencies

From the project environment:

```bash
pip install -r requirements.txt
```

The main dependencies include:

```text
flask
requests
psycopg2-binary
sqlalchemy
sentence-transformers
torch
numpy
databricks-sdk
```

---

## 2. Configure Lakebase

The application uses the same Lakebase connection pattern as the reference application.

The Postgres connection URL is stored in the Databricks secret:

```text
scope: database
key: lakebase-url
```

`lakebase.py` retrieves and decodes this connection URL at runtime.

---

## 3. Deploy the Databricks App

Deploy the project containing:

```text
app.py
app.yaml
requirements.txt
weather_client.py
lakebase.py
templates/
```

After deployment, verify the application health endpoint:

```text
/healthz
```

Expected response:

```json
{
  "status": "ok"
}
```

---

## 4. Sync Weather Data

Open the application's sync page or call:

```text
POST /weather/sync
```

Example:

```json
{
  "locations": [
    {
      "name": "Chicago, IL",
      "lat": 41.8781,
      "lon": -87.6298
    }
  ],
  "limit": 20
}
```

Verify that records are present in:

```text
weather_documents
```

---

## 5. Generate Embeddings

Run:

```text
notebooks/ingest_weather_embeddings.py
```

The script:

1. Finds weather documents without existing embeddings
2. Splits their narrative text into overlapping chunks
3. Loads `all-MiniLM-L6-v2`
4. Generates 384-dimensional embeddings
5. Writes the vectors to `weather_embeddings` using `psycopg2`

The script is idempotent for already-embedded documents.

Re-running it after no new weather data has been synced should result in no additional documents being processed.

---

## 6. Search Weather Data

After embeddings have been generated, open the semantic-search page or call:

```text
POST /weather/search
```

Example:

```json
{
  "query": "chance of thunderstorms",
  "top_k": 5
}
```

The query is embedded using the same Sentence Transformer model used during ingestion.

Lakebase then retrieves the nearest weather chunks using pgvector cosine similarity.

---

# Browser UI

Two simple HTML pages are included.

### Weather Sync

```text
/
```

Allows users to enter:

* Location name
* Latitude
* Longitude

and trigger the weather sync pipeline.

### Semantic Search

```text
/search
```

Allows users to enter natural-language weather queries and view ranked semantic-search results.

---

# Deduplication

Weather documents use stable IDs and are written with Postgres upsert logic.

Repeated calls to:

```text
POST /weather/sync
```

therefore update existing weather records instead of creating duplicate documents.

Embeddings are uniquely identified by:

```text
document_id + chunk_index
```

which prevents duplicate chunk embeddings.

---

# Known Limitations

### Location geocoding

The current implementation requires latitude and longitude to be supplied with the location name.

A future version could add a geocoding service so users can provide only:

```text
Chicago, IL
```

### Weather data coverage

Only active alerts and NWS forecast narratives are currently ingested.

Additional NWS sources such as hourly forecasts or forecast discussions could provide richer semantic context.

### Embedding execution

Embedding is currently a separate batch process rather than automatically triggered after `/weather/sync`.

A production implementation could use a Databricks Job or workflow to automatically embed newly ingested weather documents.

### Stale embeddings

The current ingestion strategy considers a document embedded once an embedding exists for its document ID.

If an existing document's narrative text changes while retaining the same ID, a production implementation should detect that change and regenerate its embeddings.

### Small dataset

This homework operates on a relatively small number of weather documents.

The HNSW index becomes much more valuable at larger scale.

---

# Future Improvements

Given more time, I would:

* Add automatic city/state geocoding
* Schedule weather synchronization using a Databricks Job
* Automatically trigger embedding ingestion after new documents arrive
* Add filters for `location` and `source_type`
* Include hourly forecast narratives
* Add NOAA/NWS forecast discussion products
* Add embedding refresh logic when source documents change
* Add LLM-generated summaries over the retrieved weather chunks
* Benchmark vector-search performance with and without the HNSW index
* Add richer validation and API-level automated tests

---

# Summary

This project demonstrates an end-to-end unstructured-data retrieval pipeline using Databricks Lakebase:

```text
Weather API
→ normalized documents
→ chunking
→ sentence embeddings
→ pgvector
→ cosine similarity
→ Flask REST API
```

The same architecture can be generalized beyond weather to other unstructured sources such as support tickets, reports, articles, documentation, or operational event data.
