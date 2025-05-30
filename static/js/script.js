document.addEventListener('DOMContentLoaded', () => {
    // --- DOM Elements ---
    const searchForm = document.getElementById('search-form');
    const queryInput = document.getElementById('query-input');
    const submitButton = document.getElementById('submit-button');
    const statusArea = document.getElementById('status-area');
    const loadingIndicator = document.getElementById('loading-indicator');
    const errorMessage = document.getElementById('error-message');
    const resultsArea = document.getElementById('results-area');
    const answerContent = document.getElementById('answer-content');
    const sourcesHeading = document.getElementById('sources-heading');
    const sourcesList = document.getElementById('sources-list');
    const downloadOptions = document.getElementById('download-options');
    const downloadDocxButton = document.getElementById('download-docx');
    const downloadPdfButton = document.getElementById('download-pdf');
    const reportIdStorage = document.getElementById('report-id-storage');
    const citationPopup = document.getElementById('citation-popup');

    let sourcesData = [];
    let popupHideTimeout = null;
    let currentResearchDepth = 'quick'; // Store depth used for current result

    // --- Event Listeners ---
    searchForm.addEventListener('submit', handleSearchSubmit);
    downloadDocxButton.addEventListener('click', handleDownload('docx'));
    downloadPdfButton.addEventListener('click', handleDownload('pdf'));
    document.body.addEventListener('click', (event) => {
        if (!citationPopup.contains(event.target) && !event.target.closest('.citation-marker')) { // Check ancestor too
            hideCitationPopup();
        }
    });
    citationPopup.addEventListener('mouseenter', () => clearTimeout(popupHideTimeout));
    citationPopup.addEventListener('mouseleave', () => popupHideTimeout = setTimeout(hideCitationPopup, 150));

    // --- Functions ---
    async function handleSearchSubmit(event) {
        event.preventDefault();
        const query = queryInput.value.trim();
        currentResearchDepth = document.querySelector('input[name="depth"]:checked').value; // Store depth

        if (!query) { showError("Please enter a query."); queryInput.focus(); return; }

        setLoadingState(true); hideError(); clearResults();

        try {
            const response = await fetch('/process', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify({ query: query, depth: currentResearchDepth }), // Send depth
            });
            const data = await response.json();
            // Store depth from response (in case backend modifies it, though unlikely here)
            currentResearchDepth = data.research_depth || currentResearchDepth;

            if (!response.ok) throw new Error(data.error || `HTTP error! Status: ${response.status}`);

            if (data.answer_html && data.sources) {
                displayResults(data);
            } else if (data.error && data.sources) {
                 showError(data.error + " Displaying sources found.");
                 // Use the returned depth to decide source list format even on error
                 renderSourceList(data.sources, currentResearchDepth);
            } else {
                 throw new Error(data.error || "Received unexpected data from server.");
            }
        } catch (error) {
            console.error('Error processing query:', error);
            showError(`Failed to get results: ${error.message}`);
        } finally {
            setLoadingState(false);
        }
    }

    function setLoadingState(isLoading) { /* (Keep setLoadingState as before) */
        if (isLoading) { loadingIndicator.style.display = 'flex'; submitButton.disabled = true; queryInput.disabled = true; statusArea.setAttribute('aria-busy', 'true'); }
        else { loadingIndicator.style.display = 'none'; submitButton.disabled = false; queryInput.disabled = false; statusArea.removeAttribute('aria-busy'); }
    }
    function showError(message) { /* (Keep showError as before) */
        errorMessage.textContent = message; errorMessage.style.display = 'block'; errorMessage.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
    function hideError() { /* (Keep hideError as before) */
        errorMessage.style.display = 'none'; errorMessage.textContent = '';
    }
    function clearResults() { /* (Keep clearResults as before) */
        answerContent.innerHTML = ''; sourcesList.innerHTML = ''; sourcesHeading.style.display = 'none';
        resultsArea.style.display = 'none'; downloadOptions.style.display = 'none';
        reportIdStorage.value = ''; sourcesData = []; hideCitationPopup();
        sourcesList.className = ''; // Clear depth-specific class
    }

    function displayResults(data) {
        answerContent.innerHTML = data.answer_html;
        sourcesData = data.sources || [];
        // Render source list based on the depth returned by the backend
        renderSourceList(sourcesData, data.research_depth);
        initializeCitationPopups();
        resultsArea.style.display = 'block';
        if (data.report_id) {
            reportIdStorage.value = data.report_id;
            downloadOptions.style.display = 'block';
        }
     }

    // --- NEW: Conditional Source List Rendering ---
    function renderSourceList(sources, depth) {
        sourcesData = sources || [];
        sourcesList.innerHTML = ''; // Clear previous
        sourcesList.className = ''; // Clear old classes

        if (sourcesData.length > 0) {
            sourcesHeading.style.display = 'block'; // Show heading if there are sources

            if (depth === 'deep') {
                sourcesList.classList.add('deep-list'); // Add class for deep styling
                sourcesData.forEach(source => {
                    const li = document.createElement('li');
                    const titleSpan = document.createElement('span');
                    titleSpan.className = 'source-title';
                    titleSpan.textContent = source.title || 'Source Title Unavailable';
                    li.appendChild(titleSpan);

                    if (source.url) {
                        const link = document.createElement('a');
                        link.className = 'source-url'; // Add class for styling
                        link.href = source.url;
                        link.textContent = `(${source.url})`; // Wrap URL in parenthesis
                        link.target = '_blank';
                        link.rel = 'noopener noreferrer';
                        li.appendChild(document.createTextNode(' ')); // Add space before URL
                        li.appendChild(link);
                    }
                    sourcesList.appendChild(li);
                });
            } else { // Default to 'quick' list style
                sourcesList.classList.add('quick-list'); // Add class for quick styling
                sourcesData.forEach(source => {
                    const li = document.createElement('li');
                    const titleSpan = document.createElement('span');
                    titleSpan.className = 'source-title';
                    titleSpan.textContent = source.title || 'Source Title Unavailable';
                    li.appendChild(titleSpan);

                    if (source.url) {
                        const link = document.createElement('a');
                        link.className = 'source-url';
                        link.href = source.url;
                        link.textContent = source.url;
                        link.target = '_blank';
                        link.rel = 'noopener noreferrer';
                        li.appendChild(link);
                    }
                    if (source.text_preview) {
                        const previewP = document.createElement('p');
                        previewP.className = 'source-preview';
                        previewP.textContent = source.text_preview + '...';
                        li.appendChild(previewP);
                    }
                    sourcesList.appendChild(li);
                });
            }
            resultsArea.style.display = 'block'; // Ensure results area is visible
        } else {
             sourcesHeading.style.display = 'none'; // Hide heading if no sources
        }
    }

    // --- Custom Citation Popup Logic (Keep as before) ---
    function initializeCitationPopups() {
        answerContent.querySelectorAll('a.citation-marker').forEach(marker => {
            marker.addEventListener('mouseenter', handleMarkerMouseEnter);
            marker.addEventListener('mouseleave', handleMarkerMouseLeave);
            marker.addEventListener('focus', handleMarkerMouseEnter);
            marker.addEventListener('blur', handleMarkerMouseLeave);
            marker.addEventListener('click', (e) => e.preventDefault());
        });
    }
    function handleMarkerMouseEnter(event) { /* (Keep as before) */
        clearTimeout(popupHideTimeout); const marker = event.target.closest('.citation-marker'); if (!marker) return; // Ensure we target the link
        try { const index = parseInt(marker.getAttribute('data-citation-index'), 10); const source = sourcesData.find(s => s.id === index); if (source) { showCitationPopup(marker, source); } else { console.warn(`Source data missing for index: ${index}`); hideCitationPopup(); } }
        catch (e) { console.error("Popup error:", e); hideCitationPopup(); }
    }
    function handleMarkerMouseLeave() { /* (Keep as before) */
        popupHideTimeout = setTimeout(hideCitationPopup, 150);
    }
    function showCitationPopup(markerElement, sourceData) { /* (Keep as before - populates and positions popup) */
        const titleStrong = document.createElement('strong'); titleStrong.textContent = sourceData.title || 'Source Title Unavailable';
        let linkHTML = ''; if (sourceData.url) { const link = document.createElement('a'); link.href = sourceData.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = sourceData.url; linkHTML = link.outerHTML; }
        let previewHTML = ''; if (sourceData.text_preview) { const previewP = document.createElement('p'); previewP.className = 'popup-preview'; previewP.textContent = sourceData.text_preview + '...'; previewHTML = previewP.outerHTML; }
        citationPopup.innerHTML = `${titleStrong.outerHTML}${linkHTML}${previewHTML}`;
        const markerRect = markerElement.getBoundingClientRect(); const vpWidth = window.innerWidth; const vpHeight = window.innerHeight; const scrollX = window.scrollX; const scrollY = window.scrollY;
        citationPopup.style.display = 'block'; /* Make it measurable */ const popW = citationPopup.offsetWidth; const popH = citationPopup.offsetHeight; citationPopup.style.display = ''; /* Hide again before positioning */
        let top = markerRect.top + scrollY - popH - 10; let left = markerRect.left + scrollX + (markerRect.width / 2) - (popW / 2);
        if (left < scrollX + 5) left = scrollX + 5; else if (left + popW > vpWidth + scrollX - 5) left = vpWidth + scrollX - popW - 5;
        if (top < scrollY + 5 || markerRect.top < popH + 15) { top = markerRect.bottom + scrollY + 10; if (top + popH > vpHeight + scrollY - 5) top = Math.max(scrollY + 5, markerRect.top + scrollY - popH - 10); }
        citationPopup.style.left = `${left}px`; citationPopup.style.top = `${top}px`;
        citationPopup.classList.add('visible'); citationPopup.setAttribute('aria-hidden', 'false');
    }
    function hideCitationPopup() { /* (Keep as before) */
        citationPopup.classList.remove('visible'); citationPopup.setAttribute('aria-hidden', 'true');
    }

    // --- Download Handler (Keep as before) ---
    function handleDownload(format) { /* (Keep as before) */
        return () => { const reportId = reportIdStorage.value; if (reportId) { window.location.href = `/download/${format}/${reportId}`; } else { console.error("No report ID for download."); showError("Cannot download: Report ID missing."); } };
    }

}); // End DOMContentLoaded
