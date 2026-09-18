"""Build player-facing Az puzzle pages from ordinary authored HTML."""

import base64
import hashlib
import json
from html import escape
from html.parser import HTMLParser
import mimetypes
from pathlib import Path
from typing import NamedTuple

from PIL import Image

SECRET_FILE = "secret.json"
PAGE_FILE = "index.html"
LINK_MAGIC = "az-link:"
IMAGE_MAGIC = b"az-image:"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
SKIP_TEXT_TAGS = {"script", "style", "textarea"}
PLAIN_ATTR = "data-az-plain"
UNLOCK_ATTR = "data-az-unlock"
FIELDS_TOKEN = "<!--az-fields-->"
UNLOCK_TOKEN = "<!--az-unlock-here-->"


class Frame(NamedTuple):
    """One open element: whether it turned cleartext on, and whether it vanished."""

    tag: str
    opened_plain: bool
    swallowed: bool
GARBAGE_GLYPHS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789#$%*+=?"
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "source", "track", "wbr",
}

RUNTIME = """
<script>
if (location.search.includes("debug")) {
    document.write('<script src="https://cdnjs.cloudflare.com/ajax/libs/eruda/3.4.3/eruda.min.js"><\\/script>');
    document.write('<script>eruda.init();<\\/script>');
}
</script>
<svg style="position:absolute;width:0;height:0" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <filter id="az-ink" x="-30%" y="-60%" width="160%" height="220%">
      <feTurbulence type="fractalNoise" baseFrequency="0.8" numOctaves="2" seed="7" result="grain"/>
      <feComponentTransfer in="grain" result="grainAlpha">
        <feFuncA type="linear" slope="2.6" intercept="-0.7"/>
      </feComponentTransfer>
      <feComposite in="SourceGraphic" in2="grainAlpha" operator="in"/>
    </filter>
  </defs>
</svg>
<style>
    .az-unlock { display: grid; place-items: center; gap: 0.35rem; min-height: 9rem; padding: 2rem 0; }
    .az-unlock input { min-width: 18rem; text-align: center; }
    .az-prompt { display: block; text-align: center; font-size: 0.8rem; opacity: 0.65; }
    .az-cursor { background: #fff; color: #000; }
</style>
<section class="az-unlock">
<!--az-fields-->
    <span class="az-field"><button id="az-unlock-button" type="button">Unlock</button></span>
</section>
<script>
document.addEventListener("DOMContentLoaded", () => {
    const glyphs = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789@#$%&*+=?";
    const unsafe = /[\\p{Cf}\\p{Cs}\\p{Co}\\p{Zl}\\p{Zp}\\u0000-\\u0008\\u000B\\u000C\\u000E-\\u001F\\u007F-\\u009F]/gu;
    const decoder = new TextDecoder();
    const cipherNodes = [...document.querySelectorAll("[data-az-cipher]")];
    const linkNodes = [...document.querySelectorAll("[data-az-href]")];
    const imageNodes = [...document.querySelectorAll("[data-az-image]")].map(image => ({ image }));
    const bytes = encoded => Uint8Array.from(atob(encoded), char => char.charCodeAt(0));
    const keyInputs = [...document.querySelectorAll("[data-az-field]")];
    // Forgiving on purpose: this gets typed one-handed on a phone in the sun,
    // so case, spaces and punctuation are all thrown away before hashing. The
    // build side folds the authored answer exactly the same way.
    const normalize = value => value.toUpperCase().replace(/[^A-Z0-9]/g, "");
    const composedKey = () => keyInputs.map(input => normalize(input.value)).join("");
    let attempt = 0;

    function mulberry32(seed) {
        return function () {
            seed |= 0;
            seed = (seed + 0x6D2B79F5) | 0;
            let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
            t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
            return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
        };
    }

    // A single wobbly, hand-scrawled stroke around a rectangle: jittered
    // corners, one gentle bow per edge, and a short pen-lift flick where
    // the line closes back on itself.
    function inkRectPath(x, y, w, h, rand) {
        const jitter = amt => (rand() - 0.5) * 2 * amt;
        const corner = (cx, cy) => [cx + jitter(1.2), cy + jitter(1.2)];
        const bow = (p1, p2, amt) => {
            const mx = (p1[0] + p2[0]) / 2;
            const my = (p1[1] + p2[1]) / 2;
            const dx = p2[0] - p1[0];
            const dy = p2[1] - p1[1];
            const len = Math.hypot(dx, dy) || 1;
            const offset = jitter(amt);
            return [mx + (-dy / len) * offset, my + (dx / len) * offset];
        };
        const tl = corner(x, y);
        const tr = corner(x + w, y);
        const br = corner(x + w, y + h);
        const bl = corner(x, y + h);
        const top = bow(tl, tr, 1.6);
        const right = bow(tr, br, 1.6);
        const bottom = bow(br, bl, 1.6);
        const left = bow(bl, tl, 1.6);
        let d = `M ${tl[0]} ${tl[1]}`;
        d += ` Q ${top[0]} ${top[1]} ${tr[0]} ${tr[1]}`;
        d += ` Q ${right[0]} ${right[1]} ${br[0]} ${br[1]}`;
        d += ` Q ${bottom[0]} ${bottom[1]} ${bl[0]} ${bl[1]}`;
        d += ` Q ${left[0]} ${left[1]} ${tl[0]} ${tl[1]}`;
        return d;
    }

    function sketchField(field) {
        const control = field.querySelector("input, button");
        if (!control) return;
        field.querySelectorAll(".az-sketch").forEach(svg => svg.remove());
        const pad = 8;
        const width = control.offsetWidth;
        const height = control.offsetHeight;
        const seedSalt = Number(control.dataset.azSeed || 0);
        const baseSeed = Math.round(width * 7 + height * 13 + seedSalt * 101);
        const draw = (className, stroke, seed) => {
            const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
            svg.classList.add("az-sketch", className);
            svg.setAttribute("viewBox", `0 0 ${width + pad * 2} ${height + pad * 2}`);
            field.insertBefore(svg, control);
            const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
            path.setAttribute("d", inkRectPath(pad, pad, width, height, mulberry32(seed)));
            path.setAttribute("fill", "none");
            path.setAttribute("stroke", stroke);
            path.setAttribute("stroke-width", "1.75");
            path.setAttribute("stroke-linecap", "round");
            path.setAttribute("stroke-linejoin", "round");
            path.setAttribute("filter", "url(#az-ink)");
            svg.appendChild(path);
        };
        if (control.tagName === "BUTTON") {
            draw("az-sketch-normal", "#b0b0b0", baseSeed);
            draw("az-sketch-hover", "#000", baseSeed + 1);
        } else {
            draw("az-sketch-normal", "#b0b0b0", baseSeed);
        }
    }

    function sketchFields() {
        document.querySelectorAll(".az-field").forEach(sketchField);
    }

    // Plain-JS SHA-256 (matches hashlib.sha256 on the build side byte for
    // byte). crypto.subtle needs a secure context (HTTPS/localhost) and is
    // simply unavailable over a plain-http LAN address, so we don't rely on
    // it for what's just puzzle obfuscation, not real security.
    function rotr(x, n) {
        return (x >>> n) | (x << (32 - n));
    }

    function sha256(bytes) {
        const K = new Uint32Array([
            0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
            0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
            0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
            0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
            0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
            0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
            0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
            0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
        ]);
        let h0 = 0x6a09e667, h1 = 0xbb67ae85, h2 = 0x3c6ef372, h3 = 0xa54ff53a;
        let h4 = 0x510e527f, h5 = 0x9b05688c, h6 = 0x1f83d9ab, h7 = 0x5be0cd19;
        const len = bytes.length;
        const bitLen = len * 8;
        const padded = new Uint8Array(Math.ceil((len + 9) / 64) * 64);
        padded.set(bytes);
        padded[len] = 0x80;
        const view = new DataView(padded.buffer);
        view.setUint32(padded.length - 4, bitLen >>> 0, false);
        view.setUint32(padded.length - 8, Math.floor(bitLen / 0x100000000), false);
        const w = new Uint32Array(64);
        for (let offset = 0; offset < padded.length; offset += 64) {
            for (let i = 0; i < 16; i++) w[i] = view.getUint32(offset + i * 4, false);
            for (let i = 16; i < 64; i++) {
                const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
                const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
                w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
            }
            let a = h0, b = h1, c = h2, d = h3, e = h4, f = h5, g = h6, h = h7;
            for (let i = 0; i < 64; i++) {
                const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
                const ch = (e & f) ^ (~e & g);
                const temp1 = (h + S1 + ch + K[i] + w[i]) | 0;
                const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
                const maj = (a & b) ^ (a & c) ^ (b & c);
                const temp2 = (S0 + maj) | 0;
                h = g; g = f; f = e; e = (d + temp1) | 0;
                d = c; c = b; b = a; a = (temp1 + temp2) | 0;
            }
            h0 = (h0 + a) | 0; h1 = (h1 + b) | 0; h2 = (h2 + c) | 0; h3 = (h3 + d) | 0;
            h4 = (h4 + e) | 0; h5 = (h5 + f) | 0; h6 = (h6 + g) | 0; h7 = (h7 + h) | 0;
        }
        const out = new Uint8Array(32);
        const outView = new DataView(out.buffer);
        [h0, h1, h2, h3, h4, h5, h6, h7].forEach((h, i) => outView.setUint32(i * 4, h >>> 0, false));
        return out;
    }

    async function keyStream(key) {
        const keyBytes = new TextEncoder().encode(key);
        const digest = sha256(keyBytes);
        return {
            permutation: digest,
            xor: new TextEncoder().encode(
                [...digest].map(byte => byte.toString(16).padStart(2, "0")).join("")
            ),
        };
    }

    function xor(cipher, hash) {
        return cipher.map((byte, index) => byte ^ hash[index % hash.length]);
    }

    function xorPixels(pixels, hash) {
        const output = new Uint8ClampedArray(pixels);
        for (let index = 0; index < output.length; index += 4) {
            output[index] ^= hash[index % hash.length];
            output[index + 1] ^= hash[(index + 1) % hash.length];
            output[index + 2] ^= hash[(index + 2) % hash.length];
        }
        return output;
    }

    function shuffledIndices(length, hash) {
        let state = ((hash[0] << 24) | (hash[1] << 16) | (hash[2] << 8) | hash[3]) >>> 0;
        const indices = [...Array(length).keys()];
        for (let index = length - 1; index > 0; index--) {
            state ^= state << 13;
            state ^= state >>> 17;
            state ^= state << 5;
            state >>>= 0;
            const swap = state % (index + 1);
            [indices[index], indices[swap]] = [indices[swap], indices[index]];
        }
        return indices;
    }

    async function decrypt(encoded, key) {
        return decoder.decode(xor(bytes(encoded), (await keyStream(key)).xor));
    }

    function display(text) {
        return text.replace(unsafe, char => glyphs[char.codePointAt(0) % glyphs.length]);
    }

    function showGarbage() {
        for (const node of cipherNodes) node.textContent = node.dataset.azGarbage;
    }

    async function restoreLinks(key) {
        for (const link of linkNodes) {
            const url = await decrypt(link.dataset.azHref, key);
            if (url.startsWith("az-link:")) link.href = url.slice(8);
        }
    }

    async function prepareImage(state) {
        if (state.canvas) return state;
        const { image } = state;
        await image.decode();
        state.canvas = document.createElement("canvas");
        state.width = image.naturalWidth;
        state.height = image.naturalHeight;
        state.canvas.width = state.width;
        state.canvas.height = state.height;
        state.canvas.className = image.className;
        state.canvas.style.cssText = image.style.cssText;
        state.canvas.style.width = `${image.clientWidth}px`;
        state.canvas.style.height = `${image.clientHeight}px`;
        for (const attribute of ["width", "height", "alt"]) {
            if (image.hasAttribute(attribute)) state.canvas.setAttribute(attribute, image.getAttribute(attribute));
        }
        state.context = state.canvas.getContext("2d");
        state.context.drawImage(image, 0, 0, state.width, state.height);
        state.locked = state.context.getImageData(0, 0, state.width, state.height);
        image.replaceWith(state.canvas);
        state.image = state.canvas;
        return state;
    }

    function revealImage(state, hash, permutationHash, currentAttempt) {
        return new Promise(resolve => {
            const { context, locked, width, height } = state;
            context.putImageData(locked, 0, 0);
            const unxored = xorPixels(locked.data, hash);
            const pixels = new ImageData(width, height);
            const order = shuffledIndices(width * height, permutationHash);
            for (let destination = 0; destination < order.length; destination++) {
                const source = order[destination];
                pixels.data.set(unxored.slice(destination * 4, destination * 4 + 4), source * 4);
            }
            const revealOrder = [...Array(width * height).keys()];
            for (let index = revealOrder.length - 1; index > 0; index--) {
                const swap = Math.floor(Math.random() * (index + 1));
                [revealOrder[index], revealOrder[swap]] = [revealOrder[swap], revealOrder[index]];
            }
            const visible = new ImageData(new Uint8ClampedArray(locked.data), width, height);
            let offset = 0;
            function frame() {
                if (currentAttempt !== attempt) return resolve();
                for (const pixel of revealOrder.slice(offset, offset + 5000)) {
                    visible.data.set(pixels.data.slice(pixel * 4, pixel * 4 + 4), pixel * 4);
                }
                context.putImageData(visible, 0, 0);
                offset += 5000;
                if (offset < order.length) {
                    requestAnimationFrame(frame);
                } else {
                    resolve();
                }
            }
            requestAnimationFrame(frame);
        });
    }

    function revealText(node, text, currentAttempt) {
        return new Promise(resolve => {
            const characters = [...text];
            function step(characterIndex) {
                if (currentAttempt !== attempt) return resolve();
                const visible = [...node.textContent];
                visible[characterIndex] = characters[characterIndex];
                const cursor = document.createElement("span");
                cursor.className = "az-cursor";
                cursor.textContent = visible[characterIndex];
                node.replaceChildren(
                    document.createTextNode(visible.slice(0, characterIndex).join("")),
                    cursor,
                    document.createTextNode(visible.slice(characterIndex + 1).join("")),
                );
                setTimeout(() => {
                    if (characterIndex + 1 === characters.length) {
                        node.textContent = visible.join("");
                        resolve();
                    } else {
                        step(characterIndex + 1);
                    }
                }, 15);
            }
            step(0);
        });
    }

    // Interleaves text and image reveals in the order they appear in the
    // document, so e.g. text -> image -> text in the HTML decodes in that
    // same order instead of every text and every image running at once.
    function documentOrder(a, b) {
        const position = a.compareDocumentPosition(b);
        return position & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1;
    }

    async function revealAll(texts, stream, currentAttempt) {
        const items = [
            ...cipherNodes.map((node, index) => ({ node, text: texts[index] })),
            ...imageNodes.map(entry => ({ image: entry })),
        ].sort((a, b) => documentOrder(a.node || a.image.image, b.node || b.image.image));
        for (const item of items) {
            if (currentAttempt !== attempt) return;
            if (item.node) {
                await revealText(item.node, item.text, currentAttempt);
            } else {
                await revealImage(await prepareImage(item.image), stream.xor, stream.permutation, currentAttempt);
            }
        }
    }

    async function unlock() {
        document.getElementById("az-unlock-button").blur();
        const key = composedKey();
        if (!key || keyInputs.some(input => !normalize(input.value))) return showGarbage();
        const currentAttempt = ++attempt;
        showGarbage();
        try {
            const [texts, stream] = await Promise.all([
                Promise.all(cipherNodes.map(node => decrypt(node.dataset.azCipher, key))),
                keyStream(key),
            ]);
            await restoreLinks(key);
            setTimeout(() => {
                revealAll(texts.map(display), stream, currentAttempt);
            }, 350);
        } catch (error) {
            console.error("az unlock failed:", error);
        }
    }

    document.getElementById("az-unlock-button").addEventListener("click", unlock);
    for (const input of keyInputs) {
        input.addEventListener("keydown", event => {
            if (event.key === "Enter") unlock();
        });
    }
    showGarbage();

    sketchFields();
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(sketchFields);
    let sketchResizeTimer;
    window.addEventListener("resize", () => {
        clearTimeout(sketchResizeTimer);
        sketchResizeTimer = setTimeout(sketchFields, 150);
    });
});
</script>
"""


