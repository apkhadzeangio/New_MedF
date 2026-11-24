/* Staging page: mark artery segments, and let the server recompute GLASS / WIfI / PLAN on every change
 * (the tables live only in staging.py). */
(function () {
    'use strict';
    var form = document.getElementById('st-form');
    if (!form) { return; }
    var segments = {};
    try { segments = JSON.parse(document.getElementById('st-segments').textContent) || {}; } catch (e) { segments = {}; }
    var hidden = form.querySelector('input[name=segments]');
    var tool = { state: 'oc', flag: null };

    function sync() { hidden.value = JSON.stringify(segments); }

    function paint(g) {
        var key = g.getAttribute('data-seg');
        var item = segments[key] || {};
        var css = ['sg', 'sg-' + (item.s || 'n')];
        ['ca', 'rx', 'by'].forEach(function (f) { if (item[f]) { css.push('sg-' + f); } });
        g.setAttribute('class', css.join(' '));
    }

    form.querySelectorAll('.st-tool').forEach(function (b) {
        b.addEventListener('click', function () {
            form.querySelectorAll('.st-tool').forEach(function (x) { x.classList.remove('on'); });
            b.classList.add('on');
            tool = { state: b.getAttribute('data-state'), flag: b.getAttribute('data-flag') };
        });
    });

    function apply(g) {
        var key = g.getAttribute('data-seg');
        var item = segments[key] || {};
        if (tool.flag) {
            item[tool.flag] = !item[tool.flag];
            if (!item[tool.flag]) { delete item[tool.flag]; }
        } else if (tool.state) {
            if (item.s === tool.state || tool.state === 'n') { delete item.s; } else { item.s = tool.state; }
        }
        if (Object.keys(item).length) { segments[key] = item; } else { delete segments[key]; }
        paint(g);
        sync();
        preview();
    }

    form.querySelectorAll('.am-edit [data-seg]').forEach(function (g) {
        g.addEventListener('click', function () { apply(g); });
        g.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); apply(g); }
        });
    });

    var timer = null, seq = 0;
    function preview() {
        clearTimeout(timer);
        timer = setTimeout(function () {
            sync();
            var data = new FormData(form);
            data.append('preview', '1');
            var mine = ++seq;
            fetch(form.getAttribute('data-preview'), { method: 'POST', body: data, credentials: 'same-origin',
                headers: { 'X-Requested-With': 'XMLHttpRequest' } })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (r) {
                    if (!r || mine !== seq) { return; }
                    var old = document.getElementById('st-res');
                    var box = document.createElement('div');
                    box.innerHTML = r.html;
                    old.replaceWith(box.firstElementChild);
                    live();
                })
                .catch(function () {});
        }, 180);
    }

    function live() {
        var short = document.querySelector('#st-res .st-short');
        var out = document.getElementById('st-live');
        if (short && out) { out.textContent = short.textContent.replace(/\s+/g, ' ').trim(); }
    }
    live();

    form.addEventListener('change', preview);
    form.addEventListener('input', function (e) { if (e.target.tagName === 'INPUT' && e.target.type !== 'radio') { preview(); } });
    form.addEventListener('submit', sync);
    sync();
}());
