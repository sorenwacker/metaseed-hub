/* What `hx-on:` attributes used to do, without eval.
 *
 * htmx compiles an `hx-on:` value with `new Function`, so under the production
 * Content-Security-Policy (script-src without 'unsafe-eval') the handler throws
 * `EvalError` and never runs. The request itself succeeds; what fails is the
 * code meant to run afterwards -- which is why "Add Entity" posted the entity
 * and then appeared to do nothing at all.
 *
 * The same behaviour is expressed as data attributes handled here, from one
 * delegated listener on document. Nothing is evaluated, so the policy can stay
 * strict.
 *
 *   data-after-request-reset      form.reset() once the request succeeds
 *   data-after-request-call       call a global function with the event
 *   data-after-request-redirect   go to this URL once the request succeeds
 *   data-working-target           element to hold a "working" notice on send
 *   data-working-message          the notice's text
 */
document.addEventListener('htmx:beforeRequest', function (event) {
    var el = event.detail.elt;
    if (!el || !el.dataset) return;

    // A "working" notice while a slow request runs, so the page does not look
    // dead and the button is not pressed again.
    var targetId = el.dataset.workingTarget;
    var message = el.dataset.workingMessage;
    if (targetId && message) {
        var target = document.getElementById(targetId);
        if (target) {
            var notice = document.createElement('div');
            notice.className = 'alert alert-info';
            notice.dataset.testid = 'seek-working';
            // textContent, not innerHTML: the message is author-written today,
            // and this way it cannot become an injection point if that changes.
            notice.textContent = message;
            target.replaceChildren(notice);
        }
    }
});

document.addEventListener('htmx:afterRequest', function (event) {
    var el = event.detail.elt;
    if (!el || !el.dataset) return;

    // Only on success: a failed request must not clear what the person typed,
    // nor navigate away from the form still holding it.
    var ok = event.detail.successful;

    if (ok && 'afterRequestReset' in el.dataset && typeof el.reset === 'function') {
        el.reset();
    }

    var fn = el.dataset.afterRequestCall;
    if (fn && typeof window[fn] === 'function') {
        window[fn](event);
    }

    var url = el.dataset.afterRequestRedirect;
    if (ok && url) {
        window.location.href = url;
    }
});
