# --- START OF FILE app.py ---

import os
import uuid
import time
import logging
import random # Keep for potential future use (jitter)
import html
import re
import json
import traceback
from io import BytesIO
from functools import wraps
from datetime import datetime, timedelta

from flask import (
    Flask, render_template, request, jsonify,
    send_file, make_response, current_app, stream_with_context, Response, g
)
from dotenv import load_dotenv
from google import genai
from google.genai import types
from ddgs import DDGS
import requests
from bs4 import BeautifulSoup
import trafilatura
import mistune

from docx import Document
from docx.shared import Pt, Inches
from xhtml2pdf import pisa

# Security & Rate Limiting
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_caching import Cache

# --- Configuration ---
load_dotenv()

# Environment check
IS_PRODUCTION = os.getenv("FLASK_ENV", "development") == "production"

logging.basicConfig(
    level=logging.WARNING if IS_PRODUCTION else logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(funcName)s:%(lineno)d] - %(message)s'
)

# --- Security Constants ---
MAX_QUERY_LENGTH = 2000
MAX_CHAT_MESSAGES = 10  # Maximum messages per chat

# --- Constants ---
QUICK_SEARCH_RESULTS = 10
DEEP_SEARCH_RESULTS = 25
SCRAPE_TIMEOUT = 8
MAX_CONTENT_LENGTH_PER_SITE = 15000
MAX_TOTAL_CONTENT_LENGTH = 200000
REPORT_STORE_MAX_ITEMS = 100
SOURCE_PREVIEW_LENGTH = 300

# --- In-memory storage ---
report_store = {}
report_order = []
chat_store = {} # Stores chat sessions: {chat_id: {messages: [], created_at: timestamp, deep_dive_count: 0}}

# --- Simple Search Cache (TTL-based) ---
search_cache = {}
SEARCH_CACHE_TTL = 300  # 5 minutes

def get_cached_search(query, num_results):
    """Get cached search results if still valid."""
    cache_key = f"{query}:{num_results}"
    if cache_key in search_cache:
        cached_time, results = search_cache[cache_key]
        if time.time() - cached_time < SEARCH_CACHE_TTL:
            logging.info(f"Cache hit for query: {query}")
            return results
        else:
            del search_cache[cache_key]
    return None

def set_cached_search(query, num_results, results):
    """Cache search results."""
    cache_key = f"{query}:{num_results}"
    search_cache[cache_key] = (time.time(), results)
    # Cleanup old entries (keep max 100)
    if len(search_cache) > 100:
        oldest_key = min(search_cache.keys(), key=lambda k: search_cache[k][0])
        del search_cache[oldest_key]

def add_to_report_store(report_id, data):
    """
    Add a report to the in-memory store, managing maximum number of items.

    Args:
        report_id (str): Unique identifier for the report
        data (dict): Report data to store
    """
    if len(report_store) >= REPORT_STORE_MAX_ITEMS:
        try:
            oldest_id = report_order.pop(0)
            if oldest_id in report_store:
                del report_store[oldest_id]
                logging.info(f"Removed oldest report ({oldest_id}).")
        except IndexError:
            pass  # No reports to remove

    report_store[report_id] = data
    report_order.append(report_id)

# --- Configuration ---
CHAT_HISTORY_FILE = 'chat_history.json'  # Fallback if MySQL fails

# --- MySQL Configuration ---
# Load database credentials from environment variables
MYSQL_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset': 'utf8mb4',
    'cursorclass': None  # Will be set after import
}

# Try to import PyMySQL
try:
    import pymysql
    MYSQL_CONFIG['cursorclass'] = pymysql.cursors.DictCursor
    MYSQL_AVAILABLE = all((
        MYSQL_CONFIG.get('host'),
        MYSQL_CONFIG.get('user'),
        MYSQL_CONFIG.get('database'),
    ))
    if not MYSQL_AVAILABLE:
        logging.info("MySQL is not configured. Using JSON file storage.")
except ImportError:
    MYSQL_AVAILABLE = False
    logging.warning("PyMySQL not installed. Using JSON file storage as fallback.")

def get_db_connection():
    """Get a MySQL database connection."""
    if not MYSQL_AVAILABLE:
        return None
    try:
        connection = pymysql.connect(**MYSQL_CONFIG)
        return connection
    except Exception as e:
        logging.error(f"MySQL connection failed: {e}")
        return None