class TextEncryptor(HTMLParser):
    """Copy HTML while replacing visible text nodes with encrypted spans.

    Anything inside an element marked `data-az-plain` is copied through
    untouched, so a page can ask its question in the open and keep only the
    answer encrypted. An element marked `data-az-unlock` is replaced by the
    unlock box, which otherwise lands at the top of the body.
    """

    def __init__(self, key: bytes, images: dict[str, tuple[str, str, str]]):
        super().__init__(convert_charrefs=True)
        self.key = key
        self.images = images
        self.parts: list[str] = []
        self.open_tags: list[Frame] = []
        self.plain_depth = 0
        self.unlock_slots = 0

    @property
    def plain(self) -> bool:
        return self.plain_depth > 0

    def _render(self, tag: str, attrs) -> str:
        """Rewrite the one attribute that would otherwise leak, then serialize."""
        lookup = dict(attrs)
        replacement = None
        if tag == "img" and (source := lookup.get("src")) in self.images:
            preview, encrypted, mime_type = self.images[source]
            replacement = ("src", [
                ("src", preview),
                ("data-az-image", encrypted),
                ("data-az-type", mime_type),
            ])
        elif tag == "a" and (href := lookup.get("href")) is not None:
            replacement = ("href", [("data-az-href", encrypt(LINK_MAGIC + href, self.key))])
        if replacement is None:
            return self.get_starttag_text()
        dropped, added = replacement
        safe_attrs = [(name, value) for name, value in attrs if name != dropped] + added
        rendered = " ".join(
            name if value is None else f'{name}="{escape(value, quote=True)}"'
            for name, value in safe_attrs
        )
        return f"<{tag} {rendered}>"

    def handle_starttag(self, tag: str, attrs) -> None:
        lookup = dict(attrs)
        swallow = UNLOCK_ATTR in lookup
        if swallow:
            self.parts.append(UNLOCK_TOKEN)
            self.unlock_slots += 1
        else:
            self.parts.append(
                self.get_starttag_text() if self.plain else self._render(tag, attrs)
            )
        opens_plain = PLAIN_ATTR in lookup and not swallow
        if opens_plain:
            self.plain_depth += 1
        if tag not in VOID_TAGS:
            self.open_tags.append(Frame(tag, opens_plain, swallow))

    def handle_startendtag(self, tag: str, attrs) -> None:
        lookup = dict(attrs)
        if UNLOCK_ATTR in lookup:
            self.parts.append(UNLOCK_TOKEN)
            self.unlock_slots += 1
            return
        self.parts.append(self.get_starttag_text() if self.plain else self._render(tag, attrs))

    def handle_endtag(self, tag: str) -> None:
        frame = self.open_tags.pop() if self.open_tags and self.open_tags[-1].tag == tag else None
        if frame is None or not frame.swallowed:
            self.parts.append(f"</{tag}>")
        if frame is not None and frame.opened_plain:
            self.plain_depth -= 1

    def handle_data(self, data: str) -> None:
        skip = self.plain or not data.strip() or any(
            frame.tag in SKIP_TEXT_TAGS for frame in self.open_tags
        )
        if skip:
            self.parts.append(data)
            return
        cipher = encrypt(data, self.key)
        self.parts.append(
            f'<span data-az-cipher="{cipher}" data-az-garbage="{garbage(data, self.key)}"></span>'
        )

    def handle_comment(self, data: str) -> None:
        self.parts.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"<!{decl}>")


