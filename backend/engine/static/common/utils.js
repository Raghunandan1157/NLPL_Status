/**
 * Shared Utilities - Unified Collection Report
 * =============================================
 * Common functions used across all modules (EOD, Hourly, OnDate, Instant, DB).
 * Include this script before module-specific app.js files.
 */

// ── Ngrok Fetch Fix ────────────────────────────────────────────────
// Override global fetch to include ngrok-skip-browser-warning header.
// Without this, ngrok free tier returns an HTML interstitial page
// instead of the actual API response, causing all fetch() calls to fail.
(function() {
    const _originalFetch = window.fetch;
    window.fetch = function(url, options) {
        options = options || {};
        options.headers = options.headers || {};
        // Support both Headers object and plain object
        if (options.headers instanceof Headers) {
            if (!options.headers.has('ngrok-skip-browser-warning')) {
                options.headers.set('ngrok-skip-browser-warning', 'true');
            }
        } else {
            if (!options.headers['ngrok-skip-browser-warning']) {
                options.headers['ngrok-skip-browser-warning'] = 'true';
            }
        }
        return _originalFetch.call(this, url, options);
    };
})();

// ── Toast Notifications ─────────────────────────────────────────────

/**
 * Show a toast notification.
 *
 * Supports two HTML patterns:
 *   1. Fixed toast element:  <div id="toast"><span class="toast-message"></span></div>
 *   2. Toast container:      <div id="toastContainer"></div>  (creates dynamic toasts)
 *
 * @param {string} message - The message to display
 * @param {string} [type='success'] - Toast type: 'success', 'error', 'info', 'warning'
 */
function showToast(message, type = 'success') {
    // Pattern 1: Fixed toast element with .toast-message span
    const fixedToast = document.getElementById('toast');
    if (fixedToast && fixedToast.querySelector('.toast-message')) {
        const toastMessage = fixedToast.querySelector('.toast-message');
        fixedToast.className = 'toast ' + type;
        toastMessage.textContent = message;
        fixedToast.classList.add('show');

        var durations = { success: 3000, info: 4000, warning: 5000, error: 6000 };
        setTimeout(function () {
            fixedToast.classList.remove('show');
        }, durations[type] || 3000);
        return;
    }

    // Pattern 2: Dynamic toast container
    var container = document.getElementById('toastContainer') || document.getElementById('toast-container');
    if (!container) {
        // Auto-create container if none exists
        container = document.createElement('div');
        container.id = 'toastContainer';
        container.style.cssText = 'position:fixed;top:20px;right:20px;z-index:10000;display:flex;flex-direction:column;gap:8px;';
        document.body.appendChild(container);
    }

    var toast = document.createElement('div');
    toast.className = 'toast ' + type;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(function () {
        toast.style.opacity = '0';
        setTimeout(function () { toast.remove(); }, 300);
    }, type === 'error' ? 6000 : type === 'warning' ? 5000 : type === 'info' ? 4000 : 3000);
}


// ── Drop Zone Initialization ────────────────────────────────────────

/**
 * Initialize drag-and-drop zones for file uploads.
 *
 * Each zone is identified by a type string. Expected HTML structure:
 *   <div id="{type}Zone" class="drop-zone">...</div>
 *   <input id="{type}Input" type="file" hidden>
 *
 * @param {string[]} types - Array of zone type identifiers (e.g., ['par', 'collection', 'demand'])
 * @param {function} onFile - Callback: onFile(type, file) called when a file is selected or dropped
 * @param {object} [options] - Optional configuration
 * @param {string} [options.accept='.xlsx,.xls'] - Accepted file extensions for validation
 */