def init_database():
    """Initialize the MySQL database and tables."""
    if not MYSQL_AVAILABLE:
        return False

    try:
        # First connect without database to create it if needed
        config_no_db = MYSQL_CONFIG.copy()
        config_no_db.pop('database', None)

        conn = pymysql.connect(**config_no_db)
        with conn.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS {MYSQL_CONFIG['database']} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        conn.close()

        # Now connect to the database and create tables
        conn = get_db_connection()
        if not conn:
            return False

        with conn.cursor() as cursor:
            # Create chats table with user_id for user-specific storage
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chats (
                    id VARCHAR(36) PRIMARY KEY,
                    user_id VARCHAR(36),
                    created_at DOUBLE NOT NULL,
                    last_active DOUBLE NOT NULL,
                    deep_dive_count INT DEFAULT 0,
                    mode VARCHAR(20) DEFAULT 'quick',
                    INDEX idx_created_at (created_at),
                    INDEX idx_last_active (last_active)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

            # Migration: Add user_id column if it doesn't exist (for existing tables)
            try:
                cursor.execute("ALTER TABLE chats ADD COLUMN user_id VARCHAR(36) DEFAULT 'anonymous'")
                cursor.execute("ALTER TABLE chats ADD INDEX idx_user_id (user_id)")
                logging.info("Migration: Added user_id column to chats table")
            except Exception as e:
                if "Duplicate column" not in str(e) and "Duplicate key name" not in str(e):
                    logging.debug(f"user_id column migration note: {e}")

            # Create messages table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id VARCHAR(36) PRIMARY KEY,
                    chat_id VARCHAR(36) NOT NULL,
                    query TEXT,
                    answer_raw LONGTEXT,
                    answer_html LONGTEXT,
                    sources JSON,
                    follow_up_questions JSON,
                    research_depth VARCHAR(20),
                    timestamp DOUBLE,
                    FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE,
                    INDEX idx_chat_id (chat_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

        conn.commit()
        conn.close()
        logging.info("MySQL database initialized successfully.")
        return True
    except Exception as e:
        logging.error(f"Failed to initialize MySQL database: {e}")
        return False

# --- Helper Functions ---
def load_chat_history():
    """Load chat history from MySQL or fallback to JSON."""
    global chat_store

    # Try MySQL first
    if MYSQL_AVAILABLE:
        conn = get_db_connection()
        if conn:
            try:
                with conn.cursor() as cursor:
                    # Load all chats
                    cursor.execute("SELECT * FROM chats")
                    chats = cursor.fetchall()

                    for chat in chats:
                        chat_id = chat['id']
                        chat_store[chat_id] = {
                            'user_id': chat.get('user_id', 'anonymous'),
                            'created_at': chat['created_at'],
                            'last_active': chat['last_active'],
                            'deep_dive_count': chat['deep_dive_count'],
                            'mode': chat['mode'],
                            'messages': []
                        }

                        # Load messages for this chat
                        cursor.execute("SELECT * FROM messages WHERE chat_id = %s ORDER BY timestamp", (chat_id,))
                        messages = cursor.fetchall()

                        for msg in messages:
                            chat_store[chat_id]['messages'].append({
                                'id': msg['id'],
                                'query': msg['query'],
                                'answer_raw': msg['answer_raw'],
                                'answer_html': msg['answer_html'],
                                'sources': json.loads(msg['sources']) if msg['sources'] else [],
                                'follow_up_questions': json.loads(msg['follow_up_questions']) if msg['follow_up_questions'] else [],
                                'research_depth': msg['research_depth'],
                                'timestamp': msg['timestamp'],
                                'chat_id': msg['chat_id']
                            })

                conn.close()
                logging.info(f"Loaded {len(chat_store)} chats from MySQL.")
                return
            except Exception as e:
                logging.error(f"Failed to load from MySQL: {e}")
                conn.close()

    # Fallback to JSON file
    if os.path.exists(CHAT_HISTORY_FILE):
        try:
            with open(CHAT_HISTORY_FILE, 'r') as f:
                chat_store = json.load(f)
            logging.info(f"Loaded {len(chat_store)} chats from JSON file (fallback).")
        except Exception as e:
            logging.error(f"Failed to load chat history: {e}")

def extract_thinking_process(text: str) -> tuple[str | None, str]:
    """
    Robustly extracts content within <thinking> tags.
    Returns: (thinking_content, cleaned_text)

    Strategies:
    1. Exact <thinking>...</thinking> match (case insensitive, dotall).
    2. Unclosed <thinking> tag that goes until "Synthesized Response:" or end of text.
    3. Markdown block ```thinking ... ``` (rare but possible).
    """
    if not text:
        return None, text

    # Strategy 1: Standard closed tags (most common)
    # We use a non-greedy match for the content to find the first closing tag
    match = re.search(r'<\s*thinking[^>]*>(.*?)<\s*/\s*thinking\s*>', text, re.DOTALL | re.IGNORECASE)
    if match:
        content = match.group(1).strip()
        # Remove the entire block
        cleaned_text = text.replace(match.group(0), "").strip()
        return content, cleaned_text

    # Strategy 2: Unclosed tag at the start
    # Sometimes the model forgets to close it or gets cut off, but usually we have "Synthesized Response:" later
    # We look for <thinking> ... then either </thinking> (missed by S1?) or "Synthesized Response:"
    start_match = re.search(r'<\s*thinking[^>]*>', text, re.IGNORECASE)
    if start_match:
        start_idx = start_match.end()
        # Try to find a logical end point
        end_markers = [r'<\s*/\s*thinking\s*>', r'Synthesized Response:', r'## Synthesized Response', r'Here is the answer']
        best_end_idx = len(text)
        found_end = False

        for marker in end_markers:
            end_match = re.search(marker, text[start_idx:], re.IGNORECASE)
            if end_match:
                current_end_idx = start_idx + end_match.start()
                if current_end_idx < best_end_idx:
                    best_end_idx = current_end_idx
                    found_end = True

        content = text[start_idx:best_end_idx].strip()
        # Clean text: Remove the tag and the content.
        # If we found an end marker, we keep the marker (except </thinking>)
        # But to be safe, let's just construct cleaned text

        # If the marker was </thinking>, we should have caught it in S1, but maybe regex weirdness.
        # If marker was "Synthesized Response:", we keep that.

        cleaned_text = text[:start_match.start()] + text[best_end_idx:]

        # If we just stripped everything to the end because no marker found,
        # cleaned_text might be empty. That's a risk.
        # But usually we define prompts to have "Synthesized Response:"

        return content, cleaned_text.strip()

    return None, text

def save_chat_history(specific_chat_id=None):
    """Save chat history to MySQL or fallback to JSON.

    Args:
        specific_chat_id (str, optional): If provided, only save this specific chat session.
    """
    # Try MySQL first
    if MYSQL_AVAILABLE:
        conn = get_db_connection()
        if conn:
            try:
                with conn.cursor() as cursor:
                    # Determine which chats to save
                    chats_to_save = chat_store.items()
                    if specific_chat_id:
                        if specific_chat_id in chat_store:
                            chats_to_save = [(specific_chat_id, chat_store[specific_chat_id])]
                        else:
                            chats_to_save = []

                    for chat_id, chat_data in chats_to_save:
                        # Upsert chat with user_id
                        cursor.execute("""
                            INSERT INTO chats (id, user_id, created_at, last_active, deep_dive_count, mode)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON DUPLICATE KEY UPDATE
                                last_active = VALUES(last_active),
                                deep_dive_count = VALUES(deep_dive_count),
                                mode = VALUES(mode)
                        """, (
                            chat_id,
                            chat_data.get('user_id', 'anonymous'),
                            chat_data.get('created_at', time.time()),
                            chat_data.get('last_active', time.time()),
                            chat_data.get('deep_dive_count', 0),
                            chat_data.get('mode', 'quick')
                        ))

                        # Save messages
                        for msg in chat_data.get('messages', []):
                            cursor.execute("""
                                INSERT INTO messages (id, chat_id, query, answer_raw, answer_html, sources, follow_up_questions, research_depth, timestamp)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                                ON DUPLICATE KEY UPDATE
                                    answer_raw = VALUES(answer_raw),
                                    answer_html = VALUES(answer_html)
                            """, (
                                msg.get('id', str(uuid.uuid4())),
                                chat_id,
                                msg.get('query', ''),
                                msg.get('answer_raw', ''),
                                msg.get('answer_html', ''),
                                json.dumps(msg.get('sources', [])),
                                json.dumps(msg.get('follow_up_questions', [])),
                                msg.get('research_depth', 'quick'),
                                msg.get('timestamp', time.time())
                            ))

                conn.commit()
                conn.close()
                logging.info("Chat history saved to MySQL.")
                return
            except Exception as e:
                logging.error(f"Failed to save to MySQL: {e}")
                conn.close()

    # Fallback to JSON file
    try:
        with open(CHAT_HISTORY_FILE, 'w') as f:
            json.dump(chat_store, f, indent=4)
        logging.info("Chat history saved to JSON file (fallback).")
    except Exception as e:
        logging.error(f"Failed to save chat history: {e}")

def delete_chat_from_db(chat_id):
    """Delete a chat from MySQL database."""
    if MYSQL_AVAILABLE:
        conn = get_db_connection()
        if conn:
            try:
                with conn.cursor() as cursor:
                    cursor.execute("DELETE FROM chats WHERE id = %s", (chat_id,))
                conn.commit()
                conn.close()
                logging.info(f"Deleted chat {chat_id} from MySQL.")
                return True
            except Exception as e:
                logging.error(f"Failed to delete chat from MySQL: {e}")
                conn.close()
    return False

def perform_search(query: str, num_results: int) -> list[dict]:
    """
    Perform a web search using DuckDuckGo with retries and caching.
    """
    # Check cache first
    cached = get_cached_search(query, num_results)
    if cached:
        return cached

    logging.info(f"Searching DuckDuckGo for: {query} (max {num_results})")

    for attempt in range(3):
        try:
            # Use ddgs with India region and safesearch off for better relevance
            with DDGS(timeout=15) as ddgs:
                results_gen = ddgs.text(query, region='in-en', safesearch='off', max_results=num_results)
                search_results = list(results_gen) if results_gen else []

            if search_results:
                results = [
                    {
                        "title": r.get("title", ""),
                        "url": r.get("href", ""),
                        "body": r.get("body", "")
                    }
                    for r in search_results if r.get("href")
                ]
                logging.info(f"Found {len(results)} results (Attempt {attempt+1}).")
                # Cache successful results
                set_cached_search(query, num_results, results)
                return results
            else:
                logging.warning(f"Attempt {attempt+1}: No results found.")
                time.sleep(1)

        except Exception as e:
            logging.error(f"DuckDuckGo Search Error (Attempt {attempt+1}): {e}")
            time.sleep(1)

    # Fallback if search fails after retries
    logging.error("All search attempts failed.")
    return []

def scrape_url(url: str, timeout: int = SCRAPE_TIMEOUT) -> str | None:
    """
    Scrape the main content from a given URL, with Trafilatura fix.

    Args:
        url (str): URL to scrape
        timeout (int): Request timeout in seconds

    Returns:
        str | None: Scraped content or None if scraping fails
    """
    # List of robust User Agents to rotate
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15'
    ]

    current_ua = random.choice(user_agents)

    headers = {
        'User-Agent': current_ua,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1', # Do Not Track
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Pragma': 'no-cache',
        'Cache-Control': 'no-cache'
    }

    # 1. Try Trafilatura
    # PythonAnywhere specific: Bypass proxy for scraped requests if they are failing
    # We can try to set environment variables temporarily or just rely on requests fallback which we can control better

    try:
        # Attempt Trafilatura - simplified call, it handles its own headers usually but we can try to hint it if needed
        # Trafilatura 1.6+ supports config, but simple fetch is usually fine.
        # We rely on fallback if it fails.
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            main_content = trafilatura.extract(
                downloaded, include_comments=False,
                include_tables=False, output_format='txt'
            )
            if main_content and len(main_content) > 100:
                logging.info(f"Trafilatura success ({len(main_content)} chars): {url}")
                return main_content.strip()[:MAX_CONTENT_LENGTH_PER_SITE]
    except Exception as e:
        # Trafilatura failure is common with proxy issues, just log and move to BS4 which we can control
        logging.warning(f"Trafilatura failed for {url}: {e}")

    # 2. Fallback to BeautifulSoup with robust browser headers
    logging.info(f"Falling back to BeautifulSoup for: {url}")
    try:
        response = requests.get(url, timeout=timeout, headers=headers, verify=True)
        response.raise_for_status()
        content_type = response.headers.get('Content-Type', '').lower()
        if 'html' not in content_type: logging.warning(f"Skipping non-HTML ({content_type}): {url}"); return None
        if len(response.content) > 7_000_000: logging.warning(f"Content size exceeds limit: {url}"); return None

        soup = BeautifulSoup(response.content, 'lxml')
        potential_containers = [soup.find('article'), soup.find('main'), soup.find('div', id='content'), soup.find('div', class_='content'), soup.find('div', id='main-content'), soup.find('div', class_='main-content'), soup.find('div', class_='entry-content'), soup.find('div', role='main'), soup]
        text = ""
        for container in potential_containers:
            if container:
                paragraphs = container.find_all('p', recursive=True)
                if paragraphs:
                    extracted_text = ' '.join(p.get_text(strip=True) for p in paragraphs)
                    if len(extracted_text) > 200: text = extracted_text; logging.info(f"BS4 using container '{container.name}' ({len(text)} chars): {url}"); break
        if not text:
             all_paragraphs = soup.find_all('p')
             if all_paragraphs:
                  extracted_text = ' '.join(p.get_text(strip=True) for p in all_paragraphs)
                  if len(extracted_text) > 100: text = extracted_text; logging.info(f"BS4 joining all 'p' tags ({len(text)} chars): {url}");
        if text: return text.strip()[:MAX_CONTENT_LENGTH_PER_SITE]
        else: body_snippet = soup.body.get_text(strip=True, separator=' ')[0:500] if soup.body else "No body"; logging.warning(f"BS4 fallback found no content. Snippet: '{body_snippet}...': {url}"); return None
    except requests.exceptions.Timeout: logging.error(f"Timeout (Requests): {url}"); return None
    except requests.exceptions.HTTPError as e: logging.error(f"HTTP Error {e.response.status_code} (Requests): {url}"); return None
    except requests.exceptions.RequestException as e: logging.error(f"RequestException (Requests) {url}: {e}", exc_info=False); return None
    except Exception as e: logging.error(f"Generic Error BS4 fallback {url}:", exc_info=True); return None


def scrape_urls(search_results: list[dict]) -> list[dict]:
    """
    Scrapes multiple URLs.
    Accepts list of search result dicts: {'url': '...', 'body': '...', ...}
    Falls back to 'body' snippet if scraping fails.
    """
    scraped_data = []; total_content_length = 0; urls_attempted = set()
    logging.info(f"Starting scrape for {len(search_results)} URLs.")

    for i, result in enumerate(search_results):
        url = result.get('url')
        if not url or url in urls_attempted: continue
        urls_attempted.add(url)

        if total_content_length >= MAX_TOTAL_CONTENT_LENGTH:
            logging.warning(f"Reached max total content length."); break

        content = scrape_url(url)

        # Fallback to snippet if scraping failed
        if not content:
            snippet = result.get('body')
            if snippet:
                logging.info(f"Scrape failed for {url}, using snippet fallback.")
                content = f"[Snippet] {snippet}"
            else:
                logging.warning(f"No content or snippet for {url}")

        if content:
            scraped_data.append({"id": len(scraped_data), "url": url, "text": content})
            total_content_length += len(content)
            logging.debug(f"Data success {url}")

        time.sleep(0.3) # Polite delay

    logging.info(f"Finished. Sources: {len(scraped_data)}/{len(urls_attempted)}. Total length: {total_content_length}")
    return scraped_data


def format_context_for_llm(scraped_data: list[dict]) -> str:
    """Formats scraped data for LLM context."""
    context_str = ""
    for item in scraped_data: context_str += f"Source [{item['id']+1}] ({item['url']}):\n{item['text']}\n\n---\n\n"
    return context_str


def preprocess_llm_text_for_citations(text: str) -> str:
    """
    Splits combined citations e.g., [1, 2] -> [1][2] and ensures proper spacing
    between consecutive citation markers.
    """
    # First handle comma-separated citations
    def replacer(match):
        numbers = match.group(1).split(',')
        return " ".join(f"[{num.strip()}]" for num in numbers if num.strip().isdigit())

    # Replace comma-separated citations
    processed_text = re.sub(r"\[\s*(\d+\s*(?:,\s*\d+\s*)*)\s*\]", replacer, text)

    # Add space between consecutive citation markers to ensure they're processed separately
    processed_text = re.sub(r"(\[\d+\])(\[\d+\])", r"\1 \2", processed_text)

    return processed_text


def process_citation_markers_in_html(html_content: str) -> str:
    """
    Improved citation marker processing for HTML content.
    Handles consecutive citations properly with spacing.
    """
    # First pass: Replace all citation markers
    def replace_citation_html(match):
        num = int(match.group(1))
        return f"<sup><a href='#' class='citation-marker' data-citation-index='{num-1}' aria-label='Citation {num}'>[{num}]</a></sup>"

    processed_html = re.sub(r"(?<![!\]/a-zA-Z0-9])\[(\d+)\](?!\])", replace_citation_html, html_content)

    # Second pass: Add spacing between consecutive sup tags for better rendering
    processed_html = re.sub(r'(</sup>)(<sup>)', r'\1 \2', processed_html)

    return processed_html


def generate_chunked_deep_research(client, model_name, prompt, safety_settings, query, context_string):
    """
    Generates deep research content using a single large generation to avoid truncation.
    Falls back to chunked approach if single generation fails.
    """
    logging.info("Using optimized deep research generation")

    def safe_get_text(response, section_name):
        """Safely extract text from response, handling SAFETY blocks."""
        try:
            if not response.candidates:
                # New SDK might handle prompt feedback differently, but simple check:
                logging.warning(f"{section_name}: No candidates returned")
                return None

            candidate = response.candidates[0]
            # Check finish reason (using string comparison for safety)
            finish_reason = getattr(candidate.finish_reason, 'name', str(candidate.finish_reason))
            if finish_reason == "SAFETY":
                logging.warning(f"{section_name}: Content blocked by safety filter")
                return None
            if finish_reason == "MAX_TOKENS":
                logging.warning(f"{section_name}: Hit token limit, but continuing with available content")

            # Use the convenience property if available
            if hasattr(response, 'text') and response.text:
                return response.text

            if hasattr(candidate.content, 'parts') and candidate.content.parts:
                return candidate.content.parts[0].text
            return None
        except Exception as e:
            logging.error(f"{section_name}: Error extracting text: {e}")
            return None

    # Use more permissive safety settings for research content
    research_safety_config = [
        types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_ONLY_HIGH"),
        types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_ONLY_HIGH"),
        types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_ONLY_HIGH"),
        types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_ONLY_HIGH"),
    ]

    # SINGLE COMPREHENSIVE GENERATION - use high token limit for complete content
    comprehensive_prompt = f"""You are a research analyst creating a comprehensive, in-depth report.

User Query: "{query}"

Based on the provided sources, write a COMPLETE and THOROUGH research report. Do NOT cut off content.

Begin directly with the report. Do not emit hidden reasoning, planning tags, or chain-of-thought.

REQUIRED SECTIONS (include ALL of these):

## Executive Summary
A 200-300 word overview of key findings.

## Introduction
Background context, significance, and scope of the topic.

## Key Findings
Organize into 4-6 major themes with ### subheadings. Each theme should have:
- Detailed explanations (300-500 words per section)
- Specific data, statistics, and examples from sources
- Inline citations [N] for every claim

## Analysis & Discussion
- Synthesize patterns and trends
- Compare different perspectives
- Discuss implications

## Recommendations
- Actionable insights
- Specific next steps

## Conclusion
- Key takeaways
- Areas for further research

CRITICAL: At the very end, include this JSON block with 3 follow-up questions:
```json
{{"follow_up_questions": ["Question 1?", "Question 2?", "Question 3?"]}}
```

Sources:
--- START OF SOURCES ---
{context_string}
--- END OF SOURCES ---

Write the complete report now. Cite sources with [N] format. Do NOT truncate - complete ALL sections fully."""

    try:
        # Try single comprehensive generation with high token limit
        logging.info("Attempting single comprehensive generation with 32K tokens")
        response = client.models.generate_content(
            model=model_name,
            contents=comprehensive_prompt,
            config=types.GenerateContentConfig(
                safety_settings=research_safety_config,
                max_output_tokens=32000,
                temperature=0.5
            )
        )
        generated_text = safe_get_text(response, "Comprehensive")

        if generated_text and len(generated_text) > 2000:
            logging.info(f"Single generation successful: {len(generated_text)} chars")
            return generated_text
        else:
            logging.warning("Single generation returned insufficient content, trying fallback")

    except Exception as e:
        logging.warning(f"Single generation failed: {e}, trying chunked fallback")

    # FALLBACK: Chunked generation with increased token limits
    logging.info("Using chunked fallback with increased limits")

    # Part 1: Executive Summary, Introduction, Key Findings (first half)
    part1_prompt = f"""User Query: "{query}"

Write the following sections for a research report. Be thorough and complete.

## Executive Summary (200-300 words)
## Introduction (200-300 words)
## Key Findings - Part 1 (write 2-3 detailed themes with ### subheadings, 400+ words each)

Use inline citations [N] for all claims.

Sources:
--- START OF SOURCES ---
{context_string[:100000]}
--- END OF SOURCES ---"""

    try:
        response1 = client.models.generate_content(
            model=model_name,
            contents=part1_prompt,
            config=types.GenerateContentConfig(
                safety_settings=research_safety_config,
                max_output_tokens=16000,
                temperature=0.5
            )
        )
        generated_text = safe_get_text(response1, "Part1") or ""
    except Exception as e:
        logging.error(f"Part 1 failed: {e}")
        generated_text = ""

    # Part 2: Key Findings (second half), Analysis, Recommendations, Conclusion
    part2_prompt = f"""User Query: "{query}"

Continue the research report with these remaining sections. Be thorough and complete.

## Key Findings - Part 2 (write 2-3 more detailed themes with ### subheadings)
## Analysis & Discussion (synthesize findings, compare perspectives)
## Recommendations (actionable insights)
## Conclusion (key takeaways)

IMPORTANT: End with this JSON block:
```json
{{"follow_up_questions": ["Question about implications?", "Question about applications?", "Question about future?"]}}
```

Use inline citations [N] for all claims.

Sources:
--- START OF SOURCES ---
{context_string[:100000]}
--- END OF SOURCES ---"""

    try:
        response2 = client.models.generate_content(
            model=model_name,
            contents=part2_prompt,
            config=types.GenerateContentConfig(
                safety_settings=research_safety_config,
                max_output_tokens=16000,
                temperature=0.5
            )
        )
        part2_text = safe_get_text(response2, "Part2")
        if part2_text:
            generated_text += "\n\n" + part2_text
    except Exception as e:
        logging.warning(f"Part 2 failed: {e}")
        # Add default follow-up questions
        generated_text += '\n\n```json\n{"follow_up_questions": ["What are the key implications of these findings?", "How can this research be applied in practice?", "What areas require further investigation?"]}\n```'

    if not generated_text:
        raise Exception("All generation attempts failed")

    logging.info(f"Chunked fallback completed: {len(generated_text)} chars")
    return generated_text


def create_gemini_prompt(query: str, context_string: str, depth: str, history: list = None) -> str:
    """Creates the prompt, adjusted for depth and emphasizing deep research requirements."""

    history_str = ""
    if history:
        history_str = "Conversation History:\n"
        for msg in history[-3:]: # Include last 3 messages for context
            history_str += f"User: {msg['query']}\nAI: {msg.get('answer_raw', '')[:200]}...\n"
        history_str += "\n"

    # Base instructions applicable to both modes
    base_instructions = f"""User Query: "{query}"

{history_str}
Sources:
--- START OF SOURCES ---
{context_string}
--- END OF SOURCES ---

General Instructions:
1.  Analyze the User Query and ALL provided Sources meticulously. Ignore irrelevant sources.
2.  **Cite (Inline):** Add `[N]` after information from Source N. Use individual markers `[1][4][5]` for combined info. Place before punctuation. **Accuracy is critical.**
3.  **Source Reliance:** Base the response *exclusively* on the provided sources. NO outside knowledge.
4.  **Clarity & Structure:** Use clear language and Markdown formatting.
5.  **Handling Gaps:** If sources lack info, state it clearly. Do not invent.
5.  **Handling Gaps:** If sources lack info, state it clearly. Do not invent.
6.  **Tone:** Objective, factual, neutral.
7.  **Answer directly:** Do not emit hidden reasoning, planning tags, or chain-of-thought. Begin with the reader-facing answer.
"""

    if depth == 'deep':
        specific_instructions = """
COMPREHENSIVE DEEP RESEARCH REPORT GUIDELINES

You are generating an in-depth research report that synthesizes information from 25-30 high-quality sources. This should be a THOROUGH, DETAILED, and ACADEMICALLY-RIGOROUS document.

REPORT STRUCTURE (ALL SECTIONS REQUIRED):

## Executive Summary
- 200-300 word overview of key findings
- Highlight the most important insights and conclusions

## Introduction
- Background context and significance of the topic
- Clear statement of the research question/objective
- Scope and limitations

## Key Findings
Organize your analysis into 4-6 major thematic sections. For each:
- Use clear ### subheadings for each theme
- Provide detailed explanations (300-500 words per section)
- Include specific data, statistics, and examples from sources
- Compare and contrast different perspectives
- Cite ALL claims with inline source references [N]

## Analysis & Discussion
- Synthesize patterns and trends across sources
- Identify areas of consensus and disagreement
- Discuss implications and significance
- Address any gaps or limitations in the available information

## Expert Perspectives (if applicable)
- Quote or summarize expert opinions from sources
- Include diverse viewpoints

## Practical Applications / Recommendations
- Actionable insights derived from the research
- Specific recommendations based on findings

## Conclusion
- Summarize key takeaways
- Highlight the most significant findings
- Suggest areas for further research

FORMATTING REQUIREMENTS:
- Total length: 2500-4000 words minimum
- Use bullet points and numbered lists for clarity
- Include relevant statistics and specific examples
- Use bold for key terms and concepts
- Ensure every factual claim has a citation [N]
- Maintain academic, objective tone throughout
"""
    else: # Quick depth
        specific_instructions = """
Specific Instructions for Quick Answer:
*   **Goal:** Concise, informative answer synthesizing key points from sources.
*   **Structure:** Use paragraphs, `### Subheadings` (optional), `* Bullet points`.
"""

    # Follow-up questions instruction - VERY EMPHATIC
    follow_up_instruction = """
CRITICAL FINAL INSTRUCTIONS - DO NOT SKIP:

1.  **ANSWER FIRST:**
    *   Begin immediately with the reader-facing Markdown answer.
    *   Do not emit `<thinking>` tags, private reasoning, or planning notes.

2.  **FOLLOW-UP QUESTIONS (MANDATORY):**
    *   After your rendered Markdown response (which comes *after* the thinking block), you MUST end with exactly this JSON block:

    ```json
    {
        "follow_up_questions": [
            "Question 1?",
            "Question 2?",
            "Question 3?"
        ]
    }
    ```

"""

    return base_instructions + specific_instructions + follow_up_instruction + "\nSynthesized Response:"


def synthesize_with_gemini(query: str, scraped_data: list[dict], api_key: str, depth: str, history: list = None) -> dict | None:
    """Synthesizes content using Gemini, adds spacing for citations."""
    if not api_key: return {"error": "AI key missing."}
    if not scraped_data: return {"error": "No content to synthesize."}

    # Initialize Client with new SDK
    client = genai.Client(api_key=api_key)

    context_string = format_context_for_llm(scraped_data)
    prompt = create_gemini_prompt(query, context_string, depth, history)

    estimated_tokens = len(prompt) / 4
    logging.info(f"Sending prompt to Gemini for '{depth}'. Estimated context: ~{estimated_tokens:.0f} tokens.")

    model_name = 'gemini-2.5-flash' if depth == 'deep' else 'gemini-2.5-flash' # Updated to latest model naming conventions
    max_tokens = 65536 # Keep generous
    logging.info(f"Using model: {model_name} (max_tokens={max_tokens})")

    # Relax safety settings to prevent blocking legitimate research on sensitive topics
    # Using new SDK types
    safety_settings = [
        types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
        types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
        types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
        types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE")
    ]

    try:
        # For deep research, use chunked generation to avoid token limit issues
        if depth == 'deep':
            try:
                generated_text_raw = generate_chunked_deep_research(client, model_name, prompt, safety_settings, query, context_string)
                if not generated_text_raw:
                    return {"error": "Failed to generate deep research content (returned None)"}
            except Exception as e:
                logging.error(f"Deep research generation failed: {e}", exc_info=True)
                return {"error": f"Deep research generation failed: {str(e)}"}
        else:
            # For quick research, use single generation
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    safety_settings=safety_settings,
                    max_output_tokens=max_tokens,
                    temperature=0.6
                )
            )

            # Check for errors in response
            # New SDK response error handling might be different, but we check text presence
            if not response.text:
                # Can check candidates if text is empty
                if response.candidates:
                     reason = getattr(response.candidates[0].finish_reason, 'name', str(response.candidates[0].finish_reason))
                     logging.warning(f"Gemini finished unexpectedly: {reason}")
                     return {"error": f"AI failed (Reason: {reason})."}
                else:
                     logging.error("Gemini response empty.")
                     return {"error": "AI returned empty response."}

            generated_text_raw = response.text

        logging.info(f"Generated text raw length: {len(generated_text_raw)}")
        # Log start of text to debug CoT tag issues
        logging.info(f"Generated text raw START (first 1000 chars): {generated_text_raw[:1000]}")

        # --- EXTRACT THINKING BLOCK ---
        # Use robust helper function
        thinking_content, generated_text_raw = extract_thinking_process(generated_text_raw)

        if thinking_content:
             logging.info(f"Removed legacy planning block ({len(thinking_content)} chars).")
        thinking_content = (
            f"Arbor compared {len(scraped_data)} retrieved sources, prioritized directly relevant evidence, "
            "and synthesized the overlapping findings into the answer below."
        )

        if not generated_text_raw.strip():
            logging.error("The model returned no reader-facing answer after removing a planning block.")
            return {"error": "The research model returned no final answer. Please try again."}

        # Extract Follow-up Questions (JSON block) with multiple fallback methods
        follow_up_questions = []

        # Method 1: Try standard JSON code block
        json_match = re.search(r'```json\s*(\{.*?\})\s*```', generated_text_raw, re.DOTALL)

        # Method 2: Try without the "json" language specifier
        if not json_match:
            logging.info("JSON code block not found, trying without language specifier.")
            json_match = re.search(r'```\s*(\{[^`]*"follow_up_questions"[^`]*\})\s*```', generated_text_raw, re.DOTALL)

        # Method 3: Try to find raw JSON object with follow_up_questions
        if not json_match:
            logging.info("Code block not found, trying raw JSON pattern.")
            json_match = re.search(r'(\{\s*"follow_up_questions"\s*:\s*\[.*?\]\s*\})', generated_text_raw, re.DOTALL)

        # Method 4: Try to find just the array of questions
        if not json_match:
            logging.info("JSON object not found, trying to find question array.")
            array_match = re.search(r'"follow_up_questions"\s*:\s*(\[[^\]]+\])', generated_text_raw, re.DOTALL)
            if array_match:
                try:
                    follow_up_questions = json.loads(array_match.group(1))
                    logging.info(f"Extracted {len(follow_up_questions)} questions from array pattern.")
                    generated_text_raw = generated_text_raw.replace(array_match.group(0), "").strip()
                except:
                    pass

        if json_match and not follow_up_questions:
            try:
                json_str = json_match.group(1)
                data = json.loads(json_str)
                follow_up_questions = data.get("follow_up_questions", [])
                logging.info(f"Successfully extracted {len(follow_up_questions)} follow-up questions.")
                # Remove the JSON block from the text to be displayed
                generated_text_raw = generated_text_raw.replace(json_match.group(0), "").strip()
            except Exception as e:
                logging.error(f"Failed to parse follow-up questions JSON: {e}")
                # If parsing fails, we might still want to remove the raw JSON text if it looks like it was intended to be hidden
                if "follow_up_questions" in json_match.group(0):
                     generated_text_raw = generated_text_raw.replace(json_match.group(0), "").strip()

        # Method 5: If still no questions, generate default ones based on the query
        if not follow_up_questions:
            logging.warning("No follow-up questions extracted. Using intelligent defaults.")
            # Extract some keywords from the query for context-aware defaults
            follow_up_questions = [
                f"What are the practical applications of this topic?",
                f"What are the main challenges or limitations?",
                f"What future developments are expected in this area?"
            ]

        # Process the text to ensure proper citation formatting
        generated_text_processed = preprocess_llm_text_for_citations(generated_text_raw)

        # Convert markdown to HTML
        markdown_parser = mistune.create_markdown(renderer='html', plugins=['strikethrough', 'footnotes', 'table', 'task_lists'])
        html_answer = markdown_parser(generated_text_processed)

        # Process citation markers in HTML with improved regex
        # This regex carefully matches individual citation markers
        html_answer = re.sub(
            r"(?<![!\\]/a-zA-Z0-9])\[(\d+)\](?!\])",
            lambda m: f"<sup><a href='#' class='citation-marker' data-citation-index='{int(m.group(1))-1}' aria-label='Citation {m.group(1)}'>[{m.group(1)}]</a></sup> ",  # Note the space after </sup>
            html_answer
        )

        # Clean up any excessive spaces that might have been added
        html_answer = re.sub(r"(\s{2,})", " ", html_answer)

        logging.info(f"Successfully generated response. Processed length: {len(generated_text_processed)}")
        return {
            "answer_raw": generated_text_processed,
            "answer_html": html_answer,
            "follow_up_questions": follow_up_questions,
            "reasoning": thinking_content
        }

    except Exception as e:
        # Error handling (unchanged)
        logging.error(f"Error calling Gemini API: {e}", exc_info=True)
        err_msg = f"AI communication error: {str(e)}"

        if "API key not valid" in str(e) or "invalidated api key" in str(e).lower():
            err_msg = "Invalid Gemini API Key."
        elif "Rate limit exceeded" in str(e) or "quota" in str(e).lower() or "429" in str(e):
            err_msg = "AI model rate limit exceeded. Please try again later or check your API quota."
        elif "User location is not supported" in str(e):
            err_msg = "AI model access restricted by location."
        elif "exceeds the maximum token limit" in str(e).lower() or "token limit" in str(e).lower():
            err_msg = "Content too large for AI model. Try with fewer sources or 'Quick' mode."

        return {"error": err_msg}

