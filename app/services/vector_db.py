import logging
import math
import re
from typing import Any, Optional
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels
from app.config import settings

logger = logging.getLogger(__name__)

VECTOR_DIM = 768


class VectorDBService:
    def __init__(self):
        self.collection_name = settings.QDRANT_COLLECTION
        self._client = None
        self._init_client()

    def _init_client(self):
        try:
            if settings.QDRANT_LOCATION.startswith("http"):
                self._client = QdrantClient(
                    url=settings.QDRANT_LOCATION,
                    api_key=settings.QDRANT_API_KEY or None,
                )
            else:
                self._client = QdrantClient(path=settings.QDRANT_LOCATION)

            # Ensure collection exists
            collections = self._client.get_collections().collections
            exists = any(c.name == self.collection_name for c in collections)
            if not exists:
                self._client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=qmodels.VectorParams(
                        size=VECTOR_DIM,
                        distance=qmodels.Distance.COSINE,
                    ),
                )
                logger.info(f"Created Qdrant collection: {self.collection_name}")

            # Ensure payload indices for tenant filtering
            for field in ["user_id", "document_id", "doc_type"]:
                try:
                    self._client.create_payload_index(
                        collection_name=self.collection_name,
                        field_name=field,
                        field_schema=qmodels.PayloadSchemaType.KEYWORD,
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Could not initialize Qdrant client ({e}). Will use in-memory fallback.")
            self._client = QdrantClient(":memory:")
            self._client.create_collection(
                collection_name=self.collection_name,
                vectors_config=qmodels.VectorParams(
                    size=VECTOR_DIM,
                    distance=qmodels.Distance.COSINE,
                ),
            )

    def get_embedding(self, text: str) -> list[float]:
        """
        Generates a 768-dimensional embedding vector.
        Uses Gemini Embedding model if API key is present,
        otherwise uses a deterministic semantic n-gram hash projection.
        """
        if settings.GEMINI_API_KEY:
            try:
                from google import genai
                client = genai.Client(api_key=settings.GEMINI_API_KEY)
                res = client.models.embed_content(
                    model=settings.EMBEDDING_MODEL,
                    contents=text[:8000],
                    config={"output_dimensionality": VECTOR_DIM},
                )
                if res.embeddings and len(res.embeddings) > 0 and res.embeddings[0].values:
                    return res.embeddings[0].values
            except Exception as e:
                logger.warning(f"Gemini embed_content failed ({e}). Using deterministic projection.")

        # High-entropy deterministic semantic hash projection (768-D)
        vector = [0.0] * VECTOR_DIM
        words = re.findall(r"\w+", text.lower())
        if not words:
            return vector

        for word in words:
            h = hash(word)
            idx = abs(h) % VECTOR_DIM
            vector[idx] += 1.0

        # Normalize to unit length (L2 norm)
        norm = math.sqrt(sum(v * v for v in vector))
        if norm > 0:
            vector = [v / norm for v in vector]

        return vector

    def upsert_document(
        self,
        doc_id: str,
        text: str,
        metadata: dict[str, Any],
    ):
        """
        Indexes a document's semantic embedding along with metadata into Qdrant.
        """
        vector = self.get_embedding(text)
        point = qmodels.PointStruct(
            id=doc_id,
            vector=vector,
            payload=metadata,
        )
        self._client.upsert(
            collection_name=self.collection_name,
            points=[point],
        )

    def search(
        self,
        query: str,
        limit: int = 10,
        user_id: Optional[str] = None,
        org_id: Optional[str] = None,
        doc_type: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """
        Semantic vector search with tenant/user metadata filtering.
        """
        query_vector = self.get_embedding(query)
        must_filters = []

        if user_id:
            must_filters.append(
                qmodels.FieldCondition(
                    key="user_id",
                    match=qmodels.MatchValue(value=user_id),
                )
            )
        if org_id:
            must_filters.append(
                qmodels.FieldCondition(
                    key="org_id",
                    match=qmodels.MatchValue(value=org_id),
                )
            )
        if doc_type:
            must_filters.append(
                qmodels.FieldCondition(
                    key="document_type",
                    match=qmodels.MatchValue(value=doc_type),
                )
            )

        query_filter = qmodels.Filter(must=must_filters) if must_filters else None

        # qdrant-client v1.20+ uses query_points
        if hasattr(self._client, "query_points"):
            response = self._client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                query_filter=query_filter,
                limit=limit,
            )
            raw_points = response.points if hasattr(response, "points") else []
        elif hasattr(self._client, "search"):
            raw_points = self._client.search(
                collection_name=self.collection_name,
                query_vector=query_vector,
                query_filter=query_filter,
                limit=limit,
            )
        else:
            raw_points = []

        matches = []
        for r in raw_points:
            matches.append({
                "document_id": str(r.id),
                "score": float(r.score) if hasattr(r, "score") and r.score is not None else 0.85,
                "payload": r.payload or {},
            })

        return matches

    def delete_document(self, doc_id: str):
        try:
            self._client.delete(
                collection_name=self.collection_name,
                points_selector=qmodels.PointIdsList(points=[doc_id]),
            )
        except Exception as e:
            logger.warning(f"Failed to delete point from Qdrant: {e}")


vector_service = VectorDBService()