def fold(answer: str) -> str:
    """Fold an authored answer the way the page folds what a player types."""
    return "".join(
        character for character in answer.upper() if character.isascii() and character.isalnum()
    )


def unlock_fields(secret: dict) -> tuple[bytes, str]:
    """Return the page key and the markup for the inputs that compose it.

    A puzzle either has one `key`, or an `answers` list whose folded values are
    concatenated -- that is how a page can ask two separate questions (the post
    count and the animal) and still decrypt against a single stream. `prompts`
    labels the boxes for the player.
    """
    answers = secret.get("answers") or [secret["key"]]
    prompts = secret.get("prompts") or [None] * len(answers)
    if len(prompts) != len(answers):
        raise ValueError("a puzzle needs one prompt per answer, or none at all")

    folded = [fold(str(answer)) for answer in answers]
    if not all(folded):
        raise ValueError("every puzzle answer needs at least one letter or digit")

    rows = []
    for index, prompt in enumerate(prompts):
        label = f'<span class="az-prompt">{escape(prompt)}</span>' if prompt else ""
        rows.append(
            f'    {label}<label class="az-field">'
            f'<input data-az-field data-az-seed="{index}" autocomplete="off" spellcheck="false">'
            f"</label>"
        )
    return "".join(folded).encode("ascii"), "\n".join(rows)


