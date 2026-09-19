// Select all: a header checkbox with data-select-all="<name>" checks or clears every checkbox of that name in its form,
// and shows whether some or all of them are checked. Pages work without it; served from the console itself, so the
// Content-Security-Policy allows only this origin's scripts ("script-src 'self'"), never inline ones.
"use strict";

function boxesOf(head) {
  var name = head.getAttribute("data-select-all");
  return head.form ? Array.prototype.slice.call(head.form.querySelectorAll('input[type="checkbox"][name="' + name + '"]')) : [];
}

function refresh(head) {
  var boxes = boxesOf(head);
  var on = boxes.filter(function (box) { return box.checked; }).length;
  head.checked = boxes.length > 0 && on === boxes.length;
  head.indeterminate = on > 0 && on < boxes.length;
}

document.addEventListener("change", function (event) {
  var box = event.target;
  if (!(box instanceof HTMLInputElement) || box.type !== "checkbox" || !box.form) {
    return;
  }
  if (box.hasAttribute("data-select-all")) {
    boxesOf(box).forEach(function (other) { other.checked = box.checked; });
    return;
  }
  box.form.querySelectorAll('input[data-select-all="' + box.name + '"]').forEach(refresh);
});

document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll("input[data-select-all]").forEach(function (head) {
    head.hidden = boxesOf(head).length === 0;
    refresh(head);
  });
});
