let activeChatId = null; // Global state for exportChat

// User ID management - each browser gets a unique persistent ID
function getUserId() {
    let userId = localStorage.getItem('arbor_user_id');
    if (!userId) {
        userId = crypto.randomUUID();
        localStorage.setItem('arbor_user_id', userId);
        console.log('Generated new user ID:', userId);
    }
    return userId;
}

// Security: HTML escape function to prevent XSS attacks
function escapeHtml(text) {
    if (typeof text !== 'string') return text;
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

document.addEventListener('DOMContentLoaded', () => {
    // DOM Elements
    const queryInput = document.getElementById('query-input');
    const submitBtn = document.getElementById('submit-btn');
    const searchForm = document.getElementById('search-form');
    const searchContainer = document.getElementById('search-container');
    const resultsArea = document.getElementById('results-area');
    const displayQuery = document.getElementById('display-query');
    const sourcesList = document.getElementById('sources-list');
    const answerContent = document.getElementById('answer-content');
    const historyList = document.getElementById('history-list');
    const newChatBtn = document.getElementById('new-chat-btn');
    const sidebar = document.getElementById('sidebar');
    const mobileMenuBtn = document.getElementById('mobile-menu-btn');
    const sidebarOverlay = document.getElementById('sidebar-overlay');

    // Load history from localStorage on startup
    loadHistoryFromStorage();

    // Event Listeners
    const sidebarCollapseBtn = document.getElementById('sidebar-collapse-btn');
    const modeQuickBtn = document.getElementById('mode-quick');
    const modeDeepBtn = document.getElementById('mode-deep');

    // Settings Elements
    const settingsBtn = document.getElementById('settings-btn');
    const settingsModal = document.getElementById('settings-modal');
    const closeSettingsBtn = document.getElementById('close-settings-btn');
    const themeSelect = document.getElementById('theme-select');
    const apiKeyInput = document.getElementById('api-key-input');
    const sessionTimeoutInput = document.getElementById('session-timeout-input');
    const saveSettingsBtn = document.getElementById('save-settings-btn');

    // Section Elements
    const sourcesSection = document.getElementById('sources-section');
    const answerSection = document.querySelector('.answer-section');


    // State
    let isGenerating = false;
    let searchMode = 'quick'; // 'quick' or 'deep'
    // let activeChatId = null; // Moved to global scope

    // Check if URL contains a chat ID (must be after state declarations)
    const urlParams = new URLSearchParams(window.location.search);
    const urlChatId = urlParams.get('chat');
    if (urlChatId) {
        loadChat(urlChatId);
    }

    // Initialize Settings
    loadSettings();

    // --- Event Listeners ---

    // Auto-resize textarea
    queryInput.addEventListener('input', function () {
        this.style.height = 'auto';
        this.style.height = (this.scrollHeight) + 'px';
        submitBtn.disabled = this.value.trim() === '';
    });

    // Export Buttons Logic
    // Removed sidebar export logic
    // const exportContainer = document.getElementById('export-container');
    // if (exportContainer) exportContainer.style.display = 'none'; // Hide by default

    // const exportPdfBtn = document.getElementById('export-pdf-btn');
    // const exportDocxBtn = document.getElementById('export-docx-btn');

    // if (exportPdfBtn) {
    //     exportPdfBtn.addEventListener('click', () => {
    //         if (activeChatId) window.open(`/export/${activeChatId}/pdf`, '_blank');
    //     });
    // }

    // if (exportDocxBtn) {
    //     exportDocxBtn.addEventListener('click', () => {
    //         if (activeChatId) window.open(`/export/${activeChatId}/docx`, '_blank');
    //     });
    // }

    // Submit Form
    searchForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const query = queryInput.value.trim();
        if (!query || isGenerating) return;

        startSearch(query);
    });

    // Enter key to submit (Shift+Enter for new line)
    queryInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            searchForm.dispatchEvent(new Event('submit'));
        }
    });

    // New Chat
    newChatBtn.addEventListener('click', () => {
        resetUI();
    });

    // Mobile Sidebar
    mobileMenuBtn.addEventListener('click', () => {
        sidebar.classList.add('open');
        sidebarOverlay.style.display = 'block';
    });

    sidebarOverlay.addEventListener('click', () => {
        sidebar.classList.remove('open');
        sidebarOverlay.style.display = 'none';
    });

    // Sidebar Collapse (Desktop)
    sidebarCollapseBtn.addEventListener('click', () => {
        sidebar.classList.toggle('collapsed');
        document.body.classList.toggle('sidebar-collapsed'); // Toggle class on body for CSS
        // Icon rotation is handled by CSS
    });

    // Search Mode Toggle
    modeQuickBtn.addEventListener('click', () => setSearchMode('quick'));
    modeDeepBtn.addEventListener('click', () => setSearchMode('deep'));

    // Citation Click Handler - scroll to source and highlight
    document.addEventListener('click', (e) => {
        // Handle both .citation and .citation-marker classes
        const citation = e.target.closest('.citation, .citation-marker');
        if (citation) {
            e.preventDefault();

            // Get the citation index from href or data attribute
            let sourceNumber;
            if (citation.dataset.citationIndex !== undefined) {
                sourceNumber = parseInt(citation.dataset.citationIndex) + 1; // Convert 0-indexed to 1-indexed
            } else if (citation.href && citation.href.includes('#source-')) {
                sourceNumber = parseInt(citation.href.split('#source-')[1]);
            } else {
                // Try to extract number from text content like "[1]"
                const match = citation.textContent.match(/\[(\d+)\]/);
                if (match) {
                    sourceNumber = parseInt(match[1]);
                }
            }

            if (sourceNumber && sourceNumber >= 1) {
                // Find the source card by ID
                const sourceCard = document.getElementById(`source-${sourceNumber}`);

                if (sourceCard) {
                    // Remove previous highlights
                    document.querySelectorAll('.source-card.highlighted').forEach(el => {
                        el.classList.remove('highlighted');
                    });

                    // Add highlight to target
                    sourceCard.classList.add('highlighted');

                    // Scroll into view smoothly
                    sourceCard.scrollIntoView({ behavior: 'smooth', block: 'center' });

                    // Remove highlight after 3 seconds
                    setTimeout(() => {
                        sourceCard.classList.remove('highlighted');
                    }, 3000);
                } else {
                    console.warn(`Source card #source-${sourceNumber} not found`);
                }
            }
        }
    });

    // Settings Modal
    // Settings Modal
    settingsBtn.addEventListener('click', () => {
        settingsModal.classList.remove('hidden');
        // Load current values
        themeSelect.value = localStorage.getItem('theme') || 'dark';
        apiKeyInput.value = localStorage.getItem('gemini_api_key') || '';
        sessionTimeoutInput.value = localStorage.getItem('session_timeout') || '60';
    });

    closeSettingsBtn.addEventListener('click', () => {
        settingsModal.classList.add('hidden');
    });

    saveSettingsBtn.addEventListener('click', () => {
        const theme = themeSelect.value;
        const apiKey = apiKeyInput.value.trim();
        const sessionTimeout = sessionTimeoutInput.value;

        // Save Theme
        localStorage.setItem('theme', theme);
        applyTheme(theme);

        // Save API Key
        if (apiKey) {
            localStorage.setItem('gemini_api_key', apiKey);
        } else {
            localStorage.removeItem('gemini_api_key');
        }

        // Save Session Timeout
        localStorage.setItem('session_timeout', sessionTimeout);

        settingsModal.classList.add('hidden');
    });

    // Close modal on outside click
    settingsModal.addEventListener('click', (e) => {
        if (e.target === settingsModal) {
            settingsModal.classList.add('hidden');
        }
    });

    // --- Functions ---

    function loadSettings() {
        const theme = localStorage.getItem('theme') || 'dark';
        applyTheme(theme);
    }

    function applyTheme(theme) {
        if (theme === 'light') {
            document.body.classList.add('light-theme');
        } else {
            document.body.classList.remove('light-theme');
        }
        // Force input background update
        queryInput.style.backgroundColor = ''; // Reset inline style to let CSS take over
    }

    function setSearchMode(mode) {
        searchMode = mode;
        if (mode === 'quick') {
            modeQuickBtn.classList.add('active');
            modeDeepBtn.classList.remove('active');
        } else {
            modeDeepBtn.classList.add('active');
            modeQuickBtn.classList.remove('active');
        }
    }

    function startSearch(query) {
        isGenerating = true;

        // UI Transition
        searchContainer.classList.remove('centered');
        searchContainer.classList.add('bottom');
        resultsArea.style.display = 'block';

        // Prepare Target Container
        let targetAnswerContainer;

        if (activeChatId) {
            // Append Mode
            const messageBlock = document.createElement('div');
            messageBlock.className = 'message-block';

            let exportButtonsHtml = '';
            if (searchMode === 'deep') {
                exportButtonsHtml = `
                    <button class="action-chip" onclick="exportChat('${activeChatId}', 'pdf')"><i class="fa-solid fa-file-pdf"></i> PDF</button>
                    <button class="action-chip" onclick="exportChat('${activeChatId}', 'docx')"><i class="fa-solid fa-file-word"></i> Docx</button>
                `;
            }

            messageBlock.innerHTML = `
                <div class="user-query-header">${query}</div>
                <div class="ai-answer-container"><p>Thinking...</p></div>
                <div class="answer-actions hidden">
                    <button class="action-chip" onclick="copyToClipboard(this)"><i class="fa-regular fa-copy"></i> Copy</button>
                    <button class="action-chip" onclick="shareChat(this)"><i class="fa-solid fa-share"></i> Share</button>
                    ${exportButtonsHtml}
                </div>
            `;
            answerContent.appendChild(messageBlock);
            targetAnswerContainer = messageBlock.querySelector('.ai-answer-container');

            // Scroll to bottom
            window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
        } else {
            // New Chat Mode
            displayQuery.textContent = query;

            sourcesList.innerHTML = '';
            if (sourcesSection) sourcesSection.classList.add('hidden');

            // Clear previous content if starting fresh
            answerContent.innerHTML = '';

            // Create first block
            const messageBlock = document.createElement('div');
            messageBlock.className = 'message-block';

            // Note: For the first block, we might not have activeChatId yet until the stream starts/ends?
            // Actually, activeChatId is set in fetchStream meta event.
            // But we create the block BEFORE fetchStream.
            // So we can't put the ID in the onclick yet.
            // We'll need to update the buttons later or use a placeholder ID that gets updated?
            // Or just re-render the actions when done?
            // Let's use a placeholder and update it, or better:
            // Since `exportChat` uses `activeChatId` from global scope if not passed,
            // we can just pass nothing or handle it in the function.
            // Let's make `exportChat` use the global `activeChatId` if the arg is missing/null.

            let exportButtonsHtml = '';
            if (searchMode === 'deep') {
                exportButtonsHtml = `
                    <button class="action-chip" onclick="exportChat(null, 'pdf')"><i class="fa-solid fa-file-pdf"></i> PDF</button>
                    <button class="action-chip" onclick="exportChat(null, 'docx')"><i class="fa-solid fa-file-word"></i> Docx</button>
                `;
            }

            messageBlock.innerHTML = `
                <div class="ai-answer-container"><p>Thinking...</p></div>
                    <div class="answer-actions hidden">
                        <button class="action-chip" onclick="copyToClipboard(this)"><i class="fa-regular fa-copy"></i> Copy</button>
                        <button class="action-chip" onclick="shareChat(this)"><i class="fa-solid fa-share"></i> Share</button>
                        ${exportButtonsHtml}
                    </div>
            `;
            answerContent.appendChild(messageBlock);
            targetAnswerContainer = messageBlock.querySelector('.ai-answer-container');
        }

        // Show/Hide Export based on mode - REMOVED (Moved to message block)
        // if (exportContainer) {
        //     if (searchMode === 'deep') {
        //         exportContainer.style.display = 'block';
        //     } else {
        //         exportContainer.style.display = 'none';
        //     }
        // }

        if (answerSection) answerSection.classList.remove('hidden');

        // Clear Input
        queryInput.value = '';
        queryInput.style.height = 'auto';

        // API Call
        fetchStream(query, targetAnswerContainer);
    }

    async function fetchStream(query, targetContainer) {
        try {
            const apiKey = localStorage.getItem('gemini_api_key');
            const response = await fetch('/chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-API-Key': apiKey || '' // Send API Key if available
                },
                body: JSON.stringify({
                    query: query,
                    mode: searchMode,
                    user_id: getUserId(), // Associate chat with this browser's user
                    chat_id: activeChatId, // Send current chat ID for context
                    session_timeout: parseInt(localStorage.getItem('session_timeout') || '60') * 60 // Send timeout in seconds
                })
            });

            if (response.status === 403) {
                const data = await response.json();
                if (data.limit_reached) {
                    document.getElementById('limit-modal').classList.remove('hidden');
                    if (targetContainer) targetContainer.innerHTML = '<p class="error">Limit reached.</p>';
                    return;
                }
            }

            if (response.status === 429) {
                const msg = `
                    <div class="error-message">
                        <p><strong>Rate Limit Reached</strong></p>
                        <p>You have hit the global rate limit. To continue without waiting, please providing your own Gemini API Key.</p>
                        <button class="primary-btn small" onclick="document.getElementById('settings-btn').click()">
                            <i class="fa-solid fa-key"></i> Enter API Key in Settings
                        </button>
                    </div>
                `;
                if (targetContainer) targetContainer.innerHTML = msg;
                return;
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let accumulatedText = "";

            // Clear initial loading state if target provided (already done in startSearch but good for safety)
            if (targetContainer) targetContainer.innerHTML = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                // Decode chunk and append to buffer
                const chunk = decoder.decode(value, { stream: true });
                accumulatedText += chunk;

                // Split by newline
                const lines = accumulatedText.split('\n');

                // Pop the last element, which is either an incomplete line or empty string (if chunk ended with \n)
                // We keep this in accumulatedText for the next iteration
                accumulatedText = lines.pop();

                for (const line of lines) {
                    if (line.trim().startsWith('data: ')) {
                        try {
                            const data = JSON.parse(line.trim().slice(6));

                            if (data.type === 'meta') {
                                activeChatId = data.data.chat_id; // Update active chat ID
                                // Update URL with chat ID
                                window.history.pushState({ chatId: activeChatId }, '', `?chat=${activeChatId}`);
                                saveToHistoryStorage(query, activeChatId, searchMode);
                            } else if (data.type === 'sources') {
                                // Render sources but keep hidden until content starts
                                // Only render sources if it's the main answer or update global sources?
                                // For now, update global sources list in sidebar
                                renderSources(data.data);
                                window.currentSources = data.data; // Store for tooltips
                                if (sourcesSection) sourcesSection.classList.add('hidden');
                            } else if (data.type === 'progress') {
                                // Update status message - escape HTML to prevent XSS
                                const safeProgress = escapeHtml(data.data);
                                if (targetContainer) targetContainer.innerHTML = `<p class="status-message"><i class="fa-solid fa-circle-notch fa-spin"></i> ${safeProgress}</p>`;
                            } else if (data.type === 'reasoning') {
                                // Render Reasoning Box
                                // Check if thinking box already exists in this block
                                const block = targetContainer.closest('.message-block');
                                let thinkingBox = block.querySelector('.thinking-box');

                                if (!thinkingBox) {
                                    thinkingBox = document.createElement('div');
                                    thinkingBox.className = 'thinking-box';
                                    thinkingBox.innerHTML = `
                                        <div class="thinking-header" onclick="this.parentElement.classList.toggle('open')">
                                            <i class="fa-solid fa-brain"></i>
                                            <span>Thinking Process</span>
                                            <i class="fa-solid fa-chevron-right"></i>
                                        </div>
                                        <div class="thinking-content">${escapeHtml(data.data)}</div>
                                    `;
                                    // Insert before the answer container (targetContainer is inside .message-block, wait, targetContainer IS .ai-answer-container)
                                    // We want it INSIDE .ai-answer-container, at the top? Or before it?
                                    // Let's put it at the top of targetContainer (which is currently clearing/streaming)
                                    // BUT, targetContainer is getting overwritten by `targetContainer.innerHTML = data.data` in content event.

                                    // Better approach: Insert it BEFORE targetContainer in the DOM?
                                    // Or make targetContainer only hold the ANSWER, and put thinking box separate.
                                    // Currently: message-block > user-query-header, ai-answer-container, answer-actions
                                    // Let's insert thinking box BEFORE ai-answer-container.

                                    block.insertBefore(thinkingBox, targetContainer);
                                } else {
                                    // Update content if streaming chunks of reasoning (currently backend sends all at once)
                                    thinkingBox.querySelector('.thinking-content').textContent = data.data;
                                }

                            } else if (data.type === 'content') {
                                // Show sources now that content is starting
                                if (sourcesSection) sourcesSection.classList.remove('hidden');
                                // Backend sends pre-rendered HTML - trusted source
                                if (targetContainer) targetContainer.innerHTML = data.data;
                            } else if (data.type === 'related') {
                                renderRelatedQuestions(data.data);
                            } else if (data.type === 'error') {
                                // Escape error messages to prevent XSS
                                const safeError = escapeHtml(data.data);
                                if (targetContainer) targetContainer.innerHTML += `<p class="error">Error: ${safeError}</p>`;
                            }
                        } catch (e) {
                            console.error("Error parsing SSE:", e, line);
                            // Do not crash on parse error, just log and continue
                        }
                    }
                }
            }
        } catch (error) {
            const safeError = escapeHtml(error.message);
            if (targetContainer) targetContainer.innerHTML = `<p class="error">Connection failed: ${safeError}</p>`;
        } finally {
            isGenerating = false;
            // Show actions for the specific message block
            if (targetContainer) {
                const block = targetContainer.closest('.message-block');
                if (block) {
                    const actions = block.querySelector('.answer-actions');
                    if (actions) actions.classList.remove('hidden');
                }
            }
        }
    }


    async function loadChat(chatId) {
        if (isGenerating) return;
        // Reset UI first
        resetUI();
        isGenerating = false;
        activeChatId = chatId; // Set active chat ID

        // Update URL with chat ID
        window.history.pushState({ chatId: chatId }, '', `?chat=${chatId}`);

        // UI Transition
        searchContainer.classList.remove('centered');
        searchContainer.classList.add('bottom');
        resultsArea.style.display = 'block';
        sourcesList.innerHTML = '';
        answerContent.innerHTML = '<p>Loading history...</p>';

        try {
            const response = await fetch(`/history/${chatId}?user_id=${getUserId()}`);
            if (!response.ok) {
                if (response.status === 404) {
                    alert("This chat session has expired or was not found.");
                    // Remove from local storage
                    let history = JSON.parse(localStorage.getItem('arbor_history') || '[]');
                    history = history.filter(h => h.chatId !== chatId);
                    localStorage.setItem('arbor_history', JSON.stringify(history));
                    loadHistoryFromStorage(); // Refresh list
                    return;
                }
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const data = await response.json();
            const messages = data.messages;
            const chatMode = data.mode || 'quick'; // Default to quick if not set

            console.log('Loaded chat data:', { chatId, mode: chatMode, messageCount: messages?.length });

            // Update searchMode to match the loaded chat
            if (chatMode === 'deep') {
                setSearchMode('deep');
            } else {
                setSearchMode('quick');
            }

            // Show/Hide Export based on loaded chat mode - REMOVED
            // if (exportContainer) {
            //     if (chatMode === 'deep') {
            //         exportContainer.style.display = 'block';
            //     } else {
            //         exportContainer.style.display = 'none';
            //     }
            // }

            if (!messages || messages.length === 0) {
                answerContent.innerHTML = '<p>No history found for this chat.</p>';
                if (answerSection) answerSection.classList.remove('hidden');
                return;
            }

            // Clear loading message
            answerContent.innerHTML = '';

            // Render all messages
            messages.forEach(msg => {
                const messageBlock = document.createElement('div');
                messageBlock.className = 'message-block';

                let answerHtml = '';
                if (msg.answer_html) {
                    answerHtml = msg.answer_html;
                } else if (msg.answer_raw) {
                    answerHtml = marked.parse(msg.answer_raw, msg.sources || []);
                } else {
                    answerHtml = '<p>No answer content found.</p>';
                }

                let exportButtonsHtml = '';
                if (chatMode === 'deep') {
                    exportButtonsHtml = `
                        <button class="action-chip" onclick="exportChat('${chatId}', 'pdf')"><i class="fa-solid fa-file-pdf"></i> PDF</button>
                        <button class="action-chip" onclick="exportChat('${chatId}', 'docx')"><i class="fa-solid fa-file-word"></i> Docx</button>
                    `;
                }

                messageBlock.innerHTML = `
                    <div class="user-query-header">${msg.query || 'Untitled'}</div>
                    ${msg.reasoning ? `
                    <div class="thinking-box">
                        <div class="thinking-header" onclick="this.parentElement.classList.toggle('open')">
                            <i class="fa-solid fa-brain"></i>
                            <span>Thinking Process</span>
                            <i class="fa-solid fa-chevron-right"></i>
                        </div>
                        <div class="thinking-content">${escapeHtml(msg.reasoning)}</div>
                    </div>` : ''}
                    <div class="ai-answer-container">${answerHtml}</div>
                    <div class="answer-actions">
                        <button class="action-chip" onclick="copyToClipboard(this)"><i class="fa-regular fa-copy"></i> Copy</button>
                        <button class="action-chip" onclick="shareChat(this)"><i class="fa-solid fa-share"></i> Share</button>
                        ${exportButtonsHtml}
                    </div>
                `;
                answerContent.appendChild(messageBlock);
            });

            // Set display query to the first query (or last? usually first is the title)
            displayQuery.textContent = messages[0].query || "Chat History";

            const lastMessage = messages[messages.length - 1];

            if (lastMessage.sources) {
                renderSources(lastMessage.sources);
                if (sourcesSection) sourcesSection.classList.remove('hidden'); // Show sources for history
            }

            if (answerSection) answerSection.classList.remove('hidden'); // Show answer for history
            if (answerSection) answerSection.classList.remove('hidden'); // Show answer for history


            if (lastMessage.follow_up_questions) {
                renderRelatedQuestions(lastMessage.follow_up_questions);
            }

            // Scroll to bottom
            window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });

        } catch (error) {
            console.error("Error loading chat:", error);
            answerContent.innerHTML = `<p class="error">Failed to load history: ${error.message}</p>`;
            if (answerSection) answerSection.classList.remove('hidden'); // Show error
        }
    }

    function renderSources(sources) {
        if (sourcesSection) sourcesSection.classList.remove('hidden'); // Show sources
        sourcesList.innerHTML = sources.map((source, index) => {
            let domain = '';
            try {
                if (source.url) {
                    domain = new URL(source.url).hostname.replace('www.', '');
                } else {
                    domain = 'Unknown';
                }
            } catch (e) {
                console.error("Invalid URL:", source.url);
                domain = 'Unknown';
            }

            return `
                <li class="source-card" id="source-${index + 1}">
                    <a href="${source.url}" target="_blank">
                        <div class="source-title">${source.title || 'Untitled'}</div>
                        <div class="source-url">${domain}</div>
                        <div class="source-index">${index + 1}</div>
                    </a>
                </li>
            `;
        }).join('');
    }

    function renderRelatedQuestions(questions) {
        if (!questions || questions.length === 0) return;

        // Create container if it doesn't exist
        let relatedContainer = document.querySelector('.related-questions');
        if (!relatedContainer) {
            relatedContainer = document.createElement('div');
            relatedContainer.className = 'related-questions';
            relatedContainer.style.marginTop = '1.5rem';
            relatedContainer.style.borderTop = '1px solid var(--border)';
            relatedContainer.style.paddingTop = '1rem';
            relatedContainer.innerHTML = '<h3 style="font-size: 1rem; margin-bottom: 0.5rem;">Related</h3>';
            answerContent.appendChild(relatedContainer);
        }

        const list = document.createElement('ul');
        list.style.listStyle = 'none';
        list.style.padding = '0';

        questions.forEach(q => {
            const li = document.createElement('li');
            li.style.padding = '0.5rem 0';
            li.style.cursor = 'pointer';
            li.style.color = 'var(--accent)';
            li.textContent = q;
            li.onclick = () => startSearch(q);
            li.onmouseover = () => li.style.textDecoration = 'underline';
            li.onmouseout = () => li.style.textDecoration = 'none';
            list.appendChild(li);
        });

        relatedContainer.appendChild(list);
    }

    function saveToHistoryStorage(query, chatId, mode) {
        let history = JSON.parse(localStorage.getItem('arbor_history') || '[]');
        // Remove if exists (to move to top)
        history = history.filter(h => h.chatId !== chatId);
        history.unshift({ query, chatId, mode: mode || 'quick', timestamp: Date.now() });
        // Limit to 20 items
        if (history.length > 20) history.pop();
        localStorage.setItem('arbor_history', JSON.stringify(history));
        loadHistoryFromStorage(); // Refresh UI
    }

    function loadHistoryFromStorage() {
        historyList.innerHTML = '';
        const history = JSON.parse(localStorage.getItem('arbor_history') || '[]');
        history.forEach(item => {
            addToHistoryUI(item.query, item.chatId, item.mode);
        });
    }

    function addToHistoryUI(query, chatId, mode) {
        const li = document.createElement('li');
        li.className = 'history-item';

        const textSpan = document.createElement('span');
        textSpan.className = 'history-text';

        // Show mode badge for deep chats
        const modeBadge = mode === 'deep' ? '<span class="history-mode-badge">🔬</span> ' : '';
        textSpan.innerHTML = `<i class="fa-regular fa-message"></i> ${modeBadge}${query}`;
        textSpan.onclick = () => {
            if (chatId) {
                loadChat(chatId);
            } else {
                queryInput.value = query;
                startSearch(query);
            }
            // On mobile, close sidebar
            sidebar.classList.remove('open');
            sidebarOverlay.style.display = 'none';
        };

        const deleteBtn = document.createElement('button');
        deleteBtn.className = 'history-delete-btn';
        deleteBtn.innerHTML = '<i class="fa-solid fa-trash"></i>';
        deleteBtn.onclick = (e) => {
            e.stopPropagation();
            deleteChat(chatId, li);
        };

        li.appendChild(textSpan);
        li.appendChild(deleteBtn);
        historyList.appendChild(li);
    }

    async function deleteChat(chatId, listItem) {
        if (!confirm('Delete this chat?')) return;

        try {
            // Delete from server
            const response = await fetch(`/chat/${chatId}?user_id=${getUserId()}`, { method: 'DELETE' });
            if (response.ok || response.status === 404) {
                // Remove from local storage
                let history = JSON.parse(localStorage.getItem('arbor_history') || '[]');
                history = history.filter(h => h.chatId !== chatId);
                localStorage.setItem('arbor_history', JSON.stringify(history));

                // Remove from UI
                listItem.remove();

                // If currently viewing this chat, reset UI
                if (activeChatId === chatId) {
                    resetUI();
                }
            }
        } catch (error) {
            console.error('Failed to delete chat:', error);
        }
    }

    // Deprecated: Old addToHistory, replaced by addToHistoryUI and saveToHistoryStorage
    function addToHistory(query) {
        // This function is kept for compatibility if needed, but we should use saveToHistoryStorage
    }

    function resetUI() {
        searchContainer.classList.remove('bottom');
        searchContainer.classList.add('centered');
        resultsArea.style.display = 'none';
        if (sourcesSection) sourcesSection.classList.add('hidden');
        if (answerSection) answerSection.classList.add('hidden');
        if (answerSection) answerSection.classList.add('hidden');

        queryInput.value = '';
        queryInput.style.height = 'auto';
        isGenerating = false;
        window.currentSources = []; // Clear sources
        activeChatId = null; // Clear active chat

        // Clear URL
        window.history.pushState({}, '', window.location.pathname);

        // Hide export buttons if they exist - REMOVED
        // const exportContainer = document.getElementById('export-container');
        // if (exportContainer) exportContainer.style.display = 'none';
    }
});

