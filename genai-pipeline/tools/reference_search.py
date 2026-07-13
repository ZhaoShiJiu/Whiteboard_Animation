import os
import time
import json
import urllib.request
import urllib.parse
from typing import Optional

from . import utils
from .utils import _emit

try:
    from log_utils import ContextLogger
except ImportError:
    ContextLogger = None  # type: ignore


def reference_search_tool_fn(
    query: str,
    logger: Optional["ContextLogger"] = None,
) -> str:
    """
    Searches Wikipedia for the given query and downloads the main page image if available.

    Args:
        query: The subject to search for (e.g., "5th president of France", "Eiffel Tower").
        logger: Optional ContextLogger for structured logging.

    Returns:
        The absolute path to the downloaded image, or an error string if not found.
    """
    try:
        # First, search Wikipedia to get the best matching title
        search_query = urllib.parse.quote(query)
        search_url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={search_query}&utf8=&format=json"

        req = urllib.request.Request(search_url, headers={'User-Agent': 'StoryboardAI/1.0'})
        with urllib.request.urlopen(req) as response:
            search_data = json.loads(response.read().decode('utf-8'))

        search_results = search_data.get('query', {}).get('search', [])
        if not search_results:
            return f"Error: No results found for query '{query}'"

        best_title = search_results[0]['title']
        _emit(logger, "info", f"Reference Search: Found Wikipedia article",
              extra={"title": best_title, "query": query})

        # Next, get the main image for this title
        title_encoded = urllib.parse.quote(best_title)
        image_url_api = f"https://en.wikipedia.org/w/api.php?action=query&titles={title_encoded}&prop=pageimages&format=json&pithumbsize=1000"

        req = urllib.request.Request(image_url_api, headers={'User-Agent': 'StoryboardAI/1.0'})
        with urllib.request.urlopen(req) as response:
            image_data = json.loads(response.read().decode('utf-8'))

        pages = image_data.get('query', {}).get('pages', {})
        page = next(iter(pages.values()))

        if 'thumbnail' not in page:
            return f"Error: No image found for article '{best_title}'"

        image_url = page['thumbnail']['source']
        _emit(logger, "debug", f"Downloading reference image", extra={"url": image_url})

        # Download the image
        timestamp = int(time.time())
        ext = os.path.splitext(urllib.parse.urlparse(image_url).path)[1]
        if not ext:
            ext = ".jpg"

        filename = f"reference_{timestamp}_{urllib.parse.quote(best_title)}{ext}"

        # Save to global output dir or current dir
        output_path = os.path.join(utils.GLOBAL_OUTPUT_DIR, filename) if utils.GLOBAL_OUTPUT_DIR else filename

        req = urllib.request.Request(image_url, headers={'User-Agent': 'StoryboardAI/1.0'})
        with urllib.request.urlopen(req) as response:
            with open(output_path, 'wb') as f:
                f.write(response.read())

        _emit(logger, "info", f"Reference image downloaded", extra={"path": output_path})
        return os.path.abspath(output_path)

    except Exception as e:
        _emit(logger, "error", f"Reference search failed for '{query}': {e}", extra={"error": str(e)})
        return f"Error during reference search for '{query}': {str(e)}"
