/* The chat thread's rows, kept by Preact (issue #233, step 2; ADR 0010).
 *
 * Until this, every 4-second poll emptied the thread and rebuilt every row,
 * so anything he had open -- a steps drawer, a menu, a selection -- closed
 * under his thumb. `app.js` still builds each row's DOM the way it always
 * has; what moved here is deciding which rows to put on screen. Preact keys
 * them, and a row whose `sig` did not change keeps the node already there,
 * so the only DOM change for one new message is that message.
 *
 * Rows are `{node, key, sig}`. A `sig` that is NaN never equals itself, so a
 * row that must always redraw (the loader, the lost-turn card) passes NaN.
 *
 * Other painters still write straight into the container -- the send
 * bubble, the error line, the loading line -- so anything in it that is not
 * this file's root is removed before each render, exactly as the old
 * `textContent = ""` did, and a root someone else emptied out is remounted
 * rather than diffed against nodes that are no longer on the page.
 */
(function () {
  "use strict";
  var P = window.htmPreact;
  if (!P) return;

  function Row(props) { P.Component.call(this, props); }
  Row.prototype = Object.create(P.Component.prototype);
  Row.prototype.constructor = Row;
  Row.prototype.shouldComponentUpdate = function (next) {
    return next.sig !== this.props.sig;
  };
  Row.prototype.render = function () {
    return P.h("div", { class: "thread-slot" });
  };
  function put(slot, node) {
    if (slot.firstChild === node && slot.childNodes.length === 1) return;
    slot.textContent = "";
    slot.appendChild(node);
  }
  Row.prototype.componentDidMount = function () { put(this.base, this.props.node); };
  Row.prototype.componentDidUpdate = function () { put(this.base, this.props.node); };

  function render(container, rows) {
    var root = container.novaThreadRoot;
    if (!root || root.parentNode !== container) {
      root = document.createElement("div");
      root.className = "thread-slot";
      container.novaThreadRoot = root;
    }
    Array.prototype.slice.call(container.childNodes).forEach(function (n) {
      if (n !== root) container.removeChild(n);
    });
    if (root.parentNode !== container) container.appendChild(root);
    // Two rows can share a key (a step row and its answer stamped the same
    // second), and Preact mismatches rows on a repeated key, so repeats get
    // a count on the end.
    var seen = {};
    P.render(rows.map(function (r) {
      var key = String(r.key);
      seen[key] = (seen[key] || 0) + 1;
      if (seen[key] > 1) key += "#" + seen[key];
      return P.h(Row, { key: key, sig: r.sig, node: r.node });
    }), root);
  }

  window.novaThread = { render: render };
})();