# --- UPDATED generate_docx ---
def generate_docx(report_data: dict) -> BytesIO:
    """Generates DOCX, fixing content addition and conditional source list."""
    document = Document()
    try:
        default_font = document.styles['Normal'].font; default_font.name = 'Arial'; default_font.size = Pt(11)
        query = report_data.get('query', 'Untitled Report')
        document.add_heading("Research Report", level=1)
        p = document.add_paragraph(); p.add_run("Query: ").bold = True; p.add_run(query); document.add_paragraph()

        answer_content_processed = report_data.get('answer_raw', 'Content not available.')
        # --- FIX: Refined Markdown to DOCX paragraph handling ---
        current_paragraph = None
        for line in answer_content_processed.split('\n'):
            stripped_line = line.strip()

            # Heading detection
            if stripped_line.startswith('#'):
                current_paragraph = None # End previous paragraph before heading
                level = stripped_line.count('#', 0, 3)
                heading_text = stripped_line.lstrip('# ').strip()
                if heading_text: document.add_heading(heading_text, level=min(level, 3))
                continue

            # List detection (basic, no nesting support here)
            is_list_item = False
            list_content = None
            list_style = 'List Bullet' # Default
            if stripped_line.startswith(('* ', '- ')):
                is_list_item = True
                list_content = stripped_line[2:]
                list_style = 'List Bullet'
            elif re.match(r"^\d+\.\s+", stripped_line):
                 is_list_item = True
                 list_content = re.sub(r"^\d+\.\s+", "", stripped_line)
                 list_style = 'List Number'

            if is_list_item:
                current_paragraph = None # End previous paragraph before list item
                if list_content: # Avoid adding empty list items
                    document.add_paragraph(list_content, style=list_style)
                continue # Move to next line after handling list item

            # Normal paragraph text
            if stripped_line:
                if current_paragraph is None:
                    # Start a new paragraph if not continuing one or after heading/list
                    current_paragraph = document.add_paragraph(stripped_line)
                else:
                    # Add line break *within* the current paragraph
                    current_paragraph.add_run("\n" + stripped_line) # Use Word line break
            elif current_paragraph is not None:
                 # Empty line signifies paragraph break
                 current_paragraph = None

        # --- End FIX ---

        # --- Conditional Sources Section ---
        document.add_page_break()
        research_depth = report_data.get('research_depth', 'quick') # Correctly get depth
        sources_heading_text = "References" if research_depth == 'deep' else "Sources Cited"
        document.add_heading(sources_heading_text, level=1)
        sources = report_data.get('sources', [])
        if sources:
            for i, source in enumerate(sources):
                p = document.add_paragraph(); p.paragraph_format.left_indent = Inches(0.25); p.paragraph_format.first_line_indent = Inches(-0.25)
                p.add_run(f"[{i+1}] ").bold = True; title = source.get('title', 'Source Title Unavailable'); url = source.get('url', '')
                p.add_run(f"{title}")
                if research_depth == 'deep' and url: p.add_run(f" ({url})") # URL only for deep
        else: document.add_paragraph("No sources were cited.")

        file_stream = BytesIO(); document.save(file_stream); file_stream.seek(0)
        logging.info(f"DOCX generated successfully (Depth: {research_depth})")
        return file_stream
    except Exception as e: logging.error(f"Error generating DOCX: {e}", exc_info=True); raise