def encrypt(text: str, key: bytes) -> str:
    """Return UTF-8 text XORed with a repeating ASCII key as base64."""
    payload = text.encode("utf-8")
    cipher = xor(payload, key)
    return base64.b64encode(cipher).decode("ascii")


def xor(data: bytes, key: bytes) -> bytes:
    """XOR data against the ASCII hexadecimal SHA-256 digest of a key."""
    key_hash = hashlib.sha256(key).hexdigest().encode("ascii")
    return bytes(byte ^ key_hash[index % len(key_hash)] for index, byte in enumerate(data))


def shuffled_indices(length: int, key: bytes) -> list[int]:
    """Return a key-seeded Fisher-Yates permutation of pixel positions."""
    mask = (1 << 32) - 1
    state = int.from_bytes(hashlib.sha256(key).digest()[:4], "big")
    indices = list(range(length))
    for index in range(length - 1, 0, -1):
        state ^= (state << 13) & mask
        state ^= state >> 17
        state ^= (state << 5) & mask
        state &= mask
        swap = state % (index + 1)
        indices[index], indices[swap] = indices[swap], indices[index]
    return indices


def scrambled_pixels(pixels: bytes, key: bytes) -> bytes:
    """Shuffle RGBA pixels and XOR their channels for a non-recognizable preview."""
    pixel_count = len(pixels) // 4
    output = bytearray(len(pixels))
    for destination, source in enumerate(shuffled_indices(pixel_count, key)):
        output[destination * 4:(destination + 1) * 4] = pixels[source * 4:(source + 1) * 4]
    key_hash = hashlib.sha256(key).hexdigest().encode("ascii")
    for offset in range(0, len(output), 4):
        output[offset] ^= key_hash[offset % len(key_hash)]
        output[offset + 1] ^= key_hash[(offset + 1) % len(key_hash)]
        output[offset + 2] ^= key_hash[(offset + 2) % len(key_hash)]
    return bytes(output)


