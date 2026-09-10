# AI Market Intelligence & Semantic Retrieval Platform

A Databricks-based AI data engineering project that combines **market-data ingestion, watchlist-driven news pipelines, distributed embedding generation, pgvector semantic retrieval, and a user-facing Databricks App**.

The platform turns external market/news data into retrieval-ready context for downstream RAG and agent workflows.

**Tech:** Databricks Apps · Databricks Workflows · Asset Bundles · Lakebase/PostgreSQL · Spark · pandas UDFs · Sentence Transformers · pgvector · Python · Flask · Massive API · trafilatura

---

## What this project demonstrates

- External REST API ingestion with pagination and rate-limit handling
- User-specific watchlists backed by Databricks Lakebase/PostgreSQL
- Scheduled Databricks Workflows defined through Asset Bundles
- Structured and unstructured news ingestion
- Full-article extraction and overlapping text chunking
- Distributed embedding generation with Spark pandas UDFs
- Document-level and chunk-level vector storage with pgvector
- Natural-language semantic search using cosine similarity
- Retrieval-ready context for RAG and agent workflows
- Databricks secret management and deployable application configuration

---

## Architecture

```text
                     +----------------------+
                     |  Databricks App UI   |
                     | Watchlist + Search   |
                     +----------+-----------+
                                |
                                v
                     +----------------------+
                     | Flask API / app.py   |
                     +----------+-----------+
                                |
              +-----------------+-----------------+
              |                                   |
              v                                   v
     +-------------------+                +-------------------+
     | Massive Market API|                | Databricks        |
     | Quotes + News     |                | Lakebase/Postgres |
     +---------+---------+                +---------+---------+
               |                                    |
               |                            watchlist + raw news
               |                                    |
               +------------------+-----------------+
                                  |
                                  v
                 +----------------------------------+
                 | Databricks Workflow / Asset     |
                 | Bundle                          |
                 | ingest_ticker_news_embeddings   |
                 +----------------+-----------------+
                                  |
                   +--------------+--------------+
                   |                             |
                   v                             v
          title/description               full article body
              embeddings                  extraction/chunking
                   |                             |
                   +--------------+--------------+
                                  |
                                  v
                     +--------------------------+
                     | Lakebase + pgvector      |
                     | document embeddings      |
                     | chunk embeddings         |
                     +------------+-------------+
                                  |
                                  v
                     +--------------------------+
                     | Semantic Search API/UI   |
                     +--------------------------+
```

---

## Core data flow

### 1. Watchlist-driven ingestion

The Databricks App lets authenticated users manage a ticker watchlist. The application stores watchlist state in Lakebase and retrieves current market information from the Massive API.

The news pipeline reads distinct tracked tickers and fetches recent articles only for symbols that are actively being followed.

### 2. Raw news storage

News records are normalized into `ticker_news_documents`, including fields such as ticker, title, description, publisher, sentiment, publication time, article URL, and the original payload for lineage and reprocessing.

### 3. Article extraction and chunking

For each article, the pipeline can retrieve the full article body and use `trafilatura` to remove navigation, advertisements, and page boilerplate.

Long-form text is split into overlapping chunks so retrieval can return the most relevant passage rather than only a document-level match.

### 4. Distributed embeddings

The pipeline generates embeddings with Sentence Transformers. Spark pandas UDFs distribute embedding work across the cluster for article-level processing.

The default model is:

```text
sentence-transformers/all-MiniLM-L6-v2
```

The pipeline supports configurable embedding models and corresponding vector dimensions.

### 5. Vector storage and semantic retrieval

Two vector tables support different retrieval granularities:

```text
ticker_news_embeddings
    document-level vectors from title + description

ticker_news_chunk_embeddings
    passage-level vectors from extracted article content
```

Both are stored in Lakebase/PostgreSQL using **pgvector**.

The `/news/search` API embeds a user's natural-language query and retrieves the nearest document and chunk vectors using cosine similarity.

Example query:

```text
companies discussing AI infrastructure spending
```

---

## Databricks orchestration

`notebooks/ingest_ticker_news_embeddings.py` contains the ingestion and embedding workflow.

The project also includes:

```text
databricks.yml
resources/ingest_ticker_news_embeddings_job.yml
```

These define a Databricks Asset Bundle so the pipeline can be deployed and scheduled as a version-controlled Databricks Workflow.

---

## Application capabilities

### Watchlist

```http
GET    /watchlist
POST   /watchlist
DELETE /watchlist/<symbol>
```

Watchlists are associated with the authenticated Databricks user.

### News ingestion

```http
POST /news/sync
```

Fetches recent market news for supplied or configured ticker symbols and upserts it into Lakebase.

### Semantic search

```http
POST /news/search
```

Example request:

```json
{
  "query": "semiconductor companies increasing AI investment",
  "limit": 5,
  "include_chunks": true
}
```

Returns both document-level and passage-level semantic matches.

### Health check

```http
GET /healthz
```

---

## Repository structure

```text
.
├── app.py
├── massive_client.py
├── lakebase.py
├── app.yaml
├── databricks.yml
├── requirements.txt
├── setup_secrets.py
│
├── notebooks/
│   └── ingest_ticker_news_embeddings.py
│
├── resources/
│   └── ingest_ticker_news_embeddings_job.yml
│
├── sql/
│   ├── 01_setup_news_table.sql
│   ├── 02_setup_embeddings_table.sql
│   ├── 03_setup_chunk_embeddings_table.sql
│   └── README.md
│
└── templates/
    ├── index.html
    └── search.html
```

---

## Running locally

Install dependencies:

```bash
pip install -r requirements.txt
```

Configure the Lakebase connection and Massive API key using environment variables or Databricks secrets, then run:

```bash
python app.py
```

---

## Databricks deployment

The Flask application can be deployed as a **Databricks App** using `app.yaml`.

The ingestion/embedding pipeline can be deployed through the included Asset Bundle:

```bash
databricks bundle deploy -t dev
databricks bundle run ingest_ticker_news_embeddings_job -t dev
```

Credentials are intentionally excluded from source control and loaded through Databricks secret scopes.

---

## Related project

A separate project applies the same semantic-retrieval architecture to National Weather Service forecast and alert narratives:

**[Weather Semantic Search](https://github.com/NikhitaDasari/weather-semantic-search)**

The next stage of this work extends the market intelligence pipeline with **Model Context Protocol (MCP)** tooling and agent-driven actions.

---

## Background

I built this project while expanding my enterprise data-engineering background into **AI data engineering**, focusing on the data systems behind useful LLM applications: ingestion, operational storage, unstructured-data processing, distributed embeddings, vector retrieval, orchestration, and application integration.
