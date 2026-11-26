"""
Multimodal Processing Module for CrossTrans.
Handles image encoding and vision model capabilities.
"""
import base64
import logging
import mimetypes
import os
import fnmatch
from typing import Tuple, Optional
from src.core.remote_config import get_config


class MultimodalProcessor:
    """Handles image processing and vision capabilities."""

    VISION_PROBE_VERSION = 2

    @staticmethod
    def has_legacy_groq_vision_result(entry: dict, provider: str = '') -> bool:
        """The tiny-image probe is not evidence against this documented Groq model."""
        route = (provider or entry.get('provider', 'Auto')).casefold()
        if route == 'auto':
            route = 'groq' if entry.get('api_key', '').startswith('gsk_') else ''
        return (route == 'groq' and entry.get('model_name', '').casefold() == 'qwen/qwen3.8-27b'
                and entry.get('vision_probe_version', 0) < MultimodalProcessor.VISION_PROBE_VERSION)

    @staticmethod
    def config_accepts_images(entry: dict, provider: str) -> bool:
        if MultimodalProcessor.has_legacy_groq_vision_result(entry, provider):
            return True
        flag = entry.get('vision_capable')
        return bool(flag) if flag is not None else MultimodalProcessor.is_vision_capable(
            entry.get('model_name', ''), provider)

    @staticmethod
    def is_vision_capable(model_name: str, provider: str) -> bool:
        """Check if a model supports vision."""
        provider = provider.lower()
        model_name = model_name.lower()
        
        vision_models = get_config().vision_models
        if provider == 'groq' and model_name == 'qwen/qwen3.8-27b':
            return True
        models = vision_models.get(provider, [])
        for m in models:
            m = m.lower()
            if provider == 'openrouter' and m == '*':
                continue
            # Handle wildcards
            if '*' in m:
                if fnmatch.fnmatch(model_name, m):
                    return True
            elif m == model_name:
                return True
                
        # Heuristics for models not explicitly listed but likely vision
        if (provider == 'openrouter' and model_name == 'qwen/qwen3.8-27b'
                or 'vision' in model_name or 'pixtral' in model_name):
            return True
            
        return False

    @staticmethod
    def encode_image_base64(image_path: str) -> Tuple[Optional[str], Optional[str]]:
        """Encode image file to base64."""
        if not os.path.exists(image_path):
            return None, None
            
        mime_type, _ = mimetypes.guess_type(image_path)
        if not mime_type:
            mime_type = 'image/jpeg'
            
        try:
            with open(image_path, "rb") as image_file:
                encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
            return encoded_string, mime_type
        except Exception as e:
            logging.error(f"Error encoding image: {e}")
            return None, None