// Export Chat Function
function exportChat(chatId, format) {
    const id = chatId || activeChatId;
    if (id) {
        window.open(`/export/${id}/${format}?user_id=${getUserId()}`, '_blank');
    } else {
        alert("Chat ID not found.");
    }
}

// Copy to Clipboard Function - Visual Feedback Improved
function copyToClipboard(btn) {
    const block = btn.closest('.message-block');
    if (!block) return;
    const text = block.querySelector('.ai-answer-container').innerText;

    // Copy markdown/text
    navigator.clipboard.writeText(text).then(() => {
        const originalHtml = btn.innerHTML;
        btn.innerHTML = '<i class="fa-solid fa-check"></i> Copied';
        btn.classList.add('copied');

        setTimeout(() => {
            btn.innerHTML = originalHtml;
            btn.classList.remove('copied');
        }, 2000);
    }).catch(err => {
        console.error('Failed to copy:', err);
        btn.innerHTML = '<i class="fa-solid fa-xmark"></i> Error';
    });
}

// Share Function - Social Modal
function shareChat(btn) {
    const chatUrl = activeChatId
        ? `${window.location.origin}${window.location.pathname}?chat=${activeChatId}`
        : window.location.href;

    // Check if modal exists, else create it
    let modal = document.getElementById('share-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'share-modal';
        modal.className = 'modal hidden';
        modal.innerHTML = `
            <div class="modal-overlay"></div>
            <div class="modal-content">
                <div class="modal-header">
                    <h2>Share Chat</h2>
                    <button class="close-modal-btn"><i class="fa-solid fa-xmark"></i></button>
                </div>
                <div class="modal-body">
                    <div class="share-grid">
                        <a href="#" class="share-option" data-platform="whatsapp" style="color: #25D366; border-color: rgba(37, 211, 102, 0.2);">
                            <i class="fa-brands fa-whatsapp"></i> WhatsApp
                        </a>
                        <a href="#" class="share-option" data-platform="twitter" style="color: #1DA1F2; border-color: rgba(29, 161, 242, 0.2);">
                            <i class="fa-brands fa-x-twitter"></i> X / Twitter
                        </a>
                        <a href="#" class="share-option" data-platform="linkedin" style="color: #0A66C2; border-color: rgba(10, 102, 194, 0.2);">
                            <i class="fa-brands fa-linkedin"></i> LinkedIn
                        </a>
                        <a href="#" class="share-option" data-platform="facebook" style="color: #1877F2; border-color: rgba(24, 119, 242, 0.2);">
                            <i class="fa-brands fa-facebook"></i> Facebook
                        </a>
                    </div>

                    <div class="share-link-wrapper">
                        <input type="text" class="share-link-input" readonly value="${chatUrl}">
                        <button class="primary-btn" id="modal-copy-link-btn">
                            <i class="fa-regular fa-copy"></i>
                        </button>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(modal);

        // Event Listeners for the new modal
        const closeBtn = modal.querySelector('.close-modal-btn');
        const overlay = modal.querySelector('.modal-overlay');
        const copyBtn = modal.querySelector('#modal-copy-link-btn');
        const input = modal.querySelector('.share-link-input');
        const shareOptions = modal.querySelectorAll('.share-option');

        const closeModal = () => modal.classList.add('hidden');
        closeBtn.onclick = closeModal;
        overlay.onclick = closeModal;

        // Copy Button Logic
        copyBtn.onclick = () => {
            input.select();
            navigator.clipboard.writeText(input.value).then(() => {
                const icon = copyBtn.querySelector('i');
                icon.className = 'fa-solid fa-check';
                copyBtn.style.backgroundColor = 'var(--accent-hover)';
                setTimeout(() => {
                    icon.className = 'fa-regular fa-copy';
                    copyBtn.style.backgroundColor = '';
                }, 2000);
            });
        };

        // Social Share Logic
        shareOptions.forEach(opt => {
            opt.onclick = (e) => {
                e.preventDefault();
                const platform = opt.dataset.platform;
                const text = "Check out this research I did with Arbor AI:";
                let url = "";

                switch (platform) {
                    case 'whatsapp':
                        url = `https://wa.me/?text=${encodeURIComponent(text + ' ' + input.value)}`;
                        break;
                    case 'twitter':
                        url = `https://twitter.com/intent/tweet?text=${encodeURIComponent(text)}&url=${encodeURIComponent(input.value)}`;
                        break;
                    case 'linkedin':
                        url = `https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(input.value)}`;
                        break;
                    case 'facebook':
                        url = `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(input.value)}`;
                        break;
                }
                window.open(url, '_blank', 'width=600,height=400');
            };
        });
    }

    // Update URL in modal (in case it changed)
    const input = modal.querySelector('.share-link-input');
    if (input) input.value = chatUrl;

    // Show Modal
    modal.classList.remove('hidden');
}

// Simple Markdown Parser (Placeholder - in prod use marked.js)
const marked = {
    parse: function (text, sources = []) {
        // Basic bold/italic/link parsing
        let html = text
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.*?)\*/g, '<em>$1</em>')
            .replace(/\[(.*?)\]\((.*?)\)/g, '<a href="$2" target="_blank">$1</a>')
            .replace(/\[(\d+)\]/g, (match, p1) => {
                const index = parseInt(p1) - 1;
                const source = sources[index];
                const title = source ? (source.title || 'Source ' + p1).replace(/"/g, '&quot;') : 'Source ' + p1;
                return `<a href="#source-${p1}" class="citation" title="${title}">[${p1}]</a>`;
            })
            .replace(/\n/g, '<br>');
        return html;
    }
};