# --- FIXED generate_pdf ---
def generate_pdf(report_data: dict) -> BytesIO | None:
    """Generates PDF, ensuring depth check and using updated CSS."""
    try:
        query = report_data.get('query', 'Untitled Report')
        answer_html_content = report_data.get('answer_html', '<p>Content not available.</p>')
        sources = report_data.get('sources', [])
        research_depth = report_data.get('research_depth', 'quick') # Get depth

        source_list_html = ""
        if sources:
            list_class = "sources-list " + ("deep-list" if research_depth == 'deep' else "quick-list")
            source_list_html = f'<ul class="{list_class}">'
            for i, source in enumerate(sources):
                title = html.escape(source.get('title', 'Source Title Unavailable'))
                url = html.escape(source.get('url', ''))
                preview = html.escape(source.get('text_preview', ''))
                source_list_html += f'<li><span class="source-number">[{i+1}]</span> <span class="source-title">{title}</span>'
                if research_depth == 'deep' and url: source_list_html += f' <a href="{url}" class="source-url">({url})</a>'
                elif research_depth == 'quick' and url: source_list_html += f'<br><a href="{url}" class="source-url">{url}</a>'
                if research_depth == 'quick' and preview: source_list_html += f'<p class="source-preview">{preview}...</p>'
                source_list_html += '</li>'
            source_list_html += "</ul>"
        else: source_list_html = "<p>No sources were cited.</p>"

        sources_heading_text = "References" if research_depth == 'deep' else "Sources Cited"

        # --- PDF HTML and CSS ---
        # In the generate_pdf function, update the CSS for citation markers:
        # In the generate_pdf function, update the CSS for citation markers:
        pdf_html = f"""
        <!DOCTYPE html><html><head><meta charset="UTF-8"><title>Report: {html.escape(query)}</title><style>
            /* Base styles */
            @page {{ size: a4 portrait; margin: 2cm 1.5cm; @frame header_frame {{ -pdf-frame-content: header_content; left: 1.5cm; width: 18cm; top: 1cm; height: 1cm; }} @frame footer_frame {{ -pdf-frame-content: footer_content; left: 1.5cm; width: 18cm; top: 26.7cm; height: 1cm; }} }}
            body {{ font-family: Arial, Helvetica, sans-serif; font-size: 10pt; line-height: 1.4; color: #333; }}
            #header_content, #footer_content {{ font-size: 9pt; color: #777; }} #header_content {{ text-align: left; }} #footer_content {{ text-align: right; }}
            h1.report-title {{ font-size: 16pt; color: #2c3e50; margin-bottom: 5px; font-weight: bold; }}
            h2.query-title {{ font-size: 11pt; color: #555; font-weight: normal; margin-bottom: 20px; border-bottom: 1px solid #eee; padding-bottom: 10px; }}
            /* Content Styles */
            .answer-content h2 {{ font-size: 14pt; color: #2c3e50; margin-top: 1.2em; margin-bottom: 0.6em; font-weight: bold; border-bottom: 1px solid #ccc; padding-bottom: 3px; }}
            .answer-content h3 {{ font-size: 12pt; color: #2c3e50; margin-top: 1em; margin-bottom: 0.5em; font-weight: bold; border-bottom: 1px solid #eee; padding-bottom: 2px; }}
            .answer-content p {{ margin-bottom: 0.8em; text-align: justify; }}
            .answer-content ul, .answer-content ol {{ margin-left: 20px; margin-bottom: 0.8em; }} .answer-content ul {{ list-style-type: disc; }} .answer-content ol {{ list-style-type: decimal; }} .answer-content li {{ margin-bottom: 0.3em; }}
            .answer-content a {{ color: #0066cc; text-decoration: underline; }}
            /* Citation Marker Style - Improved for PDF */
            .answer-content sup {{ display: inline-block; margin: 0 1px; }}
            sup a.citation-marker {{
                font-size: 0.8em;
                vertical-align: super;
                padding: 1px 2px;
                margin: 0 1px;
                color: #0066cc;
                text-decoration: none;
                background-color: #f0f0f0;
                border-radius: 2px;
                display: inline-block;
            }}
            /* Source List Styles */
            h3.sources-heading {{ font-size: 14pt; color: #2c3e50; margin-top: 25px; margin-bottom: 10px; border-bottom: 1px solid #ccc; padding-bottom: 5px; page-break-before: always; }}
            ul.sources-list {{ list-style-type: none; padding-left: 5px; margin-top: 0; }}
            ul.sources-list li {{ margin-bottom: 8px; font-size: 9pt; line-height: 1.3; }}
            span.source-number {{ font-weight: bold; margin-right: 5px; color: #111; }} span.source-title {{ color: #333; font-weight: 600; }}
            a.source-url {{ color: #0066cc; text-decoration: none; font-size: 0.9em; word-break: break-all; }}
            ul.quick-list li {{ background-color: #f8f8f8; padding: 8px; border: 1px solid #eee; border-radius: 3px;}}
            ul.quick-list a.source-url {{ display: block; margin-top: 2px; }}
            p.source-preview {{ color: #555; font-size: 0.85em; margin-top: 5px; margin-bottom: 0; padding-left: 15px; border-left: 2px solid #ddd; max-height: 4.5em; overflow: hidden; }}
            ul.deep-list li {{ background-color: transparent; padding: 2px 0; border: none; }}
            ul.deep-list a.source-url {{ display: inline; margin-left: 5px; }}
            ul.deep-list p.source-preview {{ display: none; }}

            /* Enhanced Content Styling */
            pre {{ background-color: #f4f4f4; border: 1px solid #ddd; padding: 10px; border-radius: 4px; font-family: Courier; font-size: 9pt; white-space: pre-wrap; }}
            code {{ background-color: #f4f4f4; padding: 2px 4px; border-radius: 2px; font-family: Courier; font-size: 9pt; }}
            blockquote {{ border-left: 3px solid #ccc; margin: 10px 0; padding-left: 10px; color: #666; font-style: italic; }}
            table {{ width: 100%; border-collapse: collapse; margin-bottom: 15px; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; font-size: 9pt; }}
            th {{ background-color: #f2f2f2; font-weight: bold; }}
            img {{ max-width: 100%; height: auto; }}
            </style></head><body>
            <div id="header_content">Research Report</div><div id="footer_content">Page <pdf:pagenumber> of <pdf:pagecount></div>
            <h1 class="report-title">Research Report</h1><h2 class="query-title">Query: {html.escape(query)}</h2>
            <div class="answer-content">{answer_html_content}</div>
            <h3 class="sources-heading">{sources_heading_text}</h3>
            {source_list_html}
            </body></html>"""

        pdf_stream = BytesIO(); pisa_status = pisa.CreatePDF(src=pdf_html, dest=pdf_stream)
        if pisa_status.err: logging.error(f"PDF generation failed: {pisa_status.err}"); return None
        else: pdf_stream.seek(0); logging.info(f"PDF generated successfully (Depth: {research_depth})"); return pdf_stream
    except Exception as e: logging.error(f"Exception during PDF generation: {e}", exc_info=True); return None


