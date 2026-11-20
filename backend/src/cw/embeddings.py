"""Embeddings from a local Ollama server over HTTP: queries carry the task instruction, documents do not."""

import json
import logging
import urllib.error
import urllib.request

import numpy

from cw.config import OllamaEmbeddingSettings, Settings
from cw.errors import AppErrorCode, AppException

logger = logging.getLogger(__name__)

EMBED_PATH = "/api/embed"
TAGS_PATH = "/api/tags"
JSON_CONTENT_TYPE = "application/json"
CHECK_TEXT = "def health_check() -> bool"
LATEST_TAG = ":latest"
VECTOR_TYPE = numpy.float32


def _failed(embeddings: OllamaEmbeddingSettings, detail: str) -> AppException:
    """Return the error for an Ollama failure, naming the server and model, never the embedded text."""
    return AppException(
        AppErrorCode.CONTEXT_INDEX_FAILED, f"Ollama at {embeddings.base_url} with {embeddings.model}: {detail}"
    )


def _request(embeddings: OllamaEmbeddingSettings, path: str, payload: dict | None = None) -> dict:
    """Call the Ollama API and return its JSON answer."""
    request = urllib.request.Request(
        f"{embeddings.base_url.rstrip('/')}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": JSON_CONTENT_TYPE},
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=embeddings.request_timeout_seconds) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:500]
        logger.error("Ollama %s failed with %s.", path, error.code)
        raise _failed(embeddings, f"HTTP {error.code}: {detail}") from error
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
        logger.error("Ollama is not reachable at %s.", embeddings.base_url)
        raise _failed(embeddings, f"not reachable ({error}); start it with: ollama serve") from error
    except json.JSONDecodeError as error:
        raise _failed(embeddings, "answered with something that is not JSON") from error


def _embed(embeddings: OllamaEmbeddingSettings, texts: list[str]) -> list[list[float]]:
    """Send one batch to /api/embed and return the raw vectors, one per text."""
    answer = _request(
        embeddings,
        EMBED_PATH,
        {
            "model": embeddings.model,
            "input": texts,
            "keep_alive": embeddings.keep_alive,
            "options": {"num_ctx": embeddings.context_length},
        },
    )
    vectors = answer.get("embeddings")
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise _failed(embeddings, "answered without one embedding per text")
    return vectors


def _normalised(embeddings: OllamaEmbeddingSettings, vectors: list[list[float]]) -> numpy.ndarray:
    """Keep the leading `dimensions` values of each vector and scale it to unit length."""
    matrix = numpy.asarray(vectors, dtype=VECTOR_TYPE)
    if matrix.ndim != 2 or matrix.shape[1] < embeddings.dimensions:
        width = matrix.shape[1] if matrix.ndim == 2 else 0
        raise AppException(
            AppErrorCode.INVALID_CONFIGURATION,
            f"{embeddings.model} returns {width} values; context.embeddings.dimensions is {embeddings.dimensions}.",
        )
    cut = matrix[:, : embeddings.dimensions]
    norms = numpy.linalg.norm(cut, axis=1, keepdims=True)
    return (cut / numpy.where(norms == 0, 1, norms)).astype(VECTOR_TYPE)


def encode_documents(settings: Settings, texts: list[str]) -> numpy.ndarray:
    """Return one unit vector per document text, sent in batches of `batch_size`."""
    embeddings = settings.context.embeddings
    vectors: list[list[float]] = []
    for start in range(0, len(texts), embeddings.batch_size):
        vectors += _embed(embeddings, texts[start : start + embeddings.batch_size])
    if not vectors:
        return numpy.zeros((0, embeddings.dimensions), dtype=VECTOR_TYPE)
    return _normalised(embeddings, vectors)


def encode_query(settings: Settings, text: str) -> numpy.ndarray:
    """Return the unit vector of a search query, phrased with the model's task instruction."""
    embeddings = settings.context.embeddings
    query = embeddings.query_template.format(instruction=embeddings.query_instruction, query=text)
    return _normalised(embeddings, _embed(embeddings, [query]))[0]


def check_embeddings(settings: Settings) -> None:
    """Confirm Ollama answers, the model is pulled, and its vectors are long enough; raise with the reason if not."""
    embeddings = settings.context.embeddings
    names = {model.get("name", "") for model in _request(embeddings, TAGS_PATH).get("models", [])}
    wanted = {embeddings.model, f"{embeddings.model}{LATEST_TAG}"}
    if not names & wanted:
        raise _failed(embeddings, f"the model is not pulled; run: ollama pull {embeddings.model}")
    _normalised(embeddings, _embed(embeddings, [CHECK_TEXT]))
