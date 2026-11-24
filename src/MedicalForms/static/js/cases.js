/* Cases (conference): photo upload from the phone, photo viewer, presentation mode.
 * Plain JavaScript (the presentation page has no jQuery). */
(function () {
    'use strict';

    function csrf() {
        var el = document.querySelector('input[name=csrfmiddlewaretoken]');
        return el ? el.value : '';
    }

    function store(key, value) {
        try {
            if (value === undefined) { return window.localStorage.getItem(key); }
            window.localStorage.setItem(key, value);
        } catch (e) { /* private mode: fine */ }
        return null;
    }

    /* ------------------------------------------------------------------
     * Upload: the phone shrinks the photo first (2000 px, ~0.5 MB), so even
     * a slow mobile network uploads quickly; then one request per photo.
     * ------------------------------------------------------------------ */
    var MAX_SIDE = 2000;

    function shrink(file) {
        if (!window.createImageBitmap || !document.createElement('canvas').toBlob) {
            return Promise.resolve(file);
        }
        return createImageBitmap(file, { imageOrientation: 'from-image' })
            .catch(function () { return createImageBitmap(file); })
            .then(function (bmp) {
                var scale = Math.min(1, MAX_SIDE / Math.max(bmp.width, bmp.height));
                if (scale === 1 && file.type === 'image/jpeg' && file.size < 1500000) { return file; }
                var canvas = document.createElement('canvas');
                canvas.width = Math.round(bmp.width * scale);
                canvas.height = Math.round(bmp.height * scale);
                canvas.getContext('2d').drawImage(bmp, 0, 0, canvas.width, canvas.height);
                if (bmp.close) { bmp.close(); }
                return new Promise(function (resolve) {
                    canvas.toBlob(function (blob) {
                        resolve(blob ? new File([blob], 'photo.jpg', { type: 'image/jpeg' }) : file);
                    }, 'image/jpeg', 0.88);
                });
            })
            .catch(function () { return file; });   /* the server copes with the original */
    }

    function send(form, file, row) {
        return new Promise(function (resolve) {
            var data = new FormData();
            data.append('csrfmiddlewaretoken', csrf());
            data.append('kind', (form.querySelector('input[name=kind]:checked') || {}).value || 'wound');
            data.append('taken_on', form.querySelector('input[name=taken_on]').value);
            data.append('photo', file, file.name || 'photo.jpg');
            var xhr = new XMLHttpRequest();
            xhr.open('POST', form.action);
            xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
            xhr.upload.onprogress = function (e) {
                if (e.lengthComputable) { row.querySelector('i').style.width = Math.round(100 * e.loaded / e.total) + '%'; }
            };
            xhr.onload = function () {
                var ok = false, msg = 'ვერ აიტვირთა';
                try {
                    var r = JSON.parse(xhr.responseText);
                    ok = r.added > 0;
                    if (r.errors && r.errors.length) { msg = r.errors.join(' '); }
                } catch (e) { /* not JSON */ }
                if (xhr.status === 403) { msg = 'სესია ამოიწურა — გადატვირთეთ გვერდი'; }
                row.classList.add(ok ? 'ok' : 'bad');
                row.querySelector('em').textContent = ok ? 'შენახულია' : msg;
                resolve(ok);
            };
            xhr.onerror = function () {
                row.classList.add('bad');
                row.querySelector('em').textContent = 'კავშირი გაწყდა — სცადეთ ხელახლა';
                resolve(false);
            };
            xhr.send(data);
        });
    }

    function initUpload() {
        var form = document.getElementById('cs-upload');
        var box = document.getElementById('cs-progress');
        if (!form || !box) { return; }
        form.querySelectorAll('input[type=file]').forEach(function (input) {
            input.addEventListener('change', function () {
                var files = Array.prototype.slice.call(input.files || []);
                input.value = '';
                if (!files.length) { return; }
                box.hidden = false;
                box.innerHTML = '';
                var rows = files.map(function (f, i) {
                    var row = document.createElement('div');
                    row.className = 'cs-up';
                    row.innerHTML = '<span></span><b><i></i></b><em>მზადდება…</em>';
                    row.querySelector('span').textContent = 'ფოტო ' + (i + 1);
                    box.appendChild(row);
                    return row;
                });
                var added = 0;
                files.reduce(function (chain, f, i) {
                    return chain.then(function () {
                        rows[i].querySelector('em').textContent = 'იტვირთება…';
                        return shrink(f).then(function (small) { return send(form, small, rows[i]); })
                            .then(function (ok) { if (ok) { added += 1; } });
                    });
                }, Promise.resolve()).then(function () {
                    if (added) {
                        var kind = (form.querySelector('input[name=kind]:checked') || {}).value || '';
                        window.location.href = window.location.pathname + '?kind=' + encodeURIComponent(kind) + '#photos';
                    }
                });
            });
        });
    }

    /* ------------------------------------------------------------------
     * Photo viewer on the case page (caption, type, date, delete).
     * ------------------------------------------------------------------ */
    function initViewer() {
        var dialog = document.getElementById('cs-viewer');
        var thumbs = Array.prototype.slice.call(document.querySelectorAll('.cs-thumb'));
        if (!dialog || !thumbs.length || !dialog.showModal) { return; }
        var img = dialog.querySelector('img');
        var form = dialog.querySelector('form');
        var at = 0;

        function open(i) {
            at = (i + thumbs.length) % thumbs.length;
            var t = thumbs[at];
            img.src = t.dataset.full;
            form.action = t.dataset.edit;
            form.caption.value = t.dataset.caption || '';
            form.kind.value = t.dataset.kind;
            form.taken_on.value = t.dataset.date;
            if (!dialog.open) { dialog.showModal(); }
        }

        thumbs.forEach(function (t, i) { t.addEventListener('click', function () { open(i); }); });
        dialog.querySelector('.prev').addEventListener('click', function () { open(at - 1); });
        dialog.querySelector('.next').addEventListener('click', function () { open(at + 1); });
        dialog.querySelector('.cs-close').addEventListener('click', function () { dialog.close(); });
        dialog.addEventListener('click', function (e) { if (e.target === dialog) { dialog.close(); } });
        dialog.addEventListener('keydown', function (e) {
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') { return; }
            if (e.key === 'ArrowLeft') { open(at - 1); }
            if (e.key === 'ArrowRight') { open(at + 1); }
        });
        dialog.addEventListener('close', function () { img.removeAttribute('src'); });
        form.addEventListener('submit', function (e) {
            var question = e.submitter && e.submitter.dataset.confirm;
            if (question && !window.confirm(question)) { e.preventDefault(); }
        });
    }

    /* ------------------------------------------------------------------
     * Presentation: ← → case, ↑ ↓ photo, F full screen, A initials, D decision.
     * A presentation clicker (PageUp/PageDown) works too.
     * ------------------------------------------------------------------ */
    function initPresent() {
        if (!document.body.classList.contains('cs-present')) { return; }
        var slides = Array.prototype.slice.call(document.querySelectorAll('.pz-slide'));
        var counter = document.getElementById('pz-i');
        var cur = 0;

        function load(slide) {
            if (!slide) { return; }
            slide.querySelectorAll('img[data-src]').forEach(function (im) {
                im.src = im.dataset.src;
                im.removeAttribute('data-src');
            });
        }

        function showPhoto(slide, i) {
            var buttons = slide.querySelectorAll('.pz-strip button');
            if (!buttons.length) { return; }
            var n = (i + buttons.length) % buttons.length;
            buttons.forEach(function (b, k) { b.classList.toggle('on', k === n); });
            var stage = slide.querySelector('.pz-stage');
            stage.querySelector('img').src = buttons[n].dataset.full;
            stage.querySelector('figcaption').textContent = buttons[n].dataset.caption;
            buttons[n].scrollIntoView({ block: 'nearest', inline: 'nearest' });
            if (buttons[n + 1]) { (new Image()).src = buttons[n + 1].dataset.full; }
        }

        function currentPhoto(slide) {
            var buttons = Array.prototype.slice.call(slide.querySelectorAll('.pz-strip button'));
            return buttons.findIndex(function (b) { return b.classList.contains('on'); });
        }

        function show(i) {
            cur = Math.max(0, Math.min(slides.length - 1, i));
            slides.forEach(function (s, k) { s.hidden = k !== cur; });
            load(slides[cur]);
            load(slides[cur + 1]);
            if (slides[cur].querySelector('.pz-strip')) { showPhoto(slides[cur], Math.max(0, currentPhoto(slides[cur]))); }
            counter.textContent = slides[cur].dataset.case || (cur + 1);
            if (history.replaceState) { history.replaceState(null, '', '#' + (cur + 1)); }
            window.scrollTo(0, 0);
        }

        slides.forEach(function (slide) {
            slide.querySelectorAll('.pz-strip button').forEach(function (b, k) {
                b.addEventListener('click', function () { showPhoto(slide, k); });
            });
            var stage = slide.querySelector('.pz-stage');
            if (stage) { stage.addEventListener('click', function () { stage.classList.toggle('zoom'); }); }

            var decide = slide.querySelector('.pz-decide');
            if (!decide) { return; }
            var form = decide.querySelector('form');
            var toggle = decide.querySelector('.pz-decide-toggle');
            toggle.addEventListener('click', function () {
                form.hidden = !form.hidden;
                if (!form.hidden) { form.decision.focus(); }
            });
            decide.querySelector('.pz-cancel').addEventListener('click', function () { form.hidden = true; });
            form.addEventListener('submit', function (e) {
                e.preventDefault();
                var saved = form.querySelector('.pz-saved');
                saved.textContent = 'ინახება…';
                var data = new FormData();
                data.append('csrfmiddlewaretoken', csrf());
                data.append('decision', form.decision.value);
                fetch(slide.dataset.decision, { method: 'POST', body: data, credentials: 'same-origin',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' } })
                    .then(function (r) { if (!r.ok) { throw new Error(r.status); } return r.json(); })
                    .then(function () {
                        var text = form.decision.value.trim();
                        document.querySelectorAll('.pz-slide[data-pk="' + slide.dataset.pk + '"]').forEach(function (peer) {
                            var pd = peer.querySelector('.pz-decide');
                            if (!pd) { return; }
                            pd.classList.toggle('done', !!text);
                            var ptxt = pd.querySelector('.pz-decide-text');
                            if (ptxt) { ptxt.textContent = text || '— ჯერ არ არის (D)'; }
                            var pta = pd.querySelector('textarea[name="decision"]');
                            if (pta && pta !== form.decision) { pta.value = form.decision.value; }
                        });
                        var li = document.querySelector('.pz-summary li[data-pk="' + slide.dataset.pk + '"] .pz-sum-decision');
                        if (li) { li.textContent = text || '—'; }
                        saved.textContent = 'შენახულია';
                        form.hidden = true;
                        window.scrollTo(0, 0);
                    })
                    .catch(function () { saved.textContent = 'ვერ შეინახა — შეამოწმეთ ინტერნეტი'; });
            });
        });

        function setAnon(on) {
            document.body.classList.toggle('anon', on);
            document.getElementById('pz-anon').setAttribute('aria-pressed', on ? 'true' : 'false');
            store('cases-anon', on ? '1' : '0');
        }
        document.getElementById('pz-anon').addEventListener('click', function () {
            setAnon(!document.body.classList.contains('anon'));
        });
        if (store('cases-anon') === '1') { setAnon(true); }

        var dsaBarBtn = document.getElementById('pz-dsa');
        function setDsa(on) {
            document.body.classList.toggle('am-dsa-mode', on);
            if (dsaBarBtn) { dsaBarBtn.setAttribute('aria-pressed', on ? 'true' : 'false'); }
            document.querySelectorAll('.pz-stage-slide [data-map-view]').forEach(function (b) {
                var active = (b.getAttribute('data-map-view') === 'dsa') === on;
                b.classList.toggle('on', active);
                b.setAttribute('aria-pressed', active ? 'true' : 'false');
            });
            store('cases-map-view', on ? 'dsa' : 'anat');
        }
        if (dsaBarBtn) {
            dsaBarBtn.addEventListener('click', function () {
                setDsa(!document.body.classList.contains('am-dsa-mode'));
            });
        }
        document.querySelectorAll('.pz-stage-slide [data-map-view]').forEach(function (b) {
            b.addEventListener('click', function () { setDsa(b.getAttribute('data-map-view') === 'dsa'); });
        });
        if (store('cases-map-view') === 'dsa') { setDsa(true); }

        function fullscreen() {
            if (document.fullscreenElement) { document.exitFullscreen(); }
            else if (document.documentElement.requestFullscreen) { document.documentElement.requestFullscreen().catch(function () {}); }
        }
        document.getElementById('pz-full').addEventListener('click', fullscreen);

        document.addEventListener('keydown', function (e) {
            var tag = (e.target.tagName || '').toLowerCase();
            if (tag === 'textarea' || tag === 'input') {
                if (e.key === 'Escape') { e.target.blur(); }
                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && e.target.form) { e.target.form.requestSubmit(); }
                return;
            }
            if (e.altKey || e.ctrlKey || e.metaKey) { return; }
            var slide = slides[cur];
            switch (e.key) {
                case 'ArrowRight': case 'PageDown': case ' ': show(cur + 1); break;
                case 'ArrowLeft': case 'PageUp': show(cur - 1); break;
                case 'ArrowDown': showPhoto(slide, currentPhoto(slide) + 1); break;
                case 'ArrowUp': showPhoto(slide, currentPhoto(slide) - 1); break;
                case 'Home': show(0); break;
                case 'End': show(slides.length - 1); break;
                case 'f': case 'F': case 'ფ': fullscreen(); break;
                case 'a': case 'A': case 'ა': setAnon(!document.body.classList.contains('anon')); break;
                case 'v': case 'V': case 'ვ': setDsa(!document.body.classList.contains('am-dsa-mode')); break;
                case 'd': case 'D': case 'დ':
                    var t = slide.querySelector('.pz-decide-toggle');
                    if (t) { t.click(); }
                    break;
                default: return;
            }
            e.preventDefault();
        });

        /* tablet: swipe left / right */
        var x0 = null;
        document.addEventListener('touchstart', function (e) { x0 = e.touches[0].clientX; }, { passive: true });
        document.addEventListener('touchend', function (e) {
            if (x0 === null) { return; }
            var dx = e.changedTouches[0].clientX - x0;
            x0 = null;
            if (Math.abs(dx) > 80 && !e.target.closest('.pz-strip, textarea')) { show(dx < 0 ? cur + 1 : cur - 1); }
        }, { passive: true });

        var start = parseInt((window.location.hash || '').slice(1), 10);
        show(start > 0 ? start - 1 : 0);
    }

    function initDetailControls() {
        var printBtn = document.getElementById('cs-print-btn');
        if (printBtn) {
            printBtn.addEventListener('click', function () { window.print(); });
        }
        var stagingCard = document.getElementById('staging');
        if (!stagingCard) { return; }
        var viewBtns = stagingCard.querySelectorAll('[data-map-view]');
        if (!viewBtns.length) { return; }
        function setCardView(mode) {
            var dsa = mode === 'dsa';
            stagingCard.classList.toggle('am-dsa-mode', dsa);
            viewBtns.forEach(function (b) {
                var on = b.getAttribute('data-map-view') === (dsa ? 'dsa' : 'anat');
                b.classList.toggle('on', on);
                b.setAttribute('aria-pressed', on ? 'true' : 'false');
            });
            store('cases-map-view', dsa ? 'dsa' : 'anat');
        }
        viewBtns.forEach(function (b) {
            b.addEventListener('click', function () { setCardView(b.getAttribute('data-map-view')); });
        });
        if (store('cases-map-view') === 'dsa') { setCardView('dsa'); }
    }

    function initFormControls() {
        var btn = document.getElementById('cs-insert-plan');
        var ta = document.getElementById('id_proposal');
        if (!btn || !ta) { return; }
        btn.addEventListener('click', function () {
            var planText = (btn.getAttribute('data-plan-text') || '').trim();
            if (!planText) { return; }
            var cur = ta.value.trim();
            if (!cur) {
                ta.value = planText;
            } else if (cur.indexOf(planText) === -1) {
                ta.value = cur + '\n' + planText;
            }
            ta.focus();
        });
    }

    initUpload();
    initViewer();
    initDetailControls();
    initFormControls();
    initPresent();
}());

