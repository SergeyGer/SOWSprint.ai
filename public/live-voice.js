/*
 * Mirror the live voice transcript into the composer.
 *
 * Chainlit has no server-to-client API for setting the input's text: `window_message` is
 * client-to-server only, and the socket handlers expose nothing in the other direction. The
 * frontend is a compiled bundle in the wheel, so there is nothing on the client to call
 * either. What the server *can* do is render HTML, and `unsafe_allow_html` is already on for
 * the cost dashboard — so the transcript is delivered as a hidden element's data attribute
 * and moved into the textarea here.
 *
 * The textarea is `#chat-input`. It is a controlled React input, so assigning `.value`
 * alone leaves React's own state stale and the next keystroke or re-render restores the old
 * text. The native setter is used instead, then an `input` event is dispatched, which is what
 * React listens for. That is the difference between text that appears and text that stays.
 *
 * While recording, the field is treated as read-only for the speaker: their own words are the
 * content, and typing over a transcription that is still arriving is a losing race.
 */
(function () {
  "use strict";

  const MARKER_ID = "sowsprint-live-transcript";
  const INPUT_ID = "chat-input";

  function setComposer(text) {
    const input = document.getElementById(INPUT_ID);
    if (!input) return;
    if (input.value === text) return;

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

  function currentTranscript() {
    const marker = document.getElementById(MARKER_ID);
    if (!marker) return null;
    return marker.getAttribute("data-text") || "";
  }

  let lastSeen = null;

  function sync() {
    const text = currentTranscript();
    if (text === null || text === lastSeen) return;
    lastSeen = text;
    setComposer(text);
  }

  // The transcript element is re-rendered as the message updates, so the observer watches
  // the whole document rather than the node it first saw.
  const observer = new MutationObserver(sync);
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["data-text"],
  });
  setInterval(sync, 500);
})();
