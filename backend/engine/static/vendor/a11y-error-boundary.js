/**
 * Accessibility helpers and global error boundary.
 * Loaded early on every page to provide baseline a11y and error handling.
 */
(function () {
  'use strict';

  // ── Global Error Boundary ─────────────────────────────────────────
  // Catches unhandled errors and promise rejections, shows a toast if
  // showToast() exists, otherwise falls back to console.

  window.addEventListener('error', function (event) {
    var msg = event.message || 'An unexpected error occurred';
    console.error('[ErrorBoundary]', msg, event.filename, event.lineno);
    _showError(msg);
  });

  window.addEventListener('unhandledrejection', function (event) {
    var reason = event.reason;
    var msg = (reason && reason.message) ? reason.message : String(reason || 'Unhandled promise rejection');
    console.error('[ErrorBoundary] Unhandled rejection:', reason);
    _showError(msg);
  });

  function _showError(msg) {
    // If the page has a showToast function, use it
    if (typeof window.showToast === 'function') {
      window.showToast('Error: ' + msg, 'error');
    }
  }

  // ── Focus Management for Modals ───────────────────────────────────
  // Traps focus inside an element that has [role="dialog"] or .modal-open class.
  // Call window.trapFocus(element) to activate, window.releaseFocus() to release.

  var _focusTrapStack = [];
  var _previousFocus = null;

  window.trapFocus = function (container) {
    if (!container) return;
    _previousFocus = document.activeElement;
    _focusTrapStack.push(container);
    container.setAttribute('aria-modal', 'true');

    // Focus the first focusable element inside
    var firstFocusable = _getFocusable(container)[0];
    if (firstFocusable) {
      setTimeout(function () { firstFocusable.focus(); }, 50);
    }
  };

  window.releaseFocus = function () {
    var container = _focusTrapStack.pop();
    if (container) {
      container.removeAttribute('aria-modal');
    }
    if (_previousFocus && _previousFocus.focus) {
      _previousFocus.focus();
      _previousFocus = null;
    }
  };

  document.addEventListener('keydown', function (e) {
    if (_focusTrapStack.length === 0) return;
    if (e.key !== 'Tab') return;

    var container = _focusTrapStack[_focusTrapStack.length - 1];
    var focusable = _getFocusable(container);
    if (focusable.length === 0) return;

    var first = focusable[0];
    var last = focusable[focusable.length - 1];

    if (e.shiftKey) {
      if (document.activeElement === first) {
        e.preventDefault();
        last.focus();
      }
    } else {
      if (document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  });

  function _getFocusable(container) {
    var sel = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
    return Array.prototype.slice.call(container.querySelectorAll(sel)).filter(function (el) {
      return el.offsetParent !== null; // visible only
    });
  }

  // ── Escape Key Closes Top Modal ───────────────────────────────────

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;

    // Find visible overlay/modal and close it
    var overlays = document.querySelectorAll(
      '.date-modal-overlay, .cache-modal-overlay, .dry-run-overlay, .date-warning-overlay, ' +
      '.upload-overlay, .time-picker-overlay, .demo-picker-overlay, .duplicate-confirm-overlay, ' +
      '.cat-loading-overlay, .spinner-overlay'
    );
    for (var i = overlays.length - 1; i >= 0; i--) {
      var overlay = overlays[i];
      var style = window.getComputedStyle(overlay);
      if (style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0') {
        // Try clicking a cancel/close button inside
        var cancelBtn = overlay.querySelector(
          '[class*="cancel"], [class*="close"], [id*="Cancel"], [id*="close"]'
        );
        if (cancelBtn) {
          cancelBtn.click();
          e.preventDefault();
          return;
        }
        // Fallback: hide the overlay directly
        overlay.style.display = 'none';
        window.releaseFocus();
        e.preventDefault();
        return;
      }
    }
  });

  // ── ARIA Labels for Common Interactive Elements ────────────────────
  // Runs after DOM is ready, adding labels where missing.

  function _applyAriaLabels() {
    // File inputs
    var fileInputs = document.querySelectorAll('input[type="file"]');
    for (var i = 0; i < fileInputs.length; i++) {
      var inp = fileInputs[i];
      if (!inp.getAttribute('aria-label')) {
        var zone = inp.closest('.drop-zone, .upload-zone');
        if (zone) {
          var label = zone.querySelector('.drop-zone-label, .upload-text h3, h3');
          if (label) {
            inp.setAttribute('aria-label', 'Upload ' + label.textContent.trim());
          }
        }
      }
    }

    // Buttons without accessible names
    var buttons = document.querySelectorAll('button');
    for (var j = 0; j < buttons.length; j++) {
      var btn = buttons[j];
      var text = (btn.textContent || '').trim();
      if (!text && !btn.getAttribute('aria-label') && !btn.getAttribute('title')) {
        // SVG-only button - use title attribute if present, otherwise label from context
        var svgTitle = btn.querySelector('title');
        if (svgTitle) {
          btn.setAttribute('aria-label', svgTitle.textContent.trim());
        } else if (btn.getAttribute('title')) {
          btn.setAttribute('aria-label', btn.getAttribute('title'));
        }
      }
    }

    // Drop zones as buttons for keyboard users
    var dropZones = document.querySelectorAll('.drop-zone, .upload-zone');
    for (var k = 0; k < dropZones.length; k++) {
      var dz = dropZones[k];
      if (!dz.getAttribute('role')) {
        dz.setAttribute('role', 'button');
        dz.setAttribute('tabindex', '0');
        var dzLabel = dz.querySelector('.drop-zone-label, .upload-text h3, h3');
        if (dzLabel && !dz.getAttribute('aria-label')) {
          dz.setAttribute('aria-label', 'Upload area: ' + dzLabel.textContent.trim());
        }
      }
    }

    // Add role="dialog" to modal overlays
    var modalOverlays = document.querySelectorAll(
      '.date-modal-overlay, .cache-modal-overlay, .dry-run-overlay, .date-warning-overlay, ' +
      '.upload-overlay, .time-picker-overlay, .demo-picker-overlay, .duplicate-confirm-overlay, ' +
      '.spinner-overlay'
    );
    for (var m = 0; m < modalOverlays.length; m++) {
      var overlay = modalOverlays[m];
      if (!overlay.getAttribute('role')) {
        overlay.setAttribute('role', 'dialog');
      }
      var heading = overlay.querySelector('h3, h2');
      if (heading && !overlay.getAttribute('aria-label')) {
        overlay.setAttribute('aria-label', heading.textContent.trim());
      }
    }

    // Toast containers
    var toastContainers = document.querySelectorAll('.toast-container, .toast, #toastContainer');
    for (var t = 0; t < toastContainers.length; t++) {
      toastContainers[t].setAttribute('role', 'status');
      toastContainers[t].setAttribute('aria-live', 'polite');
    }

    // Log containers
    var logContainers = document.querySelectorAll('.log-content, #logContent');
    for (var l = 0; l < logContainers.length; l++) {
      logContainers[l].setAttribute('role', 'log');
      logContainers[l].setAttribute('aria-live', 'polite');
    }

    // Progress bars
    var progressBars = document.querySelectorAll('.progress-fill, .progress-bar');
    for (var p = 0; p < progressBars.length; p++) {
      if (!progressBars[p].getAttribute('role')) {
        progressBars[p].setAttribute('role', 'progressbar');
        progressBars[p].setAttribute('aria-valuemin', '0');
        progressBars[p].setAttribute('aria-valuemax', '100');
      }
    }

    // Home links
    var homeLinks = document.querySelectorAll('a[href="/"]');
    for (var h = 0; h < homeLinks.length; h++) {
      if (!homeLinks[h].getAttribute('aria-label')) {
        homeLinks[h].setAttribute('aria-label', 'Return to home page');
      }
    }
  }

  // ── Keyboard Activation for Drop Zones ────────────────────────────
  // Let Enter/Space activate file inputs on drop zones.

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    var target = e.target;
    if (target.classList.contains('drop-zone') || target.classList.contains('upload-zone')) {
      e.preventDefault();
      var input = target.querySelector('input[type="file"]');
      if (input && !input.disabled) {
        input.click();
      }
    }
  });

  // ── prefers-reduced-motion Support ────────────────────────────────
  // Inject a stylesheet that disables animations when the user prefers it.

  var reducedMotionCSS = document.createElement('style');
  reducedMotionCSS.textContent = [
    '@media (prefers-reduced-motion: reduce) {',
    '  *, *::before, *::after {',
    '    animation-duration: 0.01ms !important;',
    '    animation-iteration-count: 1 !important;',
    '    transition-duration: 0.01ms !important;',
    '    scroll-behavior: auto !important;',
    '  }',
    '  .spinner, .spinner-overlay .spinner, .upload-spinner,',
    '  .loading-dots .dot, .cat-eyes {',
    '    animation: none !important;',
    '  }',
    '}'
  ].join('\n');
  document.head.appendChild(reducedMotionCSS);

  // ── Contrast Fix for Dim Text ─────────────────────────────────────
  // Slightly boost contrast on text that uses very low opacity / dim colors.

  var contrastCSS = document.createElement('style');
  contrastCSS.textContent = [
    '/* Improve contrast on dim text for accessibility */',
    '.card-desc, .subtitle, .section-desc, .run-hint, .drop-zone-hint,',
    '.drop-hint, .file-info-label, .status-pending,',
    '[style*="color: #888"], [style*="color:#888"] {',
    '  color: rgba(255,255,255,0.6) !important;',
    '}',
    ':root {',
    '  --text-dim: rgba(255,255,255,0.6);',
    '}'
  ].join('\n');
  document.head.appendChild(contrastCSS);

  // ── Run After DOM Ready ───────────────────────────────────────────

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _applyAriaLabels);
  } else {
    _applyAriaLabels();
  }

})();
