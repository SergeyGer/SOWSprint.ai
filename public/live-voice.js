/*
 * Voice capture state, mirrored into the composer.
 *
 * The server renders one panel with `data-state` of `idle`, `recording` or `review`, and
 * this script is the other half of that contract:
 *
 *   recording — the words arriving are mirrored into the message box as they are spoken
 *   review    — the finished transcript is placed in the box and left there for checking
 *   anything else — the box is left alone, and a previous transcript is not restored
 *
 * That last line is the fix for a real bug. The panel used to be identified by a fixed id
 * and every recording left its panel in the transcript, so `getElementById` returned the
 * OLDEST one: the first brief ever spoken. Every later recording re-displayed it, and the
 * speaker saw words they had not just said. Only the live panel is read now, and only while
 * it says it is live.
 *
 * Chainlit has no server-to-client API for the composer — `window_message` is
 * client-to-server, the socket handlers expose nothing the other way, and the frontend is a
 * compiled bundle in the wheel. The server can render HTML, and `unsafe_allow_html` is
 * already on for the cost dashboard, so the text travels in a data attribute and lands here.
 *
 * The textarea is a controlled React input: assigning `.value` leaves React's state stale
 * and the next keystroke restores the old text. The native setter is called instead, then an
 * `input` event is dispatched, which is what React listens for.
 */
(function () {
  "use strict";

  const PANEL_ID = "sowsprint-voice";
  const INPUT_ID = "chat-input";
  const BODY_FLAG = "sowsprint-recording";

  let appliedFor = null;

  function panel() {
    return document.getElementById(PANEL_ID);
  }

  function setComposer(text) {
    const input = document.getElementById(INPUT_ID);
    if (!input || input.value === text) return;

    const descriptor = Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype,
      "value"
    );
    if (descriptor && descriptor.set) {
      descriptor.set.call(input, text);
    } else {
      input.value = text;
    }
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.scrollTop = input.scrollHeight;
  }

  function indicator(state) {
    // A class on <body> drives the global banner in custom.css, so the state is visible even
    // when the panel has scrolled out of view — which on a phone it usually has.
    const banner = document.getElementById("sowsprint-voice-banner");
    if (state === "recording") {
      document.body.classList.add(BODY_FLAG);
      if (!banner) {
        const el = document.createElement("div");
        el.id = "sowsprint-voice-banner";
        el.setAttribute("role", "status");
        el.setAttribute("aria-live", "polite");
        el.innerHTML = '<span class="voice-dot"></span> Recording — tap the microphone to stop';
        document.body.appendChild(el);
      }
    } else {
      document.body.classList.remove(BODY_FLAG);
      if (banner) banner.remove();
    }
  }

  function sync() {
    const el = panel();

    if (!el) {
      indicator(null);
      // The panel is gone: the recording was sent or discarded. Drop the text the field was
      // carrying, but only if this script put it there — never wipe something typed by hand.
      if (appliedFor !== null) {
        appliedFor = null;
        const input = document.getElementById(INPUT_ID);
        if (input && input.value !== "") setComposer("");
      }
      return;
    }

    const state = el.getAttribute("data-state") || "";
    indicator(state);
    if (state !== "recording" && state !== "review") return;

    const text = el.getAttribute("data-text") || "";
    if (text === appliedFor) return;
    appliedFor = text;
    setComposer(text);
  }

  const observer = new MutationObserver(sync);
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["data-text", "data-state"],
  });
  setInterval(sync, 400);
})();
