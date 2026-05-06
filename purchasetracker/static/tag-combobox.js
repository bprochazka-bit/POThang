/*
 * tag-combobox.js
 *
 * Lightweight tag-picker combobox. No dependencies. Wraps a plain <input>
 * and adds a popup list of suggestions that:
 *   - opens on focus or click (showing all suggestions, even with empty input)
 *   - filters as the user types (case-insensitive substring match)
 *   - allows keyboard nav (Up/Down/Enter/Escape)
 *   - closes on click-outside or Escape
 *   - creates a new tag on Enter / blur if the typed value isn't in the list
 *
 * Usage:
 *
 *   <div class="tagbox">
 *     <input id="task-input" type="text" autocomplete="off">
 *     <ul class="tagbox-list" hidden></ul>
 *   </div>
 *
 *   const tb = new TagCombobox({
 *     input: document.getElementById("task-input"),
 *     list:  document.querySelector("#task-input + .tagbox-list"),
 *     suggestions: ["Camp 2026", "Lab refresh", ...],
 *     onSelect: (value) => { ... },        // value chosen / typed
 *   });
 *
 *   tb.setSuggestions(newArray);   // replace the suggestion list
 *
 * For multi-tag (comma-separated) inputs like the item edit form's tags
 * field, set `multi: true`. The combobox then operates on the last token
 * (after the last comma) and inserts the chosen value in place of it.
 */

(function (global) {
  "use strict";

  function TagCombobox(opts) {
    this.input = opts.input;
    this.list = opts.list;
    this.suggestions = (opts.suggestions || []).slice();
    this.onSelect = opts.onSelect || function () {};
    this.multi = !!opts.multi;
    this.activeIndex = -1;
    this.filtered = [];

    this._bind();
  }

  TagCombobox.prototype.setSuggestions = function (arr) {
    this.suggestions = (arr || []).slice();
    if (!this.list.hidden) this._render();
  };

  // ---- internals ----

  TagCombobox.prototype._currentToken = function () {
    if (!this.multi) return this.input.value;
    const v = this.input.value;
    const lastComma = v.lastIndexOf(",");
    return (lastComma === -1 ? v : v.slice(lastComma + 1)).trim();
  };

  TagCombobox.prototype._otherTokens = function () {
    if (!this.multi) return [];
    const v = this.input.value;
    const lastComma = v.lastIndexOf(",");
    if (lastComma === -1) return [];
    return v.slice(0, lastComma)
      .split(",")
      .map(function (s) { return s.trim(); })
      .filter(Boolean);
  };

  TagCombobox.prototype._filter = function () {
    const token = this._currentToken().toLowerCase();
    const used = new Set(this._otherTokens().map(function (s) { return s.toLowerCase(); }));
    this.filtered = this.suggestions.filter(function (s) {
      if (used.has(s.toLowerCase())) return false;
      if (!token) return true;
      return s.toLowerCase().indexOf(token) !== -1;
    });
    // Sort: exact prefix matches first, then substring matches.
    if (token) {
      this.filtered.sort(function (a, b) {
        const ap = a.toLowerCase().indexOf(token) === 0 ? 0 : 1;
        const bp = b.toLowerCase().indexOf(token) === 0 ? 0 : 1;
        return ap - bp;
      });
    }
  };

  TagCombobox.prototype._render = function () {
    this._filter();
    this.list.innerHTML = "";

    if (this.filtered.length === 0) {
      const token = this._currentToken();
      if (!token) {
        this.list.innerHTML = '<li class="tagbox-empty">No tags yet — type one to create it.</li>';
      } else {
        this.list.innerHTML =
          '<li class="tagbox-empty">No matches. Press <kbd>Enter</kbd> to create "<strong>' +
          escapeHtml(token) +
          '</strong>".</li>';
      }
    } else {
      const self = this;
      this.filtered.forEach(function (s, i) {
        const li = document.createElement("li");
        li.className = "tagbox-item";
        if (i === self.activeIndex) li.classList.add("active");
        li.textContent = s;
        li.dataset.value = s;
        li.addEventListener("mousedown", function (e) {
          // mousedown (not click) so we run before the input loses focus.
          e.preventDefault();
          self._choose(s);
        });
        self.list.appendChild(li);
      });
    }
    this.list.hidden = false;
  };

  TagCombobox.prototype._close = function () {
    this.list.hidden = true;
    this.activeIndex = -1;
  };

  TagCombobox.prototype._choose = function (value) {
    if (this.multi) {
      const others = this._otherTokens();
      others.push(value);
      this.input.value = others.join(", ") + ", ";
    } else {
      this.input.value = value;
    }
    this._close();
    this.onSelect(value);
  };

  TagCombobox.prototype._commitTyped = function () {
    const token = this._currentToken().trim();
    if (!token) return;
    // If the typed token matches an existing suggestion case-insensitively,
    // snap to the canonical casing.
    const match = this.suggestions.find(function (s) {
      return s.toLowerCase() === token.toLowerCase();
    });
    this._choose(match || token);
  };

  TagCombobox.prototype._bind = function () {
    const self = this;

    this.input.addEventListener("focus", function () { self._render(); });
    this.input.addEventListener("click", function () { self._render(); });
    this.input.addEventListener("input", function () {
      self.activeIndex = -1;
      self._render();
    });

    this.input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (self.list.hidden) { self._render(); return; }
        self.activeIndex = Math.min(self.filtered.length - 1, self.activeIndex + 1);
        self._render();
        self._scrollActive();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        if (self.list.hidden) return;
        self.activeIndex = Math.max(0, self.activeIndex - 1);
        self._render();
        self._scrollActive();
      } else if (e.key === "Enter") {
        if (!self.list.hidden && self.activeIndex >= 0
            && self.filtered[self.activeIndex]) {
          e.preventDefault();
          self._choose(self.filtered[self.activeIndex]);
        } else {
          // Let outer form handle Enter (e.g. submit). Just close the popup.
          self._close();
          self._commitTyped();
        }
      } else if (e.key === "Escape") {
        self._close();
      } else if (e.key === "Tab") {
        if (!self.list.hidden && self.activeIndex >= 0
            && self.filtered[self.activeIndex]) {
          self._choose(self.filtered[self.activeIndex]);
          // don't preventDefault - let Tab move focus
        } else {
          self._commitTyped();
          self._close();
        }
      }
    });

    this.input.addEventListener("blur", function () {
      // Delay so a click on a list item registers first.
      setTimeout(function () { self._close(); }, 120);
    });

    // Click outside closes the popup.
    document.addEventListener("mousedown", function (e) {
      if (e.target !== self.input && !self.list.contains(e.target)) {
        self._close();
      }
    });
  };

  TagCombobox.prototype._scrollActive = function () {
    const active = this.list.querySelector(".tagbox-item.active");
    if (active && active.scrollIntoView) {
      active.scrollIntoView({block: "nearest"});
    }
  };

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  global.TagCombobox = TagCombobox;
})(window);
