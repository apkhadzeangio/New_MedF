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
        if (!isNaN(wt) && (wt < 25 || wt > 260)) {
            msgs.push('შეამოწმეთ წონა (' + wt + '): შეიყვანეთ კილოგრამებში (25–260 კგ).');
        }
        if (!isNaN(creat) && creat > 15 && creat < 35 && !cunit) {
            msgs.push('კრეატინინი (' + creat + ') საეჭვოა ერთეულის გარეშე — აირჩიეთ µmol/L ან mg/dL.');
        }
        if (!isNaN(hb) && (hb < 1 || (hb > 22 && hb < 40) || hb > 260)) {
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

    // 4. Camera / Lab Photo Auto-Upload (supports both Nurse Workspace and embedded classic Tab 2 uploader)
    document.querySelectorAll('.js-nw-auto-upload, .js-auto-upload').forEach(function (inp) {
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

/* B4: localStorage Auto-Draft Interruption Recovery & Mobile WHO Sign-In toggle feedback */
(function () {
    var root = document.getElementById('nw-patient-root');
    if (!root) return;
    var pk = root.dataset.patientPk;
    if (!pk) return;

    var STORAGE_KEY = 'preop_nurse_draft_v1_' + pk;
    var MAX_AGE_MS = 12 * 60 * 60 * 1000;
    var banner = document.getElementById('nw-draft-banner');
    var metaEl = document.getElementById('nw-draft-meta');
    var btnResume = document.getElementById('btn-draft-resume');
    var btnDiscard = document.getElementById('btn-draft-discard');
    var draftForms = Array.prototype.slice.call(root.querySelectorAll('form[data-nw-draft]'));
    var lastFocused = null;

    function loadDraft() {
        try {
            var raw = localStorage.getItem(STORAGE_KEY);
            if (!raw) return null;
            var obj = JSON.parse(raw);
            if (!obj || !obj.savedAt || (Date.now() - obj.savedAt) > MAX_AGE_MS) {
                localStorage.removeItem(STORAGE_KEY);
                return null;
            }
            return obj;
        } catch (e) {
            return null;
        }
    }

    function saveDraft(form, activeEl) {
        var fKey = form.dataset.nwDraft;
        var cur = loadDraft() || {forms: {}, lastField: null, savedAt: Date.now()};
        var vals = {};
        Array.prototype.slice.call(form.elements).forEach(function (el) {
            if (!el.name || el.name === 'csrfmiddlewaretoken' || el.type === 'hidden' || el.type === 'submit' || el.type === 'button' || el.type === 'file') return;
            if (el.type === 'checkbox') {
                vals[el.name] = Boolean(el.checked);
            } else if (el.type === 'radio') {
                if (el.checked) vals[el.name] = el.value;
            } else {
                vals[el.name] = el.value;
            }
        });
        cur.forms[fKey] = vals;
        cur.savedAt = Date.now();
        if (activeEl && activeEl.name) {
            var lbl = activeEl.id ? document.querySelector('label[for="' + activeEl.id + '"]') : null;
            cur.lastField = {
                formKey: fKey,
                id: activeEl.id || '',
                name: activeEl.name,
                label: lbl ? lbl.textContent.trim() : activeEl.name
            };
        }
        try { localStorage.setItem(STORAGE_KEY, JSON.stringify(cur)); } catch (e) {}
    }

    function clearFormDraft(fKey) {
        var cur = loadDraft();
        if (!cur) return;
        delete cur.forms[fKey];
        if (Object.keys(cur.forms).length === 0) {
            try { localStorage.removeItem(STORAGE_KEY); } catch (e) {}
        } else {
            try { localStorage.setItem(STORAGE_KEY, JSON.stringify(cur)); } catch (e) {}
        }
    }

    // Restore unsaved draft after interruption / reload
    var restored = loadDraft();
    var didRestore = false;
    if (restored && restored.forms) {
        draftForms.forEach(function (form) {
            var fKey = form.dataset.nwDraft;
            var vals = restored.forms[fKey];
            if (!vals) return;
            Array.prototype.slice.call(form.elements).forEach(function (el) {
                if (!el.name || !(el.name in vals)) return;
                if (el.type === 'checkbox') {
                    if (el.checked !== Boolean(vals[el.name])) {
                        el.checked = Boolean(vals[el.name]);
                        didRestore = true;
                    }
                } else if (el.type === 'radio') {
                    if (el.value === String(vals[el.name]) && !el.checked) {
                        el.checked = true;
                        didRestore = true;
                    }
                } else if (el.value !== String(vals[el.name])) {
                    el.value = String(vals[el.name]);
                    didRestore = true;
                }
            });
        });
    }

    if (didRestore && banner) {
        banner.hidden = false;
        lastFocused = restored.lastField || null;
        if (metaEl && lastFocused && lastFocused.label) {
            metaEl.textContent = 'ბოლო შევსებული ველი: ' + lastFocused.label;
        }
        var vitForm = document.getElementById('nw-vitals-form');
        if (vitForm) vitForm.dispatchEvent(new Event('input'));
    }

    if (btnResume) {
        btnResume.addEventListener('click', function () {
            var target = null;
            if (lastFocused && lastFocused.id) target = document.getElementById(lastFocused.id);
            if (!target && lastFocused && lastFocused.name) {
                target = root.querySelector('[name="' + lastFocused.name + '"]');
            }
            if (target) {
                var det = target.closest('details');
                if (det) det.open = true;
                target.scrollIntoView({behavior: 'smooth', block: 'center'});
                setTimeout(function () { target.focus(); }, 200);
            }
        });
    }

    if (btnDiscard) {
        btnDiscard.addEventListener('click', function () {
            try { localStorage.removeItem(STORAGE_KEY); } catch (e) {}
            window.location.reload();
        });
    }

    draftForms.forEach(function (form) {
        form.addEventListener('input', function (e) { saveDraft(form, e.target); });
        form.addEventListener('change', function (e) { saveDraft(form, e.target); });
        form.addEventListener('submit', function () {
            form.querySelectorAll('.js-comma-num').forEach(function (inp) {
                if (inp.value && inp.value.indexOf(',') >= 0) {
                    inp.value = inp.value.trim().replace(/,/g, '.');
                }
            });
            clearFormDraft(form.dataset.nwDraft);
        });
    });

    // Live check visual state on WHO Sign-In 56px rows
    root.querySelectorAll('.nw-who-cb').forEach(function (cb) {
        var sync = function () {
            var row = cb.closest('.nw-who-row');
            if (!row) return;
            row.classList.toggle('checked', cb.checked);
            var st = row.querySelector('.nw-who-state');
            if (st) st.textContent = cb.checked ? '✓' : '○';
        };
        cb.addEventListener('change', sync);
        sync();
    });

    // Embedded classic tabs parity: smart branching sub-fields, self-fill copy, and rule citation modal
    root.querySelectorAll('[data-toggle-sub]').forEach(function (sw) {
        var sub = document.getElementById(sw.dataset.toggleSub);
        if (!sub) return;
        sw.querySelectorAll('input[type="radio"]').forEach(function (r) {
            r.addEventListener('change', function () {
                sub.hidden = (r.value !== '1' || !r.checked);
            });
        });
    });

    var swMets = document.getElementById('sw-mets');
    var subMets = document.getElementById('sub-mets-limit');
    if (swMets && subMets) {
        swMets.querySelectorAll('input[type="radio"]').forEach(function (r) {
            r.addEventListener('change', function () {
                subMets.hidden = (r.value !== '0' || !r.checked);
            });
        });
    }

    [
        ['chk-allergy-other', 'sub-allergy'],
        ['chk-dm', 'sub-dm'],
        ['chk-pulm', 'sub-pulm'],
        ['chk-ulcer', 'sub-ulcer'],
        ['chk-ven-rec', 'sub-ven-rec'],
        ['chk-abf-vasc', 'sub-abf-vasc'],
        ['chk-abf-abdo', 'sub-abf-abdo']
    ].forEach(function (pair) {
        var chk = document.getElementById(pair[0]);
        var sub = document.getElementById(pair[1]);
        if (chk && sub) {
            chk.addEventListener('change', function () {
                sub.hidden = !chk.checked;
            });
        }
    });

    var outSel = document.getElementById('sel-case-outcome');
    var cancelWrap = document.getElementById('wrap-cancel-reasons');
    if (outSel && cancelWrap) {
        outSel.addEventListener('change', function () {
            cancelWrap.hidden = (outSel.value.indexOf('cancelled_') !== 0);
        });
    }

    var copyBtn = document.getElementById('btn-copy-self');
    if (copyBtn) {
        copyBtn.addEventListener('click', function () {
            var fullUrl = window.location.origin + (copyBtn.dataset.selfPath || '');
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(fullUrl).then(function () {
                    var orig = copyBtn.textContent;
                    copyBtn.textContent = '✓ ბმული დაკოპირდა';
                    setTimeout(function () { copyBtn.textContent = orig; }, 2200);
                });
            } else {
                window.prompt('დააკოპირეთ ბმული პაციენტისთვის:', fullUrl);
            }
        });
    }

    var ruleDlg = document.getElementById('po-rule-modal');
    var rulesDataEl = document.getElementById('po-rules-data');
    if (ruleDlg && rulesDataEl) {
        var rulesMap = {};
        try { rulesMap = JSON.parse(rulesDataEl.textContent || '{}'); } catch (e) { rulesMap = {}; }
        var closeRuleDlg = function () {
            if (typeof ruleDlg.close === 'function' && ruleDlg.open) ruleDlg.close();
            else ruleDlg.removeAttribute('open');
        };
        document.addEventListener('click', function (ev) {
            var btn = ev.target.closest && ev.target.closest('.po-cite-btn');
            if (!btn) return;
            ev.preventDefault();
            ev.stopPropagation();
            var rid = btn.dataset.ruleId;
            var meta = rid && rulesMap[rid];
            if (!meta) return;
            var setTxt = function (id, txt) {
                var el = document.getElementById(id);
                if (el) el.textContent = txt || '';
            };
            setTxt('po-rm-id', meta.id);
            setTxt('po-rm-domain', meta.domain_label + ' · ' + meta.kind_label);
            var stEl = document.getElementById('po-rm-status');
            if (stEl) {
                stEl.textContent = meta.status === 'local-decision' ? 'ადგილობრივი გადაწყვეტილება' : '✓ პირველწყაროთი დადასტურებული';
                stEl.className = 'po-badge ' + (meta.status === 'local-decision' ? 'st-needs_doctor' : 'st-ready');
            }
            setTxt('po-rm-title', meta.title_ka);
            setTxt('po-rm-desc', meta.description_ka);
            setTxt('po-rm-source', meta.source + ' (' + meta.year + ') — ' + meta.section);
            setTxt('po-rm-class', meta.class_level);
            setTxt('po-rm-wording', meta.source_wording);
            setTxt('po-rm-val', meta.value_label);
            setTxt('po-rm-verified', meta.verified_on);
            var linkEl = document.getElementById('po-rm-link');
            if (linkEl) {
                linkEl.href = (ruleDlg.dataset.catalogUrl || '/preop/rules/') + '#rule-' + encodeURIComponent(meta.id);
            }
            if (typeof ruleDlg.showModal === 'function') ruleDlg.showModal();
            else ruleDlg.setAttribute('open', 'open');
        });
        ['po-rm-close', 'po-rm-ok'].forEach(function (id) {
            var cBtn = document.getElementById(id);
            if (cBtn) cBtn.addEventListener('click', closeRuleDlg);
        });
        ruleDlg.addEventListener('click', function (ev) {
            if (ev.target === ruleDlg) closeRuleDlg();
        });
    }
})();

