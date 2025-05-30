# AI-Research-Assistant



[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/Python-3.x-blue.svg)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-Framework-lightgrey.svg)](https://flask.palletsprojects.com/)

## ✨ Overview

The **AI Research Assistant** is a web application designed to revolutionize how users conduct research and synthesize information. It automates the process of gathering data from the web, synthesizing it into structured reports using advanced AI models, and providing clear, verifiable citations. Whether you need a quick overview or an in-depth academic article, this tool delivers comprehensive, well-sourced content.

## 🚀 Features

*   **Smart Web Search & Content Extraction:**
    *   Utilizes the DuckDuckGo Search API for efficient discovery of relevant online sources.
    *   Employs robust web scraping techniques (Trafilatura & BeautifulSoup) to intelligently extract and clean main content from diverse URLs, prioritizing quality and relevance.
*   **AI-Powered Content Synthesis (Gemini):**
    *   Leverages the Google Gemini API to synthesize scraped information into coherent, well-structured articles and reports.
    *   Offers two research depths:
        *   **Quick Research:** Provides concise, summarized answers (~7 sources).
        *   **Deep Research:** Generates comprehensive academic-style articles (Title, Abstract, Introduction, Literature Review, Analysis, Conclusion, References) with extended source analysis (~15 sources).
*   **Transparent Sourcing with Inline Citations:**
    *   Automatically generates inline numbered citations (`[N]`) for every piece of information derived from a source.
    *   Features interactive JavaScript pop-ups on citation markers, displaying source title, URL, and a text preview for instant verification.
*   **Flexible Report Export Options:**
    *   Enables seamless export of generated reports into professional Microsoft Word (DOCX) and Portable Document Format (PDF) files.
*   **User-Friendly Interface & Reliable Operation:**
    *   Designed with a responsive and intuitive frontend for a smooth user experience.
    *   Incorporates robust error handling, intelligent content length management, and polite scraping delays to ensure reliable and efficient operation.

## 💻 Technologies Used

**Backend:**
*   **Python 3.x:** Core programming language.
*   **Flask:** Web framework for the application.
*   **`google-generativeai`:** For interacting with Google Gemini API.
*   **`duckduckgo-search`:** For web search functionality.
*   **`requests`:** For making HTTP requests during scraping.
*   **`bs4` (BeautifulSoup):** For HTML parsing and fallback scraping.
*   **`trafilatura`:** For high-quality main content extraction from web pages.
*   **`mistune`:** For converting Markdown to HTML for display.
*   **`python-docx`:** For generating `.docx` (Microsoft Word) documents.
*   **`xhtml2pdf` / `pisa`:** For generating `.pdf` documents from HTML.
*   **`python-dotenv`:** For managing environment variables.
*   **`logging`:** For application logging.
*   **`uuid`, `time`, `html`, `re`:** Standard Python libraries for various utilities.

**Frontend:**
*   **HTML5:** Structure of the web pages.
*   **CSS3:** Styling (custom `style.css`).
*   **JavaScript:** Client-side interactivity, AJAX calls, and citation popups.

## ⚙️ Installation

To get a local copy up and running, follow these simple steps.

### Prerequisites

*   Python 3.8+
*   `pip` (Python package installer)

### Steps

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/your-username/ai-research-assistant.git
    cd ai-research-assistant
    ```

2.  **Create and activate a virtual environment:**
    ```bash
    python -m venv venv
    # On Windows:
    .\venv\Scripts\activate
    # On macOS/Linux:
    source venv/bin/activate
    ```

3.  **Install dependencies:**
    First, ensure you have a `requirements.txt` file. If not, generate one from the existing code:
    ```bash
    pip install Flask google-generativeai duckduckgo-search requests beautifulsoup4 trafilatura mistune python-docx xhtml2pdf python-dotenv
    # Then generate requirements.txt
    pip freeze > requirements.txt
    ```
    Now, install them:
    ```bash
    pip install -r requirements.txt
    ```

4.  **Set up environment variables:**
    Create a file named `.env` in the root directory of the project (where `app.py` is located) and add your API keys:
    ```
    GEMINI_API_KEY="YOUR_GOOGLE_GEMINI_API_KEY"
    FLASK_SECRET_KEY="a_strong_random_secret_key_for_flask_sessions"
    ```
    Replace `"YOUR_GOOGLE_GEMINI_API_KEY"` with your actual key obtained from [Google AI Studio](https://aistudio.google.com/app/apikey). You can generate a random string for `FLASK_SECRET_KEY`.

5.  **Run the application:**
    ```bash
    flask --app app.py run --debug
    ```
    The `--debug` flag is useful for development as it provides auto-reloading and a debugger. For production, you'd typically run it with a WSGI server like Gunicorn.

## 🚀 Usage

Once the application is running:

1.  Open your web browser and navigate to `http://127.0.0.1:5000/`.
2.  Enter your research query in the input field (e.g., "What are the effects of climate change on coral reefs?").
3.  Select your desired research depth:
    *   **Quick:** For concise answers.
    *   **Deep Research:** For comprehensive, academic-style articles.
4.  Click the "Ask" button.
5.  The AI will process your query, perform web searches, scrape content, and synthesize an answer. This might take a moment, especially for "Deep Research."
6.  Once the results are displayed, you can:
    *   Review the synthesized answer.
    *   Hover over citation markers (e.g., `[1]`) to see source details.
    *   Scroll down to view the "Sources Cited" list, which dynamically adapts based on the research depth.
    *   Use the "Download DOCX" or "Download PDF" buttons to save the report.

## 📁 Project Structure

```
.
├── app.py                     # Main Flask application logic
├── requirements.txt           # Python dependencies
├── .env.example               # Example .env file for configuration
├── static/
│   ├── css/
│   │   └── style.css          # Frontend styling
│   └── js/
│       └── script.js          # Frontend JavaScript for interactivity
└── templates/
    └── index.html             # Main HTML template for the UI
```

## 🙏 Contributing

Contributions are what make the open-source community such an amazing place to learn, inspire, and create. Any contributions you make are **greatly appreciated**.

If you have a suggestion that would make this better, please fork the repo and create a pull request. You can also simply open an issue with the tag "enhancement".

1.  Fork the Project
2.  Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3.  Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4.  Push to the Branch (`git push origin feature/AmazingFeature`)
5.  Open a Pull Request

## 📄 License

Distributed under the MIT License. See `LICENSE` for more information.

## 📞 Contact

Jayachandran PM - jayachandranpm2001@gmail.com

Project Link: https://github.com/jayachandranpm/AI-Research-Assistant
---

**Before you upload:**

1.  **Create `requirements.txt`:** Run `pip freeze > requirements.txt` in your activated virtual environment to generate this file with exact versions.
2.  **Create `.env.example`:** Copy your `.env` file to `.env.example` and replace your actual API keys with placeholders like `YOUR_GOOGLE_GEMINI_API_KEY`. This shows others what variables they need to set up.
3.  **Create `LICENSE` file:** A simple text file with the MIT license text. You can find it [here](https://opensource.org/licenses/MIT).
4.  **Add a GIF/Screenshot:** A short GIF or a few screenshots of the application in action will significantly boost its appeal on GitHub. You can replace the "See it in action!" placeholder with a link to your GIF or embed it directly.
5.  **Update Links:** Make sure to replace `https://github.com/jayachandranpm/AI-Research-Assistant` with the actual URL of your repository!