# --- Flask App Initialization and Routes ---

def generate_refined_queries(user_query: str, api_key: str) -> list[str]:
    """
    Generates optimized search queries using Gemini to target official sources and comparisons.
    """
    try:
        # Correctly import the new SDK
        from google import genai
        from google.genai import types
        import json

        # Use the global genai client or create new
        client = genai.Client(api_key=api_key)

        prompt = f"""
        You are an expert search engine operator. Your goal is to generate 3 highly effective search queries to answer the user's request thoroughly.

        User Request: "{user_query}"

        Guidelines:
        Analyze the intent of the request (e.g., informational, comparative, technical/official, troubleshooting, etc.) and generate 3 diverse search queries:
        1. Query 1: The most direct and improved version of the user's query.
        2. Query 2: A targeted search for authoritative sources (e.g., using "site:" for documentation if technical, or "review" if shopping, or "official" if news).
        3. Query 3: A complementary query covering a different angle (e.g., specific details, "vs" comparison, or "reddit"/"forum" for community opinions if relevant).

        Output format: Return ONLY a raw JSON list of strings. No markdown, no code blocks.
        Example: ["refined query 1", "refined query 2", "refined query 3"]
        """

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type='application/json'
            )
        )

        if response.text:
            queries = json.loads(response.text)
            if isinstance(queries, list):
                # Ensure we don't go overboard, max 3 queries
                return queries[:3]

        return [user_query] # Fallback

    except Exception as e:
        logging.error(f"Error generating refined queries: {e}")
        return [user_query] # Fallback