function initDropZones(types, onFile, options) {
    var accept = (options && options.accept) || '.xlsx,.xls';

    types.forEach(function (type) {
        var zone = document.getElementById(type + 'Zone');
        var input = document.getElementById(type + 'Input');

        if (!zone || !input) return;

        // Click to open file browser
        zone.addEventListener('click', function (e) {
            // Don't trigger if clicking a remove button inside the zone
            if (e.target.closest('.file-remove, .remove-btn, [data-remove]')) return;
            input.click();
        });

        // File input change
        input.addEventListener('change', function (e) {
            if (e.target.files.length > 0) {
                var file = e.target.files[0];
                if (_validateFileExtension(file, accept)) {
                    onFile(type, file);
                }
            }
        });

        // Drag events
        zone.addEventListener('dragover', function (e) {
            e.preventDefault();
            e.stopPropagation();
            zone.classList.add('dragover');
        });

        zone.addEventListener('dragleave', function (e) {
            e.preventDefault();
            e.stopPropagation();
            zone.classList.remove('dragover');
        });

        zone.addEventListener('drop', function (e) {
            e.preventDefault();
            e.stopPropagation();
            zone.classList.remove('dragover');

            if (e.dataTransfer.files.length > 0) {
                var file = e.dataTransfer.files[0];
                if (_validateFileExtension(file, accept)) {
                    onFile(type, file);
                } else {
                    showToast('Please upload an Excel file (.xlsx or .xls)', 'error');
                }
            }
        });
    });
}

/**
 * Validate file extension against accepted types.
 * @private
 */
function _validateFileExtension(file, accept) {
    if (!accept) return true;
    var ext = '.' + file.name.split('.').pop().toLowerCase();
    var accepted = accept.split(',').map(function (s) { return s.trim().toLowerCase(); });
    return accepted.indexOf(ext) !== -1;
}


// ── Format Bytes ────────────────────────────────────────────────────

/**
 * Format a byte count into a human-readable string.
 *
 * @param {number} bytes - Number of bytes
 * @param {number} [decimals=1] - Number of decimal places
 * @returns {string} Formatted string (e.g., "1.5 MB")
 */
function formatBytes(bytes, decimals) {
    if (decimals === undefined) decimals = 1;
    if (bytes === 0) return '0 B';
    var k = 1024;
    var sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    var i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(decimals)) + ' ' + sizes[i];
}


// ── Copy to Clipboard ───────────────────────────────────────────────

/**
 * Copy text to the clipboard with visual button feedback.
 *
 * @param {string} text - The text to copy
 * @param {HTMLElement} [btnElement] - Optional button element for visual feedback
 * @param {string} [successMessage='Copied!'] - Toast message on success
 * @returns {Promise<boolean>} Resolves to true if copy succeeded
 */
async function copyToClipboard(text, btnElement, successMessage) {
    if (!successMessage) successMessage = 'Copied!';

    try {
        await navigator.clipboard.writeText(text);

        // Visual feedback on the button
        if (btnElement) {
            var originalHTML = btnElement.innerHTML;
            btnElement.classList.add('copied');
            btnElement.innerHTML =
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">' +
                '<polyline points="20 6 9 17 4 12"></polyline>' +
                '</svg>' +
                '<span>Copied!</span>';

            setTimeout(function () {
                btnElement.classList.remove('copied');
                btnElement.innerHTML = originalHTML;
            }, 2000);
        }

        showToast(successMessage, 'success');
        return true;

    } catch (err) {
        console.error('Copy failed:', err);

        // Fallback: textarea method for older browsers
        try {
            var textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.style.cssText = 'position:fixed;left:-9999px;top:-9999px;';
            document.body.appendChild(textarea);
            textarea.select();
            document.execCommand('copy');
            document.body.removeChild(textarea);

            showToast(successMessage, 'success');
            return true;
        } catch (fallbackErr) {
            showToast('Failed to copy to clipboard', 'error');
            return false;
        }
    }
}


// ── File Validation ─────────────────────────────────────────────────

/**
 * Check if a file is a valid Excel file by extension.
 *
 * @param {File} file - The File object to validate
 * @returns {boolean} True if the file has a .xlsx or .xls extension
 */