def garbage(text: str, key: bytes) -> str:
    """Return printable garbage while retaining the text node's whitespace."""
    cipher = base64.b64decode(encrypt(text, key))
    key_hash = hashlib.sha256(key).hexdigest().encode("ascii")
    output = []
    offset = 0
    for character in text:
        width = len(character.encode("utf-8"))
        if character.isspace():
            output.append(character)
        else:
            byte = cipher[offset] ^ key_hash[offset % len(key_hash)]
            output.append(GARBAGE_GLYPHS[byte % len(GARBAGE_GLYPHS)])
        offset += width
    return "".join(output)


def encrypt_page(
    page: str, key: bytes, images: dict[str, tuple[str, str, str]], fields: str
) -> str:
    """Preserve the document and replace visible body text with encrypted spans."""
    body_start = page.lower().find("<body")
    body_end = page.lower().rfind("</body>")
    if body_start < 0 or body_end < 0:
        raise ValueError("puzzle page needs <body> and </body> tags")
    body_open_end = page.find(">", body_start) + 1
    if body_open_end == 0:
        raise ValueError("puzzle page has an incomplete <body> tag")

    encryptor = TextEncryptor(key, images)
    encryptor.feed(page[body_open_end:body_end])
    encryptor.close()
    if encryptor.unlock_slots > 1:
        raise ValueError("a puzzle page can hold at most one data-az-unlock slot")

    runtime = RUNTIME.replace(FIELDS_TOKEN, fields)
    body = "".join(encryptor.parts)
    # The box sits wherever the page marked it, and at the top when it didn't.
    body = body.replace(UNLOCK_TOKEN, runtime) if encryptor.unlock_slots else runtime + body
    return page[:body_open_end] + body + page[body_end:]