def create_app():
    app = Flask(__name__)

    # Initialize MySQL database if available
    init_database()

    # Load history on startup
    load_chat_history()

    # --- Security Configuration ---
    secret_key = os.getenv("FLASK_SECRET_KEY")
    if not secret_key:
        if IS_PRODUCTION:
            logging.critical("FLASK_SECRET_KEY not set! Set it in production.")
            # Generate a random key for this session (not ideal but prevents crash)
            secret_key = os.urandom(32).hex()
        else:
            secret_key = "dev-secret-key-change-in-production"

    app.config['SECRET_KEY'] = secret_key
    app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024  # 1MB max request size
    app.config['GEMINI_API_KEY'] = os.getenv("GEMINI_API_KEY")

    if not app.config['GEMINI_API_KEY']:
        logging.warning("GEMINI_API_KEY not found.")
    else:
        logging.info("GEMINI_API_KEY found.")

    # --- Rate Limiting ---
    limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        default_limits=["200 per day", "100 per hour"],
        storage_uri="memory://",
    )

    # --- Security Headers Middleware ---
    @app.after_request
    def add_security_headers(response):
        # Prevent clickjacking - DISABLED to allow iframe embedding
        # response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        # Prevent MIME type sniffing
        response.headers['X-Content-Type-Options'] = 'nosniff'
        # XSS protection
        response.headers['X-XSS-Protection'] = '1; mode=block'
        # Referrer policy
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        # Cache control for API responses
        if request.path.startswith('/chat') or request.path.startswith('/export'):
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        return response

    # --- Health Check Endpoint ---
    @app.route('/health')
    def health_check():
        return jsonify({
            "status": "healthy",
            "timestamp": time.time(),
            "version": "1.0.0"
        }), 200

    # --- Main Routes ---
    @app.route('/')
    def index():
        return render_template('index.html')

    @app.route('/chat', methods=['POST'])
    @limiter.limit("30 per minute", exempt_when=lambda: request.headers.get('X-API-Key'))
    def process_query_route():
        start_time = time.time()
        data = request.get_json()

        # Input validation
        if not request.is_json:
            return jsonify({"error": "Request must be JSON"}), 400

        query = data.get('query', '').strip()
        if not query:
            return jsonify({"error": "Query cannot be empty."}), 400

        # Security: Limit query length
        if len(query) > MAX_QUERY_LENGTH:
            return jsonify({"error": f"Query too long. Maximum {MAX_QUERY_LENGTH} characters allowed."}), 400

        research_depth = data.get('mode', 'quick')  # Frontend sends 'mode', not 'depth'
        if research_depth not in ['quick', 'deep']:
            research_depth = 'quick'

        chat_id = data.get('chat_id')
        user_id = data.get('user_id') or str(uuid.uuid4())  # Generate if not provided

        # Use User API Key if provided, otherwise System Key
        api_key = request.headers.get('X-API-Key') or current_app.config.get('GEMINI_API_KEY')
        if not api_key: return jsonify({"error": "AI model not configured."}), 500

        # Chat Session Management - now includes user_id verification
        if not chat_id or chat_id not in chat_store:
            chat_id = str(uuid.uuid4())
            chat_store[chat_id] = {
                "messages": [],
                "created_at": time.time(),
                "deep_dive_count": 0,
                "mode": research_depth, # Store the initial mode
                "last_active": time.time(), # Track last active time
                "user_id": user_id  # Associate chat with user
            }
            logging.info(f"Created new chat session: {chat_id} for user: {user_id}")
            save_chat_history(chat_id)
        else:
            # Verify that this chat belongs to this user
            if chat_store[chat_id].get('user_id') and chat_store[chat_id]['user_id'] != user_id:
                return jsonify({"error": "Chat not found or not authorized."}), 403
            # Migrate old chats: if no user_id, assign current user
            if not chat_store[chat_id].get('user_id'):
                chat_store[chat_id]['user_id'] = user_id
                save_chat_history(chat_id)


        chat_session = chat_store[chat_id]

        # Check Session Timeout
        session_timeout = data.get('session_timeout', 3600) # Default 1 hour
        last_active = chat_session.get('last_active', time.time())
        if time.time() - last_active > session_timeout:
             # Session Expired
             del chat_store[chat_id] # Clean up
             save_chat_history() # Full save for deletions (simpler)
             return jsonify({
                 "error": "Session expired.",
                 "limit_reached": True, # Reuse limit modal logic or add new one
                 "message": "Your chat session has expired due to inactivity. Please start a new chat."
             }), 403

        # Update last active
        chat_session['last_active'] = time.time()
        save_chat_history(chat_id)

        # Check Limits
        if len(chat_session["messages"]) >= 10:
            return jsonify({
                "error": "Message limit reached.",
                "limit_reached": True,
                "message": "You have reached the limit of 10 messages for this chat. Please start a new chat."
            }), 403

        if research_depth == 'deep':
            # Deep Research: More sources, more depth
            search_limit = 30
            # You might also want to adjust the prompt or other parameters for deep mode here
        else:
            search_limit = 7  # Quick mode should remain focused and responsive.

        if research_depth == 'deep':
            if chat_session["deep_dive_count"] >= 5:
                return jsonify({
                    "error": "Deep dive limit reached.",
                    "limit_reached": True,
                    "message": "You have reached the limit of 5 deep dive searches for this chat. Please use Quick search or start a new chat."
                }), 403
            chat_session["deep_dive_count"] += 1

        logging.info(f"Processing query: '{query}' [Depth: {research_depth}] [ChatID: {chat_id}]")

        def generate():
            try:
                # 1. Search
                yield f"data: {json.dumps({'type': 'meta', 'data': {'chat_id': chat_id}})}\n\n"

                yield f"data: {json.dumps({'type': 'progress', 'data': '🧠 Refining search queries for better results...'})}\n\n"

                # Generate Refined Queries
                refined_queries = generate_refined_queries(query, api_key)
                logging.info(f"Refined queries: {refined_queries}")

                if research_depth == 'deep':
                     yield f"data: {json.dumps({'type': 'progress', 'data': f'🔍 Step 1/5: Executing {len(refined_queries)} optimized searches...'})}\n\n"
                else:
                     yield f"data: {json.dumps({'type': 'progress', 'data': '🔍 Searching across multiple sources...'})}\n\n"

                num_results = search_limit
                all_search_results = []
                seen_urls = set()

                queries_to_run = refined_queries if research_depth == 'deep' else refined_queries[:2]
                for q_idx, q in enumerate(queries_to_run):
                    # Search for each refined query
                    logging.info(f"Executing search {q_idx+1}/{len(refined_queries)}: {q}")
                    results = perform_search(q, num_results=num_results)

                    for r in results:
                        if r.get('url') and r['url'] not in seen_urls:
                            all_search_results.append(r)
                            seen_urls.add(r['url'])

                    # Small delay to avoid rate limits if looping fast
                    time.sleep(0.5)

                search_results = all_search_results[:search_limit]
                logging.info(f"Total unique results found: {len(search_results)}")

                if research_depth == 'deep':
                     yield f"data: {json.dumps({'type': 'progress', 'data': f'✅ Step 2/5: Found {len(search_results)} unique sources. Preparing to analyze...'})}\n\n"

                urls_to_scrape = [r['url'] for r in search_results if r.get('url')]
                url_to_title_map = {r['url']: r.get('title') for r in search_results if r.get('url')}

                if not urls_to_scrape:
                    yield f"data: {json.dumps({'type': 'error', 'data': 'Could not find relevant web sources.'})}\n\n"
                    return

                if research_depth == 'deep':
                    yield f"data: {json.dumps({'type': 'progress', 'data': f'📖 Step 3/5: Reading and extracting content from {len(urls_to_scrape)} sources...'})}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'progress', 'data': f'📖 Reading {len(urls_to_scrape)} sources...'})}\n\n"

                logging.info(f"Attempting to scrape {len(urls_to_scrape)} URLs.")

                # Pass full search results to allow snippet fallback
                scraped_data = scrape_urls(search_results)

                if not scraped_data:
                    yield f"data: {json.dumps({'type': 'error', 'data': 'Failed to retrieve usable content from web sources.'})}\n\n"
                    return

                success_rate = len(scraped_data) / len(urls_to_scrape) if urls_to_scrape else 0
                if success_rate < 0.4: logging.warning(f"Low scrape success rate ({success_rate:.1%}).")

                if research_depth == 'deep':
                    yield f"data: {json.dumps({'type': 'progress', 'data': f'✅ Step 4/5: Successfully extracted content from {len(scraped_data)} sources.'})}\n\n"

                sources_final = []
                for item in scraped_data:
                    preview = item.get('text', '')[:SOURCE_PREVIEW_LENGTH]
                    sources_final.append({
                        "id": item["id"],
                        "url": item["url"],
                        "title": url_to_title_map.get(item["url"], f"Source {item['id']+1}"),
                        "text_preview": preview
                    })

                # Yield sources immediately
                yield f"data: {json.dumps({'type': 'sources', 'data': sources_final})}\n\n"

                # 3. Synthesize
                if research_depth == 'deep':
                    yield f"data: {json.dumps({'type': 'progress', 'data': '🧠 Step 5/5: Generating comprehensive research report (this may take 30-60 seconds)...'})}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'progress', 'data': '🧠 Synthesizing answer...'})}\n\n"

                # 4. Synthesize Answer with History
                synthesis_result = synthesize_with_gemini(query, scraped_data, api_key, research_depth, history=chat_session["messages"])

                if not synthesis_result or "error" in synthesis_result:
                     error_msg = synthesis_result.get("error", "Synthesis failed.") if synthesis_result else "Synthesis failed."
                     yield f"data: {json.dumps({'type': 'error', 'data': error_msg, 'sources': sources_final, 'research_depth': research_depth})}\n\n"
                     return

                # Yield Reasoning Content (Send FIRST)
                if synthesis_result.get('reasoning'):
                     yield f"data: {json.dumps({'type': 'reasoning', 'data': synthesis_result['reasoning']})}\n\n"

                # Yield Follow-up Questions (Send SECOND)
                if synthesis_result.get("follow_up_questions"):
                     yield f"data: {json.dumps({'type': 'related', 'data': synthesis_result['follow_up_questions']})}\n\n"

                # Store Report
                report_id = str(uuid.uuid4())
                report_data = {
                    "id": report_id,
                    "query": query,
                    "research_depth": research_depth,
                    "sources": sources_final,
                    "answer_raw": synthesis_result["answer_raw"],
                    "answer_html": synthesis_result["answer_html"],
                    "reasoning": synthesis_result.get("reasoning", ""), # Store reasoning
                    "follow_up_questions": synthesis_result.get("follow_up_questions", []),
                    "timestamp": time.time(),
                    "chat_id": chat_id
                }
                add_to_report_store(report_id, report_data)

                # Update Chat Session
                chat_session["messages"].append(report_data)
                save_chat_history(chat_id)

                elapsed_time = time.time() - start_time
                logging.info(f"Request processed in {elapsed_time:.2f}s. Report ID: {report_id}")

                result_data = {
                    "answer_html": synthesis_result["answer_html"],
                    "sources": sources_final,
                    "report_id": report_id,
                    "chat_id": chat_id,
                    "research_depth": research_depth,
                    "follow_up_questions": synthesis_result.get("follow_up_questions", []),
                    "message_count": len(chat_session["messages"]),
                    "deep_dive_count": chat_session["deep_dive_count"]
                }
                # Send pre-rendered HTML for immediate display without client-side parsing issues
                yield f"data: {json.dumps({'type': 'content', 'data': synthesis_result['answer_html']})}\n\n"
                yield f"data: {json.dumps({'type': 'related', 'data': synthesis_result.get('follow_up_questions', [])})}\n\n"

            except Exception as e:
                logging.error(f"Error during generation: {e}")
                logging.error(traceback.format_exc())
                yield f"data: {json.dumps({'type': 'error', 'data': f'An unexpected error occurred: {str(e)}'})}\n\n"

        return Response(stream_with_context(generate()), mimetype='text/event-stream', headers={
            'X-Accel-Buffering': 'no',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive'
        })

    @app.route('/history/<chat_id>', methods=['GET'])
    def get_chat_history(chat_id):
        """
        Retrieves chat history.
        Shared Link Logic:
        - If chat exists, return it regardless of user_id (Read Access for anyone with link).
        - Frontend handles 'is_owner' logic for showing Delete/Edit buttons.
        """
        if chat_id not in chat_store:
            return jsonify({"error": "Chat not found"}), 404

        chat_data = chat_store[chat_id]
        request_user_id = request.args.get('user_id')
        owner_id = chat_data.get('user_id')

        # Add is_owner flag to response for frontend logic
        response_data = chat_data.copy()
        response_data['is_owner'] = (request_user_id == owner_id) if owner_id else True

        return jsonify(response_data)

    @app.route('/chat/<chat_id>', methods=['DELETE'])
    def delete_chat_route(chat_id):
        user_id = request.args.get('user_id')
        if chat_id in chat_store:
            # Verify ownership before deleting
            chat_user_id = chat_store[chat_id].get('user_id')
            if chat_user_id and user_id and chat_user_id != user_id:
                return jsonify({"error": "Chat not found"}), 404
            del chat_store[chat_id]
            save_chat_history()
            logging.info(f"Deleted chat: {chat_id}")
            return jsonify({"success": True}), 200
        return jsonify({"error": "Chat not found"}), 404

    @app.route('/export/<chat_id>/<format>', methods=['GET'])
    @limiter.limit("10 per minute")  # Rate limit exports
    def export_chat(chat_id, format):
        # Validate format
        if format not in ['pdf', 'docx']:
            return jsonify({"error": "Invalid export format. Use 'pdf' or 'docx'."}), 400

        # Verify ownership
        user_id = request.args.get('user_id')
        if chat_id not in chat_store:
            return "Chat not found", 404
        chat_user_id = chat_store[chat_id].get('user_id')
        # Only block if both user_ids exist and don't match
        # Allow access if chat has no user_id (legacy) or if request has no user_id
        if chat_user_id and user_id and chat_user_id != user_id:
            logging.warning(f"Export denied: chat belongs to {chat_user_id}, requested by {user_id}")
            return "Chat not found", 404

        chat_session = chat_store[chat_id]
        messages = chat_session["messages"]

        if format == 'pdf':
            try:
                from xhtml2pdf import pisa
                from io import BytesIO
                import markdown

                # Get the first query as title
                title = messages[0].get('query', 'Research Report') if messages else 'Research Report'
                chat_mode = chat_session.get('mode', 'quick')

                # Professional HTML template
                html_content = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="utf-8">
                    <style>
                        @page {{
                            size: A4;
                            margin: 2cm 2.5cm;
                            @frame header {{
                                -pdf-frame-content: header-content;
                                top: 0.5cm;
                                left: 2.5cm;
                                right: 2.5cm;
                                height: 1cm;
                            }}
                            @frame footer {{
                                -pdf-frame-content: footer-content;
                                bottom: 0.5cm;
                                left: 2.5cm;
                                right: 2.5cm;
                                height: 1cm;
                            }}
                        }}
                        body {{
                            font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
                            font-size: 11pt;
                            line-height: 1.6;
                            color: #1a1a1a;
                        }}
                        #header-content {{
                            text-align: right;
                            font-size: 9pt;
                            color: #666;
                            border-bottom: 1px solid #e0e0e0;
                            padding-bottom: 5px;
                        }}
                        #footer-content {{
                            text-align: center;
                            font-size: 9pt;
                            color: #666;
                            border-top: 1px solid #e0e0e0;
                            padding-top: 5px;
                        }}
                        .cover {{
                            text-align: center;
                            padding-top: 150px;
                        }}
                        .cover h1 {{
                            font-size: 28pt;
                            color: #2563eb;
                            margin-bottom: 20px;
                            font-weight: 600;
                        }}
                        .cover .subtitle {{
                            font-size: 14pt;
                            color: #666;
                        }}
                        .cover .badge {{
                            display: inline-block;
                            background: {'#7c3aed' if chat_mode == 'deep' else '#3b82f6'};
                            color: white;
                            padding: 5px 15px;
                            border-radius: 20px;
                            font-size: 10pt;
                            margin-top: 20px;
                        }}
                        h2 {{
                            font-size: 14pt;
                            color: #1e40af;
                            border-bottom: 2px solid #e0e0e0;
                            padding-bottom: 8px;
                            margin-top: 30px;
                        }}
                        h3 {{
                            font-size: 12pt;
                            color: #374151;
                        }}
                        .question {{
                            background: #f8fafc;
                            padding: 15px 20px;
                            border-left: 4px solid #2563eb;
                            margin: 25px 0 15px 0;
                            font-weight: 600;
                            font-size: 12pt;
                            color: #1e3a5f;
                        }}
                        .answer {{
                            padding: 0 10px;
                        }}
                        .answer p {{
                            margin-bottom: 12px;
                            text-align: justify;
                        }}
                        .answer ul, .answer ol {{
                            margin: 10px 0 10px 20px;
                        }}
                        .answer li {{
                            margin-bottom: 6px;
                        }}
                        strong {{
                            color: #1e40af;
                        }}
                        code {{
                            background: #f1f5f9;
                            padding: 2px 6px;
                            border-radius: 4px;
                            font-family: 'Courier New', monospace;
                            font-size: 10pt;
                        }}
                        pre {{
                            background: #1e293b;
                            color: #e2e8f0;
                            padding: 15px;
                            border-radius: 8px;
                            font-family: 'Courier New', monospace;
                            font-size: 9pt;
                            overflow-x: auto;
                            white-space: pre-wrap;
                        }}
                        blockquote {{
                            border-left: 3px solid #3b82f6;
                            margin: 15px 0;
                            padding: 10px 20px;
                            background: #eff6ff;
                            font-style: italic;
                            color: #334155;
                        }}
                        table {{
                            width: 100%;
                            border-collapse: collapse;
                            margin: 15px 0;
                        }}
                        th, td {{
                            border: 1px solid #e5e7eb;
                            padding: 10px 12px;
                            text-align: left;
                            font-size: 10pt;
                        }}
                        th {{
                            background: #f8fafc;
                            font-weight: 600;
                            color: #374151;
                        }}
                        .sources {{
                            margin-top: 40px;
                            padding-top: 20px;
                            border-top: 2px solid #e5e7eb;
                        }}
                        .sources h2 {{
                            color: #374151;
                        }}
                        .source-item {{
                            margin: 8px 0;
                            font-size: 10pt;
                        }}
                        .source-item a {{
                            color: #2563eb;
                            text-decoration: none;
                        }}
                        hr {{
                            border: none;
                            border-top: 1px solid #e5e7eb;
                            margin: 30px 0;
                        }}
                    </style>
                </head>
                <body>
                    <div id="header-content">{'Deep Research' if chat_mode == 'deep' else 'Quick Search'} Report</div>
                    <div id="footer-content">Page <pdf:pagenumber> of <pdf:pagecount></div>

                    <div class="cover">
                        <h1>{html.escape(title[:100])}</h1>
                        <div class="subtitle">Generated Research Report</div>
                        <div class="badge">{'🔬 Deep Research' if chat_mode == 'deep' else '⚡ Quick Search'}</div>
                    </div>

                    <pdf:nextpage />
                """

                # Add each message
                for i, msg in enumerate(messages):
                    query = msg.get('query', 'Question')
                    answer_raw = msg.get('answer_raw', '')
                    answer_html = msg.get('answer_html', '')
                    sources = msg.get('sources', [])

                    # Convert markdown to HTML if needed
                    if not answer_html and answer_raw:
                        try:
                            answer_html = markdown.markdown(answer_raw, extensions=['tables', 'fenced_code'])
                        except:
                            answer_html = f"<p>{answer_raw}</p>"

                    html_content += f"""
                    <div class="question">{html.escape(query)}</div>
                    <div class="answer">{answer_html}</div>
                    """

                    # Add sources if available
                    if sources:
                        html_content += '<div class="sources"><h2>📚 Sources</h2>'
                        for idx, src in enumerate(sources, 1):
                            title_src = src.get('title', 'Source')
                            url = src.get('url', '#')
                            html_content += f'<div class="source-item">[{idx}] <a href="{url}">{html.escape(title_src)}</a></div>'
                        html_content += '</div>'

                    if i < len(messages) - 1:
                        html_content += '<hr />'

                html_content += "</body></html>"

                # Convert to PDF
                pdf_buffer = BytesIO()
                pisa_status = pisa.CreatePDF(html_content, dest=pdf_buffer)

                if pisa_status.err:
                    return f"Error creating PDF: {pisa_status.err}", 500

                pdf_buffer.seek(0)
                return send_file(
                    pdf_buffer,
                    as_attachment=True,
                    download_name=f"research_report_{chat_id[:8]}.pdf",
                    mimetype='application/pdf'
                )

            except ImportError as e:
                logging.error(f"PDF Import Error: {e}")
                return f"PDF library error: {e}. Try: pip install xhtml2pdf", 500
            except Exception as e:
                logging.error(f"PDF Export Error: {e}", exc_info=True)
                return f"Error exporting PDF: {e}", 500

        elif format == 'docx':
            try:
                from docx import Document
                from docx.shared import Pt
                from io import BytesIO
                import re

                doc = Document()
                doc.add_heading('Chat Export', 0)

                for msg in messages:
                    query = msg.get('query', 'User Query')
                    answer_raw = msg.get('answer_raw', '')

                    # User Query
                    p = doc.add_paragraph()
                    runner = p.add_run(f"User: {query}")
                    runner.bold = True
                    runner.font.size = Pt(12)

                    # AI Answer (Strip Markdown for simple docx)
                    # A better approach would be to parse markdown to docx, but for now simple text
                    clean_answer = re.sub(r'\*\*(.*?)\*\*', r'\1', answer_raw) # Bold
                    clean_answer = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', clean_answer) # Links

                    doc.add_paragraph(clean_answer)
                    doc.add_paragraph("-" * 40) # Separator

                docx_buffer = BytesIO()
                doc.save(docx_buffer)
                docx_buffer.seek(0)

                return send_file(
                    docx_buffer,
                    as_attachment=True,
                    download_name=f"chat_{chat_id}.docx",
                    mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                )

            except ImportError:
                return "python-docx not installed", 500
            except Exception as e:
                logging.error(f"Docx Export Error: {e}")
                return f"Error exporting Docx: {e}", 500

        return "Invalid format", 400

    @app.route('/download/docx/<report_id>')
    def download_docx_route(report_id):
        # (generate_docx now correctly checks depth from report_data)
        report_data = report_store.get(report_id)
        if not report_data: return "Report not found or has expired.", 404
        try: file_stream = generate_docx(report_data); query_slug = "".join(c if c.isalnum() else "_" for c in report_data.get('query', 'report'))[:30].strip('_'); filename = f"Research_Report_{query_slug or 'report'}.docx"; logging.info(f"Serving DOCX: {filename}"); return send_file(file_stream, mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document', as_attachment=True, download_name=filename)
        except Exception as e: logging.error(f"Error generating/sending DOCX: {e}", exc_info=True); return "Error generating DOCX file.", 500 # Log exception detail

    @app.route('/download/pdf/<report_id>')
    def download_pdf_route(report_id):
        # (generate_pdf now correctly checks depth from report_data)
        report_data = report_store.get(report_id)
        if not report_data: return "Report not found or has expired.", 404
        try:
            pdf_stream = generate_pdf(report_data)
            if not pdf_stream: logging.error(f"PDF generation returned None for report {report_id}"); return "Error generating PDF file (check server logs).", 500
            query_slug = "".join(c if c.isalnum() else "_" for c in report_data.get('query', 'report'))[:30].strip('_'); filename = f"Research_Report_{query_slug or 'report'}.pdf"; logging.info(f"Serving PDF: {filename}"); response = make_response(pdf_stream.getvalue()); response.headers['Content-Type'] = 'application/pdf'; response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'; return response
        except Exception as e: logging.error(f"Error generating/sending PDF: {e}", exc_info=True); return "Error generating PDF file.", 500 # Log exception detail


    @app.route('/history', methods=['GET'])
    def get_history():
        """Returns a list of past chat sessions."""
        history = []
        # Sort chats by creation time (newest first)
        sorted_chats = sorted(chat_store.items(), key=lambda item: item[1]['created_at'], reverse=True)

        for chat_id, chat_data in sorted_chats:
            # Use the first query as the title
            first_query = "New Chat"
            if chat_data["messages"]:
                first_query = chat_data["messages"][0].get("query", "Untitled")

            history.append({
                "id": chat_id,
                "query": first_query, # Display first query as title
                "timestamp": chat_data.get("created_at", time.time())
            })
        return jsonify(history)



    @app.route('/report/<report_id>', methods=['GET'])
    def get_report_json(report_id):
        """Returns the JSON data for a specific report."""
        data = report_store.get(report_id)
        if not data:
            return jsonify({"error": "Report not found"}), 404
        return jsonify(data)

    @app.after_request
    def after_request(response):
        """Allows embedding in iframes by setting CSP."""
        response.headers.pop('X-Frame-Options', None) # Remove if present
        # Explicitly allow embedding everywhere (including local file:// basics for testing)
        response.headers['Content-Security-Policy'] = "frame-ancestors 'self' https: http: file: *"
        return response

    return app


# --- Main Execution ---

# Create app at global scope for WSGI
app = create_app()

if __name__ == '__main__':
    # Use FLASK_ENV or FLASK_DEBUG for debug mode, default to True for local dev only
    debug_mode = os.getenv('FLASK_DEBUG', 'True').lower() == 'true'
    app.run(debug=debug_mode, host='0.0.0.0', port=5000)

# --- END OF FILE app.py ---
