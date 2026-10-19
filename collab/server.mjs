// The shared-editor service. It keeps the live copy of each session's document
// and sends changes between the people in the session (Hocuspocus, built on
// Yjs). It holds no data of its own: Sympraxi, the Django application, is where
// documents are kept, and this service loads and saves them through Sympraxi's
// internal address with a shared secret.
//
// People connect with a short-lived token that Sympraxi signed for one person
// and one document. A token that isn't signed with the shared secret, is for
// another document, or has expired, is refused before anything is sent.

import { createHmac, timingSafeEqual } from "node:crypto";
import http from "node:http";
import https from "node:https";

import { Server } from "@hocuspocus/server";
import { yDocToProsemirrorJSON } from "@tiptap/y-tiptap";
import * as Y from "yjs";

const SECRET = process.env.SYMPRAXI_COLLAB_SECRET || "";
const BACKEND = (process.env.SYMPRAXI_COLLAB_BACKEND || "http://web:8000").replace(/\/+$/, "");
// The site's own host name, which Django checks (SYMPRAXI_ALLOWED_HOSTS).
const HOST = process.env.SYMPRAXI_COLLAB_HOST || process.env.SYMPRAXI_DOMAIN || "";
const PORT = Number(process.env.SYMPRAXI_COLLAB_PORT || 1234);
const ADDRESS = process.env.SYMPRAXI_COLLAB_ADDRESS || "0.0.0.0";
// The name of the editor's content inside the Yjs document (Tiptap's default).
const FIELD = "default";

if (!SECRET) {
  console.error("SYMPRAXI_COLLAB_SECRET is not set; the shared editor has nothing to check tokens with.");
  process.exit(1);
}

/** Check a token Sympraxi signed (HS256) and return its claims, or throw. */
function verify(token, documentName, now = Date.now() / 1000) {
  const parts = String(token || "").split(".");
  if (parts.length !== 3) {
    throw new Error("malformed");
  }
  const [header, payload, signature] = parts;
  const expected = createHmac("sha256", SECRET).update(`${header}.${payload}`).digest();
  const given = Buffer.from(signature, "base64url");
  if (given.length !== expected.length || !timingSafeEqual(given, expected)) {
    throw new Error("bad signature");
  }
  if (JSON.parse(Buffer.from(header, "base64url").toString()).alg !== "HS256") {
    throw new Error("bad algorithm");
  }
  const claims = JSON.parse(Buffer.from(payload, "base64url").toString());
  if (claims.aud !== "sympraxi-collab") {
    throw new Error("wrong audience");
  }
  if (claims.doc !== documentName) {
    throw new Error("wrong document");
  }
  if (typeof claims.exp !== "number" || claims.exp < now) {
    throw new Error("expired");
  }
  if (typeof claims.nbf === "number" && claims.nbf > now + 60) {
    throw new Error("not yet valid");
  }
  return claims;
}

// Calls to Sympraxi go straight to the web service, not through Caddy, so they say
// what Caddy would: the site's own host name (Django only answers its own), and that
// the request arrived over HTTPS (or Django would redirect it there).
function call(path, body) {
  const url = new URL(`${BACKEND}/internal/collab/${path}/`);
  const client = url.protocol === "https:" ? https : http;
  const payload = JSON.stringify(body);
  const headers = {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(payload),
    "X-Collab-Secret": SECRET,
    "X-Forwarded-Proto": "https",
  };
  if (HOST) {
    headers.Host = HOST;
  }
  return new Promise((resolve, reject) => {
    const request = client.request(url, { method: "POST", headers, timeout: 30000 }, (response) => {
      const chunks = [];
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("end", () => {
        if (response.statusCode !== 200) {
          reject(new Error(`Sympraxi answered ${response.statusCode} to ${path}`));
          return;
        }
        try {
          resolve(JSON.parse(Buffer.concat(chunks).toString()));
        } catch (error) {
          reject(error);
        }
      });
    });
    request.on("timeout", () => request.destroy(new Error(`Sympraxi took too long to answer ${path}`)));
    request.on("error", reject);
    request.end(payload);
  });
}

const server = new Server({
  port: PORT,
  address: ADDRESS,
  quiet: true,
  // Save a couple of seconds after the last change, and at least every ten.
  debounce: 2000,
  maxDebounce: 10000,

  async onAuthenticate({ token, documentName }) {
    let claims;
    try {
      claims = verify(token, documentName);
    } catch (error) {
      throw new Error("not allowed");
    }
    return { user: { id: claims.sub, name: claims.name, role: claims.role } };
  },

  async onLoadDocument({ document, documentName }) {
    const { state } = await call("load", { doc: documentName });
    if (state) {
      Y.applyUpdate(document, Buffer.from(state, "base64"));
    }
    return document;
  },

  async onStoreDocument({ document, documentName }) {
    await call("store", {
      doc: documentName,
      state: Buffer.from(Y.encodeStateAsUpdate(document)).toString("base64"),
      content: yDocToProsemirrorJSON(document, FIELD),
      chat: document.getArray("chat").toJSON(),
    });
  },
});

await server.listen();
console.log(`The shared editor is listening on ${ADDRESS}:${PORT}`);