def encrypt_images(source_dir: Path, output_dir: Path, key: bytes) -> dict[str, tuple[str, str, str]]:
    """Write noisy previews and encrypted originals, then remove public originals."""
    images = {}
    for source in source_dir.rglob("*"):
        if not source.is_file() or source.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        relative = source.relative_to(source_dir)
        output = output_dir / relative
        preview = output.with_name(f"{output.name}.az.png")
        encrypted = output.with_name(f"{output.name}.az")
        with Image.open(source) as image:
            rgba = image.convert("RGBA")
            scrambled = Image.frombytes("RGBA", rgba.size, scrambled_pixels(rgba.tobytes(), key))
            scrambled.save(preview)
        encrypted.write_bytes(xor(IMAGE_MAGIC + source.read_bytes(), key))
        output.unlink(missing_ok=True)
        mime_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        images[relative.as_posix()] = (
            preview.relative_to(output_dir).as_posix(),
            encrypted.relative_to(output_dir).as_posix(),
            mime_type,
        )
    return images


def build(source_dir: Path, output_dir: Path, site) -> None:
    """Encrypt every direct child puzzle directory that contains a secret file."""
    for puzzle_dir in sorted(source_dir.iterdir()):
        if not puzzle_dir.is_dir():
            continue
        secret_path = puzzle_dir / SECRET_FILE
        if not secret_path.exists():
            continue

        secret = json.loads(secret_path.read_text(encoding="utf-8"))
        try:
            key, fields = unlock_fields(secret)
        except (KeyError, ValueError) as error:
            raise ValueError(f"{secret_path}: {error}") from error

        page_path = output_dir / puzzle_dir.name / PAGE_FILE
        if not page_path.exists():
            raise FileNotFoundError(f"missing puzzle page {page_path}")
        images = encrypt_images(puzzle_dir, page_path.parent, key)
        page_path.write_text(
            encrypt_page(page_path.read_text(encoding="utf-8"), key, images, fields),
            encoding="utf-8",
        )
        site.logger.info(f"Encrypted puzzle page {puzzle_dir.name}")
