// The shared document of a session: a Tiptap editor whose content is a Yjs document
// kept in step with everyone else's through the Hocuspocus service, a chat in the
// same Yjs document, and who is here. Built into one file (`npm run build`) and
// loaded as a module only on the room page. Its settings come from the JSON block
// "room-config", because the CSP allows no inline script.

import { HocuspocusProvider } from "@hocuspocus/provider";
import { Editor } from "@tiptap/core";
import Collaboration from "@tiptap/extension-collaboration";
import CollaborationCaret from "@tiptap/extension-collaboration-caret";
import StarterKit from "@tiptap/starter-kit";
import * as Y from "yjs";

// Colors for each person's cursor, dark enough to read as a label on white.
const COLORS = ["#005ea2", "#b50909", "#1a4480", "#4c3b8c", "#0b6b3a", "#8a4600", "#7a1f5c", "#3d4551"];

function colorFor(id) {
  let hash = 0;
  for (const character of String(id)) {
    hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  }
  return COLORS[hash % COLORS.length];
}

function start(config) {
  const status = document.getElementById("room-status");
  const people = document.getElementById("room-people");
  const labels = config.labels;
  const ydoc = new Y.Doc();
  const provider = new HocuspocusProvider({
    url: config.url,
    name: config.document,
    document: ydoc,
    token: config.token,
  });
  const me = { name: config.name, color: colorFor(config.name) };

  provider.on("status", ({ status: current }) => {
    status.textContent =
      current === "connected"
        ? labels.connected
        : current === "connecting"
          ? labels.connecting
          : labels.offline;
  });
  provider.on("authenticationFailed", () => {
    status.textContent = labels.offline;
  });

  // --- The editor -------------------------------------------------------------------

  const editor = new Editor({
    element: document.getElementById("room-editor"),
    // Tiptap would add a <style> element, which the CSP blocks; its few base rules
    // are in sympraxi.css instead.
    injectCSS: false,
    extensions: [
      // The shared history replaces the editor's own, and links aren't wanted here.
      StarterKit.configure({ undoRedo: false, link: false, heading: { levels: [1, 2, 3] } }),
      Collaboration.configure({ document: ydoc }),
      CollaborationCaret.configure({
        provider,
        user: me,
        // Built without style attributes, which the CSP blocks; the color is set on the element.
        render: (user) => {
          const caret = document.createElement("span");
          caret.className = "collaboration-carets__caret";
          caret.style.borderColor = user.color;
          const label = document.createElement("span");
          label.className = "collaboration-carets__label";
          label.style.backgroundColor = user.color;
          label.textContent = user.name;
          caret.append(label);
          return caret;
        },
        selectionRender: () => ({ nodeName: "span", class: "collaboration-carets__selection" }),
      }),
    ],
    editorProps: {
      attributes: {
        role: "textbox",
        "aria-multiline": "true",
        "aria-label": labels.editor,
        class: "sympraxi-room__prose",
      },
    },
  });

  const toolbar = document.getElementById("room-toolbar");
  const buttons = [];

  function addButton(label, run, active) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "usa-button usa-button--outline";
    button.textContent = label;
    if (active) {
      button.setAttribute("aria-pressed", "false");
    }
    button.addEventListener("click", () => {
      run();
      editor.view.focus();
    });
    toolbar.append(button);
    buttons.push({ button, active });
  }

  addButton(config.toolbar.bold, () => editor.chain().toggleBold().run(), () => editor.isActive("bold"));
  addButton(config.toolbar.italic, () => editor.chain().toggleItalic().run(), () => editor.isActive("italic"));
  addButton(
    config.toolbar.heading,
    () => editor.chain().toggleHeading({ level: 2 }).run(),
    () => editor.isActive("heading", { level: 2 }),
  );
  addButton(config.toolbar.bullets, () => editor.chain().toggleBulletList().run(), () => editor.isActive("bulletList"));
  addButton(config.toolbar.numbers, () => editor.chain().toggleOrderedList().run(), () => editor.isActive("orderedList"));
  addButton(config.toolbar.undo, () => editor.chain().undo().run());
  addButton(config.toolbar.redo, () => editor.chain().redo().run());

  editor.on("transaction", () => {
    for (const { button, active } of buttons) {
      if (active) {
        button.setAttribute("aria-pressed", active() ? "true" : "false");
      }
    }
  });

  // --- Who is here ------------------------------------------------------------------

  function showPeople() {
    const names = new Set();
    for (const [client, state] of provider.awareness.getStates()) {
      if (client !== provider.awareness.clientID && state.user && state.user.name) {
        names.add(state.user.name);
      }
    }
    people.textContent = names.size
      ? labels.here.replace("{names}", [...names].join(", "))
      : labels.alone;
  }
  provider.awareness.on("change", showPeople);
  showPeople();

  // --- The chat ---------------------------------------------------------------------

  const chat = ydoc.getArray("chat");
  const log = document.getElementById("room-chat");
  const form = document.getElementById("room-chat-form");
  const input = document.getElementById("room-chat-text");

  function showChat() {
    log.replaceChildren();
    for (const message of chat.toArray()) {
      const item = document.createElement("li");
      const name = document.createElement("strong");
      name.textContent = String(message.name || "") + ": ";
      const text = document.createElement("span");
      text.textContent = String(message.text || "");
      item.append(name, text);
      log.append(item);
    }
    log.scrollTop = log.scrollHeight;
  }
  chat.observe(showChat);
  showChat();

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) {
      return;
    }
    chat.push([
      {
        id: crypto.randomUUID(),
        name: config.name,
        text: text.slice(0, 1000),
        at: new Date().toISOString(),
      },
    ]);
    input.value = "";
    input.focus();
  });

  // --- Saving a version -------------------------------------------------------------

  document.getElementById("room-save").addEventListener("click", async () => {
    // The service saves a moment after the last change; give it that moment.
    await new Promise((done) => setTimeout(done, 2500));
    const response = await fetch(config.versionsUrl, {
      method: "POST",
      headers: { "X-CSRFToken": config.csrf, Accept: "application/json" },
      credentials: "same-origin",
    });
    status.textContent = response.ok ? labels.saved : labels.saveFailed;
  });
}

const element = document.getElementById("room-config");
if (element) {
  start(JSON.parse(element.textContent));
}
