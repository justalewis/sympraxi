// The comment viewer: draws a PDF with PDF.js and lets a tutor comment on it.
// Loaded as a module only on the viewer page, from a file because the CSP allows
// no inline script. Everything it needs comes from the JSON block "viewer-config".
// Without it the page still offers the file to download.

const configElement = document.getElementById("viewer-config");

if (configElement) {
  const config = JSON.parse(configElement.textContent);
  start(config).catch((error) => {
    console.error(error);
    say(document.getElementById("viewer-status"), config.labels.failed);
  });
}

function say(element, message) {
  if (element) {
    element.textContent = message;
  }
}

async function start(config) {
  const status = document.getElementById("viewer-status");
  const pagesElement = document.getElementById("viewer-pages");
  const base = config.library.slice(0, config.library.lastIndexOf("/") + 1);
  const pdfjs = await import(config.library);
  pdfjs.GlobalWorkerOptions.workerSrc = config.worker;
  const doc = await pdfjs.getDocument({
    url: config.pdf,
    cMapUrl: base + "cmaps/",
    cMapPacked: true,
    standardFontDataUrl: base + "standard_fonts/",
    wasmUrl: base + "wasm/",
    iccUrl: base + "iccs/",
    isEvalSupported: false,
    enableXfa: false,
  }).promise;

  const comments = config.comments.slice();
  const pageViews = new Map();
  let selection = null;
  let width = 0;

  const list = document.getElementById("comment-list");
  const empty = document.getElementById("comment-empty");
  const form = document.getElementById("comment-form");
  const selectButton = document.getElementById("comment-selection");
  const pageButton = document.getElementById("comment-page");
  const pageInput = document.getElementById("comment-page-number");
  const quoteElement = document.getElementById("comment-quote");
  const bodyInput = document.getElementById("comment-body");
  const cancelButton = document.getElementById("comment-cancel");
  const heading = document.getElementById("comments-heading");

  async function build() {
    width = pagesElement.clientWidth;
    pagesElement.replaceChildren();
    pageViews.clear();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            observer.unobserve(entry.target);
            draw(Number(entry.target.dataset.page));
          }
        }
      },
      { rootMargin: "600px 0px" },
    );
    for (let number = 1; number <= doc.numPages; number += 1) {
      const page = await doc.getPage(number);
      const natural = page.getViewport({ scale: 1 });
      const scale = Math.min(1.6, width / natural.width);
      const viewport = page.getViewport({ scale });
      const section = document.createElement("section");
      section.className = "viewer-page";
      section.dataset.page = String(number);
      section.setAttribute("aria-label", config.labels.page.replace("{n}", number).replace("{total}", doc.numPages));
      section.style.width = viewport.width + "px";
      section.style.height = viewport.height + "px";
      pagesElement.append(section);
      pageViews.set(number, { section, page, scale, drawn: false });
      observer.observe(section);
    }
    showHighlights();
    say(status, "");
  }

  async function draw(number) {
    const view = pageViews.get(number);
    if (!view || view.drawn) {
      return;
    }
    view.drawn = true;
    const { section, page, scale } = view;
    const ratio = window.devicePixelRatio || 1;
    const viewport = page.getViewport({ scale });
    const canvas = document.createElement("canvas");
    canvas.setAttribute("aria-hidden", "true");
    canvas.width = Math.floor(viewport.width * ratio);
    canvas.height = Math.floor(viewport.height * ratio);
    canvas.style.width = viewport.width + "px";
    canvas.style.height = viewport.height + "px";
    const textLayer = document.createElement("div");
    textLayer.className = "textLayer";
    textLayer.style.setProperty("--total-scale-factor", String(scale));
    textLayer.style.width = viewport.width + "px";
    textLayer.style.height = viewport.height + "px";
    const marks = document.createElement("div");
    marks.className = "viewer-marks";
    marks.setAttribute("aria-hidden", "true");
    section.append(canvas, marks, textLayer);
    const context = canvas.getContext("2d");
    await page.render({
      canvasContext: context,
      viewport: page.getViewport({ scale: scale * ratio }),
    }).promise;
    const text = new pdfjs.TextLayer({
      textContentSource: page.streamTextContent(),
      container: textLayer,
      viewport,
    });
    await text.render();
    view.marks = marks;
    showHighlights(number);
  }

  function showHighlights(only) {
    for (const [number, view] of pageViews) {
      if (!view.marks || (only && number !== only)) {
        continue;
      }
      view.marks.replaceChildren();
      for (const comment of comments) {
        if (comment.page !== number) {
          continue;
        }
        for (const [left, top, w, h] of comment.rects) {
          const mark = document.createElement("div");
          mark.className = "viewer-highlight";
          mark.style.left = left * 100 + "%";
          mark.style.top = top * 100 + "%";
          mark.style.width = w * 100 + "%";
          mark.style.height = h * 100 + "%";
          view.marks.append(mark);
        }
      }
    }
  }

  function renderList() {
    list.replaceChildren();
    for (const comment of comments) {
      const item = document.createElement("li");
      item.className = "viewer-comment";
      item.id = "comment-" + comment.id;
      const meta = document.createElement("p");
      meta.className = "usa-hint";
      meta.textContent = config.labels.meta
        .replace("{page}", comment.page)
        .replace("{author}", comment.author);
      item.append(meta);
      if (comment.quote) {
        const quote = document.createElement("blockquote");
        quote.className = "viewer-quote";
        quote.textContent = comment.quote;
        item.append(quote);
      }
      const body = document.createElement("p");
      body.textContent = comment.body;
      item.append(body);
      const go = document.createElement("button");
      go.type = "button";
      go.className = "usa-button usa-button--unstyled";
      go.textContent = config.labels.show;
      go.addEventListener("click", () => scrollToPage(comment.page));
      item.append(go);
      if (comment.mine && config.canComment) {
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "usa-button usa-button--unstyled sympraxi-danger";
        remove.textContent = config.labels.remove;
        remove.addEventListener("click", () => removeComment(comment));
        item.append(" ", remove);
      }
      list.append(item);
    }
    empty.hidden = comments.length > 0;
  }

  function scrollToPage(number) {
    const view = pageViews.get(number);
    if (view) {
      view.section.scrollIntoView({ block: "start" });
    }
  }

  function selectionDetails() {
    const current = window.getSelection();
    if (!current || current.rangeCount === 0 || current.isCollapsed) {
      return null;
    }
    const range = current.getRangeAt(0);
    const node = range.commonAncestorContainer;
    const element = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
    const section = element && element.closest(".viewer-page");
    const text = current.toString().replace(/\s+/g, " ").trim();
    if (!section || !text) {
      return null;
    }
    const box = section.getBoundingClientRect();
    const rects = [];
    for (const r of range.getClientRects()) {
      if (r.width < 1 || r.height < 1) {
        continue;
      }
      rects.push([
        Math.max(0, (r.left - box.left) / box.width),
        Math.max(0, (r.top - box.top) / box.height),
        Math.min(1, r.width / box.width),
        Math.min(1, r.height / box.height),
      ]);
      if (rects.length >= 40) {
        break;
      }
    }
    return { page: Number(section.dataset.page), quote: text.slice(0, 500), rects };
  }

  document.addEventListener("selectionchange", () => {
    const found = selectionDetails();
    if (found) {
      selection = found;
    }
    if (selectButton) {
      selectButton.disabled = !selection;
    }
  });

  function openForm(details) {
    selection = null;
    form.hidden = false;
    form.dataset.rects = JSON.stringify(details.rects);
    form.dataset.quote = details.quote;
    pageInput.value = String(details.page);
    quoteElement.textContent = details.quote;
    quoteElement.hidden = !details.quote;
    bodyInput.value = "";
    bodyInput.focus();
  }

  if (config.canComment) {
    selectButton.addEventListener("click", () => {
      if (selection) {
        openForm(selection);
      }
    });
    pageButton.addEventListener("click", () => openForm({ page: 1, quote: "", rects: [] }));
    cancelButton.addEventListener("click", () => {
      form.hidden = true;
      pageButton.focus();
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const page = Number(pageInput.value);
      if (!Number.isInteger(page) || page < 1 || page > doc.numPages) {
        say(status, config.labels.badPage.replace("{total}", doc.numPages));
        pageInput.focus();
        return;
      }
      const response = await fetch(config.commentsUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": config.csrf },
        credentials: "same-origin",
        body: JSON.stringify({
          page,
          quote: form.dataset.quote || "",
          rects: JSON.parse(form.dataset.rects || "[]"),
          body: bodyInput.value,
        }),
      });
      if (!response.ok) {
        say(status, (await response.json().catch(() => ({}))).error || config.labels.error);
        return;
      }
      const made = await response.json();
      comments.push(made);
      comments.sort((a, b) => a.page - b.page);
      form.hidden = true;
      renderList();
      showHighlights(made.page);
      say(status, config.labels.saved);
      const item = document.getElementById("comment-" + made.id);
      if (item) {
        item.setAttribute("tabindex", "-1");
        item.focus();
      }
    });
  }

  async function removeComment(comment) {
    const response = await fetch(config.commentsUrl + "?comment=" + comment.id, {
      method: "DELETE",
      headers: { "X-CSRFToken": config.csrf },
      credentials: "same-origin",
    });
    if (!response.ok) {
      say(status, config.labels.error);
      return;
    }
    comments.splice(comments.indexOf(comment), 1);
    renderList();
    showHighlights(comment.page);
    say(status, config.labels.removed);
    heading.setAttribute("tabindex", "-1");
    heading.focus();
  }

  renderList();
  await build();
  let resizing = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizing);
    resizing = setTimeout(() => {
      if (Math.abs(pagesElement.clientWidth - width) > 20) {
        build();
      }
    }, 250);
  });
}
