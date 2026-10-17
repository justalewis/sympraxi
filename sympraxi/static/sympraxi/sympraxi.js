// Sympraxi's own script, loaded on every page from a file because the CSP
// allows no inline script. Every page works without it.

(function () {
  "use strict";

  // A form marked data-submit-once ignores a second submit while the first is
  // on its way, so a double-click on "Book it" sends one booking. The buttons
  // get aria-disabled, not disabled: a disabled button is left out of the
  // submission, and the Continue button's name has to reach the server.
  var BUTTONS = 'button:not([type]), button[type="submit"], input[type="submit"]';

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-submit-once")) {
      return;
    }
    if (event.defaultPrevented) {
      return;
    }
    if (form.dataset.submitting === "true") {
      event.preventDefault();
      return;
    }
    form.dataset.submitting = "true";
    form.querySelectorAll(BUTTONS).forEach(function (button) {
      button.setAttribute("aria-disabled", "true");
    });
  });

  // The Back button can restore a page as it was left, mid-submit. Let its
  // forms be sent again.
  window.addEventListener("pageshow", function (event) {
    if (!event.persisted) {
      return;
    }
    document.querySelectorAll("form[data-submit-once]").forEach(function (form) {
      delete form.dataset.submitting;
      form.querySelectorAll(BUTTONS).forEach(function (button) {
        button.removeAttribute("aria-disabled");
      });
    });
  });
})();