function isValidExcelFile(file) {
    if (!file || !file.name) return false;
    return /\.(xlsx|xls)$/i.test(file.name);
}


// ── Date Formatting ─────────────────────────────────────────────────

/**
 * Get current time as HH:MM:SS string.
 *
 * @returns {string} Time string (e.g., "14:30:45")
 */
function getTimeString() {
    return new Date().toTimeString().split(' ')[0];
}


// ── Prevent Global Drag ─────────────────────────────────────────────

/**
 * Prevent the browser from opening files dropped outside drop zones.
 * Call once on page load.
 */
function preventGlobalDragDrop() {
    document.addEventListener('dragover', function (e) { e.preventDefault(); });
    document.addEventListener('drop', function (e) { e.preventDefault(); });
}


// ── Server URL Banner (for ngrok sharing) ───────────────────────────

/**
 * Auto-inject a compact URL banner at the top of every page.
 * Shows the current server URL so it can be copied and shared via ngrok.
 */
(function injectServerUrlBanner() {
    // Skip banner if already viewing via ngrok (phone) — no need to show the URL
    if (window.location.hostname.includes('ngrok')) return;

    function createBanner() {
        var style = document.createElement('style');
        style.textContent = [
            '#ngrokBanner{position:relative;z-index:99999;display:flex;align-items:center;justify-content:center;gap:10px;padding:7px 16px;',
            'background:rgba(10,10,20,0.95);border-bottom:1px solid rgba(255,255,255,0.08);',
            'font-family:"JetBrains Mono","DM Sans",monospace;font-size:12px;color:rgba(255,255,255,0.7);}',
            '#ngrokBanner .nb-label{color:rgba(255,255,255,0.4);font-size:10px;text-transform:uppercase;letter-spacing:1px;}',
            '#ngrokBanner .nb-url{color:#7ec8e3;font-weight:500;user-select:all;}',
            '#ngrokBanner .nb-copy{background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.12);',
            'border-radius:4px;color:#7ec8e3;font-family:inherit;font-size:11px;padding:2px 10px;cursor:pointer;transition:all 0.15s ease;}',
            '#ngrokBanner .nb-copy:hover{background:rgba(126,200,227,0.15);}',
        ].join('');
        document.head.appendChild(style);

        var banner = document.createElement('div');
        banner.id = 'ngrokBanner';
        banner.innerHTML = '<span class="nb-label">ngrok</span><span class="nb-url" id="ngrokUrlText">fetching...</span>';

        var copyBtn = document.createElement('button');
        copyBtn.className = 'nb-copy';
        copyBtn.id = 'ngrokCopyBtn';
        copyBtn.textContent = 'Copy';
        copyBtn.style.display = 'none';
        banner.appendChild(copyBtn);

        document.body.insertBefore(banner, document.body.firstChild);

        var urlSpan = document.getElementById('ngrokUrlText');

        fetch('/api/ngrok-url')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.url) {
                    urlSpan.textContent = data.url;
                    copyBtn.style.display = '';
                    copyBtn.onclick = function() {
                        navigator.clipboard.writeText(data.url).then(function() {
                            copyBtn.textContent = 'Copied!';
                            copyBtn.style.color = '#10b981';
                            setTimeout(function() {
                                copyBtn.textContent = 'Copy';
                                copyBtn.style.color = '#7ec8e3';
                            }, 1500);
                        });
                    };
                } else {
                    urlSpan.textContent = 'ngrok not running';
                    urlSpan.style.color = 'rgba(255,255,255,0.3)';
                }
            })
            .catch(function() {
                urlSpan.textContent = 'ngrok not running';
                urlSpan.style.color = 'rgba(255,255,255,0.3)';
            });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', createBanner);
    } else {
        createBanner();
    }
})();


// ── Processing flag (used by pre-flight check in app.js) ────────────
window.__isLocalProcessing = false;
