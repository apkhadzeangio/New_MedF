/* Track B — Nurse Workspace client-side helpers (comma decimals, live vitals guards,
   CEA bilateral lock, 1-tap self-fill link tracking, camera auto-upload). */
(function () {
    function getCsrfToken() {
        var inp = document.querySelector('input[name="csrfmiddlewaretoken"]');
        return inp ? inp.value : '';
    }

    function parseNum(raw) {
        if (raw === null || raw === undefined) return NaN;
        var s = String(raw).trim().replace(/,/g, '.');
        if (!s) return NaN;
        return Number(s);
    }

    // 1. Mobile registration: hide side for AAA/ABF, lock bilateral ('B') for CEA
    var regOps = document.querySelectorAll('.js-reg-op');
    var sideCard = document.getElementById('nw-reg-side-card');
    var sideB = document.getElementById('nw-side-b');
    var ceaNote = document.getElementById('nw-cea-side-note');
    function syncRegSide() {
        var picked = document.querySelector('.js-reg-op:checked');
        if (!picked) return;
        var v = picked.value || '';
        var isMidline = (v === 'open' || v === 'evar' || v === 'tbd' || v.indexOf('abf_') === 0);
        var isCea = v.indexOf('cea_') === 0;
        if (sideCard) sideCard.hidden = isMidline;
        if (sideB) {
            sideB.disabled = isCea;
            if (isCea && sideB.checked) sideB.checked = false;
        }
        if (ceaNote) ceaNote.hidden = !isCea;
    }
    if (regOps.length) {
        regOps.forEach(function (el) { el.addEventListener('change', syncRegSide); });
        syncRegSide();
    }

    // 2. Comma-decimal normalization on submit + instant clinical guardrails on vitals form
    var vitalsForm = document.getElementById('nw-vitals-form');
    var liveGuard = document.getElementById('nw-live-guard');
    function runVitalsGuards() {
        if (!vitalsForm || !liveGuard) return;
        var msgs = [];
        var sbp = parseNum((document.getElementById('nw-bp-sys') || {}).value);
        var dbp = parseNum((document.getElementById('nw-bp-dia') || {}).value);
        var wt = parseNum((document.getElementById('nw-wt') || {}).value);
        var creat = parseNum((document.getElementById('nw-creat') || {}).value);
        var cunit = (document.getElementById('nw-creat-unit') || {}).value || '';
        var hb = parseNum((document.getElementById('nw-hb') || {}).value);

        if (!isNaN(sbp) && !isNaN(dbp) && sbp <= dbp) {
            msgs.push('შეამოწმეთ წნევა: ზედა წნევა (' + sbp + ') უნდა იყოს ქვედაზე (' + dbp + ') მეტი!');
        }
        if (!isNaN(wt) && (wt < 30 || wt > 250)) {
            msgs.push('შეამოწმეთ წონა (' + wt + '): შეიყვანეთ კილოგრამებში (30–250 კგ).');
        }
        if (!isNaN(creat) && creat >= 15 && creat <= 35 && !cunit) {
            msgs.push('კრეატინინი (' + creat + ') საეჭვოა ერთეულის გარეშე — აირჩიეთ µmol/L ან mg/dL.');
        }
        if (!isNaN(hb) && (hb < 4 || (hb > 25 && hb < 40) || hb > 240)) {
            msgs.push('შეამოწმეთ ჰემოგლობინი (' + hb + ') — მიუთითეთ გ/ლ (მაგ. 130) ან გ/დლ (მაგ. 13,0).');
        }

        if (msgs.length) {
            liveGuard.hidden = false;
            liveGuard.innerHTML = msgs.map(function (m) { return '<div>' + m + '</div>'; }).join('');
        } else {
            liveGuard.hidden = true;
            liveGuard.innerHTML = '';
        }
    }
    if (vitalsForm) {
        vitalsForm.addEventListener('input', runVitalsGuards);
        vitalsForm.addEventListener('change', runVitalsGuards);
        vitalsForm.addEventListener('submit', function () {
            vitalsForm.querySelectorAll('.js-comma-num').forEach(function (inp) {
                if (inp.value && inp.value.indexOf(',') >= 0) {
                    inp.value = inp.value.trim().replace(/,/g, '.');
                }
            });
        });
        runVitalsGuards();
    }

    // 3. 1-Tap Self-Fill Link Tracking (WhatsApp / SMS / Copy)
    function markSelfFillSent(url, via) {
        if (!url) return;
        var fd = new FormData();
        fd.append('csrfmiddlewaretoken', getCsrfToken());
        fd.append('via', via || 'copy');
        fetch(url, {
            method: 'POST',
            headers: {'X-Requested-With': 'XMLHttpRequest'},
            body: fd
        }).then(function (r) { return r.ok ? r.json() : null; }).then(function (data) {
            if (!data || !data.ok) return;
            var pill = document.getElementById('nw-sf-status-pill');
            if (pill && data.self_fill) {
                pill.textContent = data.self_fill.label;
                pill.className = 'nw-sf-pill tone-' + data.self_fill.tone;
            }
        }).catch(function () {});
    }

    document.querySelectorAll('.js-sf-track').forEach(function (el) {
        el.addEventListener('click', function () {
            markSelfFillSent(el.dataset.trackUrl, el.dataset.via);
        });
    });

    document.querySelectorAll('.js-sf-copy').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var link = btn.dataset.copyUrl || '';
            if (navigator.clipboard && navigator.clipboard.writeText && link) {
                navigator.clipboard.writeText(link).catch(function () {});
            }
            var orig = btn.textContent;
            btn.textContent = 'დაკოპირდა ✓';
            markSelfFillSent(btn.dataset.trackUrl, 'copy');
            setTimeout(function () { btn.textContent = orig; }, 2200);
        });
    });

    // 4. Camera / Lab Photo Auto-Upload
    document.querySelectorAll('.js-nw-auto-upload').forEach(function (inp) {
        inp.addEventListener('change', function () {
            if (inp.files && inp.files.length && inp.form) {
                inp.form.submit();
            }
        });
    });
})();

/* B3: 1-tap Jump from Missing Checklist to exact section / input */
(function () {
    document.querySelectorAll('.js-nw-jump').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var secId = btn.dataset.targetSection;
            var inpId = btn.dataset.targetInput;
            var sec = secId ? document.getElementById(secId) : null;
            if (sec) {
                if (sec.tagName === 'DETAILS') sec.open = true;
                sec.scrollIntoView({behavior: 'smooth', block: 'start'});
            }
            if (inpId) {
                setTimeout(function () {
                    var inp = document.getElementById(inpId);
                    if (inp) inp.focus();
                }, 220);
            }
        });
    });
})();
