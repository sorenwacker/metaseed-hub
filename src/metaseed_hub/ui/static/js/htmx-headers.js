/* Headers every htmx request carries.
 *
 * These were an `hx-headers` attribute on <body>. htmx evaluates such a value
 * with `new Function`, so the production Content-Security-Policy -- which
 * allows 'self' and 'unsafe-inline' for scripts but not 'unsafe-eval' --
 * rejected it, and every request raised:
 *
 *     Uncaught EvalError: Evaluating a string as JavaScript violates the
 *     following Content Security Policy directive
 *
 * Forms then did nothing at all. Development serves no CSP, so it only ever
 * failed on the deployed site.
 *
 * Setting the headers in htmx:configRequest needs no eval, so the policy can
 * stay strict.
 */
document.addEventListener('htmx:configRequest', function (event) {
    event.detail.headers['X-Requested-With'] = 'XMLHttpRequest';

    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.content) {
        event.detail.headers['X-CSRF-Token'] = meta.content;
    }
});
