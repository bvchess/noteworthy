/* Notes Viewer - Vanilla JS client */
(function () {
  "use strict";

  // Icons
  var FOLDER_SVG =
    '<svg width="18" height="14" viewBox="-1 -1 17 14" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round">' +
    '<path d="M1 2.5C1 1.67 1.67 1 2.5 1H6L7.5 3H12.5C13.33 3 14 3.67 14 4.5V10.5C14 11.33 13.33 12 12.5 12H2.5C1.67 12 1 11.33 1 10.5Z"/>' +
    "</svg>";
  var GEAR_SVG =
    '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M12.22 2h-.44a2 2 0 00-2 2v.18a2 2 0 01-1 1.73l-.43.25a2 2 0 01-2 0l-.15-.08a2 2 0 00-2.73.73l-.22.38a2 2 0 00.73 2.73l.15.1a2 2 0 011 1.72v.51a2 2 0 01-1 1.74l-.15.09a2 2 0 00-.73 2.73l.22.38a2 2 0 002.73.73l.15-.08a2 2 0 012 0l.43.25a2 2 0 011 1.73V20a2 2 0 002 2h.44a2 2 0 002-2v-.18a2 2 0 011-1.73l.43-.25a2 2 0 012 0l.15.08a2 2 0 002.73-.73l.22-.39a2 2 0 00-.73-2.73l-.15-.08a2 2 0 01-1-1.74v-.5a2 2 0 011-1.74l.15-.09a2 2 0 00.73-2.73l-.22-.38a2 2 0 00-2.73-.73l-.15.08a2 2 0 01-2 0l-.43-.25a2 2 0 01-1-1.73V4a2 2 0 00-2-2z"/>' +
    '<circle cx="12" cy="12" r="3"/>' +
    "</svg>";

  var FOLDER_SVG_SMALL =
    '<svg width="10" height="8" viewBox="-1 -1 17 14" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round">' +
    '<path d="M1 2.5C1 1.67 1.67 1 2.5 1H6L7.5 3H12.5C13.33 3 14 3.67 14 4.5V10.5C14 11.33 13.33 12 12.5 12H2.5C1.67 12 1 11.33 1 10.5Z"/>' +
    "</svg>";
  var CHEVRON_SVG =
    '<svg width="8" height="8" viewBox="0 0 8 8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M2 1L6 4L2 7"/>' +
    "</svg>";

  // State
  let currentFolderId = null;
  let currentFolderIsVirtual = false;
  let currentNoteId = null;
  let searchTimeout = null;
  let isSearching = false;

  // DOM references
  const folderTree = document.getElementById("folder-tree");
  const noteListHeader = document.getElementById("note-list-header");
  const noteListItems = document.getElementById("note-list-items");
  const noteDateBanner = document.getElementById("note-date-banner");
  const noteBody = document.getElementById("note-body");
  const searchInput = document.getElementById("search-input");
  const lightbox = document.getElementById("lightbox");
  const lightboxImg = document.getElementById("lightbox-img");
  const lightboxBackdrop = document.getElementById("lightbox-backdrop");
  const themeToggle = document.getElementById("theme-toggle");
  const themeIcon = document.getElementById("theme-icon");

  // --- API helpers ---
  async function api(path) {
    const resp = await fetch(path);
    if (!resp.ok) throw new Error(`API error: ${resp.status}`);
    return resp.json();
  }

  // --- Folder tree ---
  async function loadTree() {
    const data = await api("/api/tree");
    const accounts = data.accounts;
    const tags = data.tags || [];
    folderTree.innerHTML = "";
    for (const account of accounts) {
      const section = document.createElement("div");
      const label = document.createElement("div");
      label.className = "account-name";
      label.textContent = account.name;
      section.appendChild(label);
      for (const folder of account.folders) {
        section.appendChild(renderFolder(folder, 0));
      }
      folderTree.appendChild(section);
    }
    if (tags.length > 0) {
      renderTags(tags, data.tags_expanded !== false);
    }
  }

  function renderFolder(folder, depth) {
    const hasChildren = folder.children && folder.children.length > 0;

    if (hasChildren) {
      const details = document.createElement("details");
      details.open = folder.is_expanded !== undefined ? folder.is_expanded : depth < 1;

      const summary = document.createElement("summary");
      summary.appendChild(makeFolderItem(folder, true));
      details.appendChild(summary);

      const childContainer = document.createElement("div");
      childContainer.className = "folder-children";
      for (const child of folder.children) {
        childContainer.appendChild(renderFolder(child, depth + 1));
      }
      details.appendChild(childContainer);
      return details;
    }

    return makeFolderItem(folder, false);
  }

  function makeFolderItem(folder, hasChildren) {
    const isAllFolder = folder.is_all_folder;
    let extraClass = "";
    if (isAllFolder) extraClass = " all-folder";
    else if (folder.is_smart_folder) extraClass = " smart-folder";

    const div = document.createElement("div");
    div.className = "folder-item" + extraClass;
    div.dataset.folderId = folder.id;

    let html = "";
    if (hasChildren) {
      html += '<span class="expand-arrow">' + CHEVRON_SVG + '</span>';
    } else {
      html += '<span class="expand-arrow expand-arrow-hidden"></span>';
    }
    const icon = folder.is_smart_folder ? GEAR_SVG : FOLDER_SVG;
    html += '<span class="folder-icon">' + icon + "</span>";
    html += '<span class="folder-name">' + escapeHtml(folder.name) + "</span>";
    const count = hasChildren ? folder.total_note_count : folder.note_count;
    if (count > 0) {
      html += '<span class="folder-count">' + count + "</span>";
    }
    div.innerHTML = html;

    div.addEventListener("click", function (e) {
      e.stopPropagation();
      selectFolder(folder.id, folder.name, folder.is_smart_folder || folder.is_all_folder);
    });

    return div;
  }

  function selectFolder(folderId, folderName, isVirtual) {
    isSearching = false;
    searchInput.value = "";
    currentFolderId = folderId;
    currentFolderIsVirtual = !!isVirtual;
    currentNoteId = null;

    // Update selection highlight
    document.querySelectorAll(".folder-item.selected").forEach(function (el) {
      el.classList.remove("selected");
    });
    document.querySelectorAll('.folder-item[data-folder-id="' + folderId + '"]').forEach(function (el) {
      el.classList.add("selected");
    });

    noteListHeader.textContent = folderName || "";
    loadNotes(folderId);
  }

  // --- Note list ---
  async function loadNotes(folderId) {
    const data = await api("/api/notes?folder=" + encodeURIComponent(folderId));
    const sortOrder = data.sort_order || "default";
    renderNoteList(data.notes, sortOrder);
    noteListItems.focus();
    // Clear content area
    noteDateBanner.textContent = "";
    noteBody.innerHTML = '<div class="empty-state">Select a note to view</div>';
  }

  function renderNoteList(notes, sortOrder) {
    noteListItems.innerHTML = "";
    if (notes.length === 0) {
      noteListItems.innerHTML = '<div class="empty-state">No notes</div>';
      return;
    }
    for (const note of notes) {
      const div = document.createElement("div");
      div.className = "note-item";
      div.dataset.noteId = note.id || note.note_id;

      let textHtml = '<div class="note-item-title">' + escapeHtml(note.name || note.title) + "</div>";
      const displayDate = sortOrder === "date_created" ? note.creation_date : note.modification_date;
      if (displayDate) {
        textHtml += '<div class="note-item-date">' + formatDate(displayDate) + "</div>";
      }
      if (currentFolderIsVirtual && note.folder_name) {
        textHtml += '<div class="note-item-folder">' + FOLDER_SVG_SMALL + " " + escapeHtml(note.folder_name) + "</div>";
      } else if (note.preview) {
        textHtml += '<div class="note-item-preview">' + escapeHtml(note.preview) + "</div>";
      }
      if (note.snippet) {
        textHtml += '<div class="search-result-snippet">' + note.snippet + "</div>";
      }
      if (note.folder_path) {
        textHtml += '<div class="search-result-folder">' + escapeHtml(note.folder_path) + "</div>";
      }

      let html = '<div class="note-item-text">' + textHtml + "</div>";
      if (note.first_image) {
        html += '<div class="note-item-thumb"><img src="' + escapeHtml(note.first_image) + '" loading="lazy" alt=""></div>';
      }

      div.innerHTML = html;
      div.addEventListener("click", function () {
        selectNoteItem(div);
      });
      div.addEventListener("dblclick", function () {
        openNoteInNewWindow(note.id || note.note_id);
      });
      noteListItems.appendChild(div);
    }
  }

  // --- Note list keyboard navigation ---
  noteListItems.setAttribute("tabindex", "0");

  noteListItems.addEventListener("keydown", function (e) {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const items = noteListItems.querySelectorAll(".note-item");
    if (items.length === 0) return;
    const current = noteListItems.querySelector(".note-item.selected");
    const index = current ? Array.prototype.indexOf.call(items, current) : -1;
    let next;
    if (e.key === "ArrowDown") {
      next = index < items.length - 1 ? index + 1 : index;
    } else {
      next = index > 0 ? index - 1 : 0;
    }
    if (items[next] === current) return;
    selectNoteItem(items[next]);
    items[next].scrollIntoView({ block: "nearest" });
  });

  // --- Note item selection helper ---
  function selectNoteItem(noteItem) {
    document.querySelectorAll(".note-item.selected").forEach(function (el) {
      el.classList.remove("selected");
    });
    noteItem.classList.add("selected");
    selectNote(noteItem.dataset.noteId);
  }

  // --- Note content ---
  async function selectNote(noteId, pushHistory) {
    if (pushHistory === undefined) pushHistory = true;
    currentNoteId = noteId;
    if (pushHistory) {
      history.pushState({ noteId: noteId }, "");
    }
    const data = await api("/api/note/" + encodeURIComponent(noteId));

    // Show centered date banner like Apple Notes
    if (data.modification_date) {
      noteDateBanner.textContent = formatDateLong(data.modification_date);
    } else {
      noteDateBanner.textContent = "";
    }

    noteBody.innerHTML = data.html;

    // Wire up image lightbox
    noteBody.querySelectorAll(".image-link").forEach(function (link) {
      link.addEventListener("click", function (e) {
        e.preventDefault();
        lightboxImg.src = link.href;
        lightbox.classList.remove("hidden");
      });
    });

    // Wire up note-to-note links
    noteBody.querySelectorAll(".note-link").forEach(function (link) {
      link.addEventListener("click", async function (e) {
        e.preventDefault();
        var relPath = link.dataset.path;
        if (relPath && currentNoteId) {
          try {
            var resolved = await api(
              "/api/resolve-link?from=" + encodeURIComponent(currentNoteId) +
              "&rel=" + encodeURIComponent(relPath)
            );
            selectNote(resolved.note_id);
            return;
          } catch (_) {
            // Fall through to title search below
          }
        }
        // Fallback: search by link title
        searchInput.value = link.textContent;
        doSearch(link.textContent);
      });
    });
  }

  async function openNoteInNewWindow(noteId) {
    var data = await api("/api/note/" + encodeURIComponent(noteId));
    var dateLine = data.modification_date ? formatDateLong(data.modification_date) : "";
    var win = window.open("", "_blank");
    win.document.write(
      "<!DOCTYPE html><html" + (isDark() ? ' class="dark-mode"' : "") + ">" +
      "<head><meta charset=\"utf-8\"><title>" + escapeHtml(data.name) + "</title>" +
      '<link rel="stylesheet" href="/static/style.css">' +
      "<style>body{background:var(--bg-content);overflow:auto}" +
      "#note-standalone{max-width:900px;margin:0 auto;padding:24px 40px 40px;line-height:1.4;font-size:16px}" +
      "#note-standalone h1:first-child{font-size:28px;font-weight:700;margin:0 0 4px}" +
      ".standalone-date{font-size:13px;color:var(--text-secondary);text-align:center;margin-bottom:12px}</style>" +
      "</head><body>" +
      '<div id="note-standalone">' +
      "<h1>" + escapeHtml(data.name) + "</h1>" +
      '<div class="standalone-date">' + escapeHtml(dateLine) + "</div>" +
      '<div id="note-body">' + data.html + "</div>" +
      "</div></body></html>"
    );
    win.document.close();
  }

  // --- Search ---
  searchInput.addEventListener("input", function () {
    clearTimeout(searchTimeout);
    var query = searchInput.value.trim();
    if (!query) {
      isSearching = false;
      if (currentFolderId) {
        loadNotes(currentFolderId);
      } else {
        noteListItems.innerHTML = "";
        noteListHeader.textContent = "";
      }
      return;
    }
    searchTimeout = setTimeout(function () {
      doSearch(query);
    }, 300);
  });

  async function doSearch(query) {
    isSearching = true;
    noteListHeader.textContent = 'Search: "' + query + '"';
    var results = await api("/api/search?q=" + encodeURIComponent(query));
    renderNoteList(results, "default");
  }

  // --- Tags ---
  function renderTags(tags, expanded) {
    const details = document.createElement("details");
    details.className = "tags-section";
    if (expanded) details.open = true;
    const summary = document.createElement("summary");
    summary.className = "tags-section-label";
    summary.textContent = "Tags";
    details.appendChild(summary);
    const container = document.createElement("div");
    container.className = "tags-container";
    for (const tag of tags) {
      const pill = document.createElement("button");
      pill.className = "tag-pill";
      pill.textContent = "#" + tag;
      pill.addEventListener("click", function () {
        searchInput.value = "#" + tag;
        doSearch("#" + tag);
      });
      container.appendChild(pill);
    }
    details.appendChild(container);
    folderTree.appendChild(details);
  }

  // --- Lightbox ---
  lightboxBackdrop.addEventListener("click", closeLightbox);
  lightboxImg.addEventListener("click", closeLightbox);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeLightbox();
  });

  function closeLightbox() {
    lightbox.classList.add("hidden");
    lightboxImg.src = "";
  }

  // --- Theme toggle ---
  function isDark() {
    return document.documentElement.classList.contains("dark-mode");
  }

  function setTheme(dark) {
    var html = document.documentElement;
    html.classList.remove("dark-mode", "light-mode");
    html.classList.add(dark ? "dark-mode" : "light-mode");
    localStorage.setItem("viewer-theme", dark ? "dark" : "light");
    themeIcon.textContent = dark ? "\u2600" : "\u263E";
  }

  function initTheme() {
    var saved = localStorage.getItem("viewer-theme");
    if (saved === "dark") {
      setTheme(true);
    } else if (saved === "light") {
      setTheme(false);
    } else {
      // No saved preference — detect system and commit to it
      var systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      setTheme(systemDark);
    }
  }

  themeToggle.addEventListener("click", function () {
    setTheme(!isDark());
  });

  // --- Helpers ---
  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  function formatDate(isoStr) {
    try {
      var d = new Date(isoStr);
      return d.toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
    } catch (e) {
      return isoStr;
    }
  }

  function formatDateLong(isoStr) {
    try {
      var d = new Date(isoStr);
      return d.toLocaleDateString(undefined, {
        year: "numeric",
        month: "long",
        day: "numeric",
      }) + " at " + d.toLocaleTimeString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch (e) {
      return isoStr;
    }
  }

  // --- History navigation ---
  window.addEventListener("popstate", function (e) {
    var state = e.state || {};
    if (state.noteId) {
      selectNote(state.noteId, false);
      document.querySelectorAll(".note-item").forEach(function (el) {
        el.classList.toggle("selected", el.dataset.noteId === state.noteId);
      });
    } else {
      currentNoteId = null;
      noteDateBanner.textContent = "";
      noteBody.innerHTML = '<div class="empty-state">Select a note to view</div>';
      document.querySelectorAll(".note-item.selected").forEach(function (el) {
        el.classList.remove("selected");
      });
    }
  });

  // --- Init ---
  initTheme();
  history.replaceState({ noteId: null }, "");
  loadTree();
  noteBody.innerHTML = '<div class="empty-state">Select a folder to browse notes</div>';
})();
