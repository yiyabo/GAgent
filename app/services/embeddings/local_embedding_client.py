#!/usr/bin/env python3
"""
Local Embedding Client Module

Uses open-source sentence-transformers models for local embedding generation.
Designed as a drop-in replacement for GLMApiClient.
"""

import logging
import threading
import time
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class LocalEmbeddingClient:
    """Local embedding client using sentence-transformers"""

    def __init__(self, config):
        """
        Initialize local embedding client

        Args:
            config: Configuration object containing model settings
        """
        self.config = config
        
        # Get model name from config, default to all-mpnet-base-v2
        self.model_name = getattr(config, 'local_embedding_model', 'sentence-transformers/all-mpnet-base-v2')
        self.embedding_dimension = getattr(config, 'embedding_dimension', 768)
        self.mock_mode = getattr(config, 'mock_mode', False)
        
        # Lazy load the model
        self._model_instance = None
        self._device = None
        self._model_lock = threading.Lock()
        
        # IMPORTANT: Set self.model to model_name (string) for cache compatibility
        # This is used by batch processor: self.cache.put(text, embedding, self.api_client.model)
        self.model = self.model_name
        
        logger.info(f"Local Embedding Client initialized - Model: {self.model_name}")

    @property
    def _model(self):
        """Lazy load the sentence-transformers model"""
        if self._model_instance is None:
            with self._model_lock:
                if self._model_instance is None:
                    try:
                        from sentence_transformers import SentenceTransformer
                        import torch

                        # Determine device
                        self._device = 'cuda' if torch.cuda.is_available() else 'cpu'
                        logger.info(f"Loading model on device: {self._device}")

                        # Load model
                        self._model_instance = SentenceTransformer(self.model_name, device=self._device)
                        logger.info(f"Model loaded successfully: {self.model_name}")

                    except ImportError:
                        logger.error("sentence-transformers not installed. Install with: pip install sentence-transformers")
                        raise ImportError(
                            "sentence-transformers is required for local embeddings. "
                            "Install with: pip install sentence-transformers"
                        )
                    except Exception as e:
                        logger.error(f"Failed to load model {self.model_name}: {e}")
                        raise
        
        return self._model_instance

    def get_embeddings_from_api(self, texts: List[str]) -> List[List[float]]:
        """
        Get embeddings from local model (API-compatible interface)

        Args:
            texts: List of texts

        Returns:
            List of embeddings

        Raises:
            Exception: Raised when embedding generation fails
        """
        if self.mock_mode:
            return self._create_mock_embeddings(texts)

        try:
            start_time = time.time()
            
            # Generate embeddings
            # Note: sentence-transformers 2.x has different encode() signature
            embeddings = self._model.encode(
                texts,
                batch_size=32,
                show_progress_bar=False,
                normalize_embeddings=True,  # Normalize for cosine similarity
                convert_to_numpy=True
            )
            
            # Convert to list format
            embeddings_list = embeddings.tolist()
            
            elapsed = time.time() - start_time
            logger.debug(
                f"Generated {len(embeddings_list)} embeddings in {elapsed:.2f}s "
                f"({len(texts)/elapsed:.1f} texts/sec)"
            )
            
            return embeddings_list
            
        except Exception as e:
            logger.error(f"Local embedding generation failed: {e}")
            raise

    def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Alias for get_embeddings_from_api to maintain compatibility

        Args:
            texts: List of texts to get embeddings for

        Returns:
            List of embeddings
        """
        return self.get_embeddings_from_api(texts)

    def _create_mock_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Create mock embeddings for testing"""
        import numpy as np

        embeddings = []
        for text in texts:
            # Generate deterministic mock embedding based on text content
            np.random.seed(hash(text) % (2**32))
            embedding = np.random.normal(0, 1, self.embedding_dimension).tolist()
            embeddings.append(embedding)

        logger.debug(f"Generated {len(embeddings)} mock embeddings")
        return embeddings

    def test_connection(self) -> bool:
        """Test model loading"""
        if self.mock_mode:
            logger.info("Mock mode - connection test skipped")
            return True

        try:
            # Try to load model and generate a test embedding
            test_embeddings = self.get_embeddings_from_api(["test"])
            success = len(test_embeddings) > 0 and len(test_embeddings[0]) == self.embedding_dimension
            
            if success:
                logger.info(f"Model test successful - dimension: {len(test_embeddings[0])}")
            else:
                logger.error(f"Model test failed - unexpected dimension")
            
            return success
            
        except Exception as e:
            logger.error(f"Model connection test failed: {e}")
            return False

    def get_client_info(self) -> Dict[str, Any]:
        """Get client information"""
        device_info = "not loaded"
        if self._model_instance is not None:
            device_info = self._device
        
        return {
            "model": self.model_name,
            "type": "local",
            "device": device_info,
            "dimension": self.embedding_dimension,
            "mock_mode": self.mock_mode,
        }

    def get_model_info(self) -> Dict[str, Any]:
        """Get detailed model information"""
        info = self.get_client_info()
        
        if self._model_instance is not None:
            info.update({
                "max_seq_length": self._model.max_seq_length,
                "model_loaded": True,
            })
        else:
            info["model_loaded"] = False
        
        return info
