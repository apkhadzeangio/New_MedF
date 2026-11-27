(function () {
    var dirtyPanes = {};
    var submitting = false;
    var tabs = document.querySelectorAll('.po-tab[data-tab]');
    var panes = document.querySelectorAll('.po-pane[data-pane]');
    var hdrTab = document.getElementById('hdr-tab-inp');
    var stickyBar = document.getElementById('po-sticky-save');
    var stickyBtn = document.getElementById('btn-sticky-save');

    function getActiveTabKey() {
        var curBtn = document.querySelector('.po-tab.on[data-tab]');
        return curBtn ? curBtn.dataset.tab : '';
    }

    function syncStickyBar() {
        if (!stickyBar) return;
        var cur = getActiveTabKey();
        stickyBar.classList.toggle('on', Boolean(cur && dirtyPanes[cur]));
    }

    function submitActivePaneForm() {
        var cur = getActiveTabKey();
        if (!cur) return;
        var pane = document.querySelector('.po-pane[data-pane="' + cur + '"]');
        if (!pane) return;
        var forms = pane.querySelectorAll('form');
        for (var i = 0; i < forms.length; i++) {
            if (forms[i].getAttribute('enctype') === 'multipart/form-data') continue;
            submitting = true;
            dirtyPanes[cur] = false;
            syncStickyBar();
            if (forms[i].requestSubmit) {
                forms[i].requestSubmit();
            } else {
                forms[i].submit();
            }
            return;
        }
    }

    if (stickyBtn) {
        stickyBtn.addEventListener('click', submitActivePaneForm);
    }

    document.addEventListener('keydown', function (e) {
        if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) {
            var cur = getActiveTabKey();
            if (cur) {
                e.preventDefault();
                submitActivePaneForm();
            }
        }
    });

    panes.forEach(function (pane) {
        var paneKey = pane.dataset.pane;
        pane.querySelectorAll('form').forEach(function (frm) {
            if (frm.getAttribute('enctype') === 'multipart/form-data') return;
            frm.addEventListener('input', function () { dirtyPanes[paneKey] = true; syncStickyBar(); });
            frm.addEventListener('change', function () { dirtyPanes[paneKey] = true; syncStickyBar(); });
            frm.addEventListener('submit', function () {
                submitting = true;
                dirtyPanes[paneKey] = false;
                syncStickyBar();
            });
        });
    });

    function hasAnyDirtyPane() {
        return Object.keys(dirtyPanes).some(function (k) { return dirtyPanes[k]; });
    }

    window.addEventListener('beforeunload', function (e) {
        if (!submitting && hasAnyDirtyPane()) {
            e.preventDefault();
            e.returnValue = '';
        }
    });

    document.querySelectorAll('.js-auto-upload').forEach(function (inp) {
        inp.addEventListener('change', function () {
            if (!inp.files || !inp.files.length) return;
            if (dirtyPanes.s1 && !window.confirm('საფეხურ 1-ში შეტანილი ანალიზები ჯერ არ შეგინახავთ. გავაგრძელოთ ფაილის ატვირთვა შეუნახავად?')) {
                inp.value = '';
                return;
            }
            submitting = true;
            inp.form.submit();
        });
    });

    function switchTab(t, skipConfirm) {
        var curTab = getActiveTabKey();
        if (!skipConfirm && curTab && curTab !== t && dirtyPanes[curTab]) {
            if (!window.confirm('ამ ტაბში შეცვლილი მონაცემები ჯერ არ არის შენახული. მაინც გადავიდეთ სხვა ტაბზე?')) {
                return false;
            }
            dirtyPanes[curTab] = false;
        }
        tabs.forEach(function (b) { b.classList.toggle('on', b.dataset.tab === t); });
        panes.forEach(function (p) { p.hidden = (p.dataset.pane !== t); });
        if (hdrTab) hdrTab.value = t;
        syncStickyBar();
        try {
            var u = new URL(window.location.href);
            u.searchParams.set('tab', t);
            window.history.replaceState(null, '', u.toString());
        } catch (e) {}
        return true;
    }

    tabs.forEach(function (btn) {
        btn.addEventListener('click', function () { switchTab(btn.dataset.tab, false); });
    });

    // Guided Missing-Item Jump Chips
    document.querySelectorAll('.po-jump-chip[data-jump-tab]').forEach(function (chip) {
        chip.addEventListener('click', function () {
            var targetTab = chip.dataset.jumpTab;
            var targetField = chip.dataset.jumpField;
            var container = null;
            if (targetTab === 'header') {
                var hdrDetails = document.getElementById('po-header-edit');
                if (hdrDetails) {
                    hdrDetails.open = true;
                    container = hdrDetails;
                }
            } else {
                if (!switchTab(targetTab, false)) return;
                container = document.querySelector('.po-pane[data-pane="' + targetTab + '"]');
            }
            if (!container) return;
            var el = targetField
                ? (container.querySelector('#' + targetField) || container.querySelector('[name="' + targetField + '"]'))
                : container;
            if (!el) el = container;
            var parentDetails = el.closest('details');
            if (parentDetails) parentDetails.open = true;
            var flashBox = el.closest('.fld') || el.closest('.po-task-row') || el.closest('.po-q-item') || el;
            flashBox.scrollIntoView({ behavior: 'smooth', block: 'center' });
            flashBox.classList.remove('po-field-flash');
            void flashBox.offsetWidth;
            flashBox.classList.add('po-field-flash');
            try { el.focus({ preventScroll: true }); } catch (e) {}
        });
    });

    // Stage 1 Required vs Optional Visual Hierarchy + Live Physiological Range Guard
    var reqDataEl = document.getElementById('po-s1-req-data');
    var s1Pane = document.querySelector('.po-pane[data-pane="s1"]');
    if (reqDataEl && s1Pane) {
        try {
            var reqSet = {};
            JSON.parse(reqDataEl.textContent || '[]').forEach(function (k) { reqSet[k] = true; });
            Object.keys(reqSet).forEach(function (fname) {
                var ctrl = s1Pane.querySelector('[name="' + fname + '"]');
                if (!ctrl) return;
                var lbl = ctrl.closest('.fld');
                if (!lbl || lbl.classList.contains('is-req')) return;
                lbl.classList.add('is-req');
                var tag = document.createElement('span');
                tag.className = 'fld-req-tag';
                lbl.insertBefore(tag, ctrl.closest('.d-flex') || ctrl);
                var syncReq = function () {
                    var v = (ctrl.value || '').trim();
                    var ok = v !== '' && v !== 'pending';
                    lbl.classList.toggle('is-filled', ok);
                    tag.textContent = ok ? '✓' : 'სავალდებულო';
                };
                ctrl.addEventListener('input', syncReq);
                ctrl.addEventListener('change', syncReq);
                syncReq();
            });
        } catch (e) {}
    }

    function attachLiveGuard(ctrl, evalFn) {
        if (!ctrl) return;
        var lbl = ctrl.closest('.fld');
        if (!lbl) return;
        var hint = document.createElement('div');
        hint.className = 'fld-live-hint';
        hint.hidden = true;
        lbl.appendChild(hint);
        var run = function () {
            ctrl.classList.remove('lv-warn', 'lv-crit');
            var raw = (ctrl.value || '').trim();
            if (!raw) { hint.hidden = true; return; }
            var num = parseFloat(raw.replace(/,/g, '.'));
            if (!isFinite(num)) { hint.hidden = true; return; }
            var res = evalFn(num);
            if (!res) { hint.hidden = true; return; }
            if (res.level === 'crit') ctrl.classList.add('lv-crit');
            else if (res.level === 'warn') ctrl.classList.add('lv-warn');
            hint.className = 'fld-live-hint ' + res.level;
            hint.textContent = res.msg;
            hint.hidden = false;
        };
        ctrl.addEventListener('input', run);
        ctrl.addEventListener('change', run);
        run();
    }

    document.querySelectorAll('input[name="hb"]').forEach(function (el) {
        attachLiveGuard(el, function (v) {
            if (v < 3.5 || v > 260) return { level: 'crit', msg: 'დაუჯერებელი Hb (' + v + ') — გადაამოწმეთ მნიშვნელობა და ერთეული!' };
            if (v > 22.0 && v < 40.0) return { level: 'crit', msg: 'საეჭვო Hb ერთეული (' + v + ') — მიუთითეთ გ/ლ (40–260) ან გ/დლ (3.5–22.0)!' };
            if (v >= 3.5 && v <= 22.0) {
                var gl = Math.round(v * 100) / 10;
                return { level: gl < 100 ? 'crit' : (gl < 115 ? 'warn' : 'info'), msg: '≈ ' + gl + ' გ/ლ' + (gl < 100 ? ' (მძიმე ანემია!)' : (gl < 115 ? ' (ანემიის რეფლექსი)' : '')) };
            }
            if (v < 100) return { level: 'crit', msg: 'მძიმე ანემია (<100 გ/ლ)!' };
            if (v < 115) return { level: 'warn', msg: 'ანემია (<115 გ/ლ — ჩაირთვება ფერიტინი/TSAT)' };
            return null;
        });
    });
    document.querySelectorAll('input[name="k"]').forEach(function (el) {
        attachLiveGuard(el, function (v) {
            if (v < 3.3 || v >= 5.4) return { level: 'crit', msg: 'კრიტიკული K+ (' + v + ' მმოლ/ლ — არითმიის რისკი)!' };
            if (v < 3.5 || v > 5.0) return { level: 'warn', msg: 'სასაზღვრო K+ (ნორმა 3.5–5.0)' };
            return null;
        });
    });
    document.querySelectorAll('input[name="inr"], input[name="dos_inr"]').forEach(function (el) {
        attachLiveGuard(el, function (v) {
            if (v > 1.5) return { level: 'crit', msg: 'მაღალი INR (>1.5 — სისხლდენის რისკი)!' };
            if (v > 1.35) return { level: 'warn', msg: 'მომატებული INR (>1.35 — სისხლდენის/ASRA ნეირაქსიული ზღვარი ≤ 1.4)' };
            return null;
        });
    });
    document.querySelectorAll('input[name="plt"]').forEach(function (el) {
        attachLiveGuard(el, function (v) {
            if (v > 2500 && v < 3000) return { level: 'crit', msg: 'დააზუსტეთ თრომბოციტების ერთეული (×10^9/L თუ /µL)!' };
            var n = v >= 3000 ? Math.round(v / 1000) : v;
            if (n < 100) return { level: 'crit', msg: 'თრომბოციტოპენია (<100 ×10^9/L)!' };
            if (n < 150) return { level: 'warn', msg: 'ზომიერი კლება (<150 ×10^9/L)' };
            return null;
        });
    });
    document.querySelectorAll('input[name="creat"]').forEach(function (el) {
        var unitSel = el.closest('form') ? el.closest('form').querySelector('select[name="creat_unit"]') : null;
        attachLiveGuard(el, function (v) {
            var u = unitSel ? unitSel.value : '';
            if (!u && v > 15 && v < 35) return { level: 'crit', msg: 'აირჩიეთ ერთეული (მკმოლ/ლ თუ მგ/დლ)!' };
            if ((u === 'umol' && v < 15) || (u === 'mgdl' && v > 35)) {
                return { level: 'crit', msg: 'კრეატინინის მნიშვნელობა და არჩეული ერთეული არ ემთხვევა!' };
            }
            var mgdl = u === 'mgdl' ? v : (u === 'umol' ? v / 88.4 : (v <= 15 ? v : v / 88.4));
            if (mgdl > 2.0) return { level: 'crit', msg: 'მაღალი კრეატინინი (RCRI +1 / ნეფროლოგი)!' };
            if (mgdl >= 1.4) return { level: 'warn', msg: 'დაქვეითებული თირკმლის ფუნქცია' };
            return null;
        });
        if (unitSel) unitSel.addEventListener('change', function () { el.dispatchEvent(new Event('input')); });
    });
    document.querySelectorAll('input[name="glucose"], input[name="dos_glucose"]').forEach(function (el) {
        attachLiveGuard(el, function (v) {
            var mmol = v > 35 ? Math.round((v / 18.0) * 100) / 100 : v;
            if (mmol >= 14.0 || mmol < 3.9) return { level: 'crit', msg: 'კრიტიკული გლიკემია (' + mmol + ' მმოლ/ლ)!' };
            if (mmol >= 7.0) return { level: 'warn', msg: 'მომატებული გლუკოზა (ჩაირთვება HbA1c)' };
            return null;
        });
    });
    document.querySelectorAll('input[name="fibrinogen"]').forEach(function (el) {
        attachLiveGuard(el, function (v) {
            var gl = v > 15 ? Math.round((v / 100.0) * 100) / 100 : v;
            if (gl < 1.5) return { level: 'crit', msg: 'მძიმე ჰიპოფიბრინოგენემია (' + gl + ' გ/ლ < 1.5)!' };
            if (gl < 1.8) return { level: 'warn', msg: 'დაბალი ფიბრინოგენი (' + gl + ' გ/ლ < 1.8)' };
            return null;
        });
    });
    document.querySelectorAll('input[name="ldl"]').forEach(function (el) {
        attachLiveGuard(el, function (v) {
            var mmol = v > 20 ? Math.round((v / 38.67) * 100) / 100 : v;
            if (mmol >= 1.4) return { level: 'warn', msg: 'LDL ' + mmol + ' მმოლ/ლ (სამიზნე <1.4 მმოლ/ლ — ESVS)' };
            return null;
        });
    });
    document.querySelectorAll('input[name="bp_sys"], input[name="dos_bp_sys"]').forEach(function (el) {
        attachLiveGuard(el, function (v) {
            if (v >= 180 || v < 90) return { level: 'crit', msg: v >= 180 ? 'კრიტიკული ჰიპერტენზია (≥180 mmHg)!' : 'ჰიპოტენზია (<90 mmHg)!' };
            if (v >= 160) return { level: 'warn', msg: 'მომატებული წნევა (≥160 mmHg)' };
            return null;
        });
    });
    document.querySelectorAll('input[name="abi_r"], input[name="abi_l"]').forEach(function (el) {
        attachLiveGuard(el, function (v) {
            if (v > 1.40) return { level: 'warn', msg: 'არაკომპრესირებადი (>1.40 — შეიტანეთ Toe Pressure / TBI)' };
            if (v < 0.40) return { level: 'crit', msg: 'კრიტიკული იშემია (ABI < 0.40)!' };
            if (v < 0.80) return { level: 'warn', msg: 'დაქვეითებული ABI (< 0.80)' };
            return null;
        });
    });

    // Copy signed Patient Self-Fill URL
    var copyBtn = document.getElementById('btn-copy-self');
    if (copyBtn) {
        copyBtn.addEventListener('click', function () {
            var fullUrl = window.location.origin + copyBtn.dataset.selfPath;
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

    // Placement kind toggle (dated vs Someday list)
    var slotSel = document.getElementById('slot-kind-sel');
    var vdWrap = document.getElementById('visit-date-wrap');
    if (slotSel && vdWrap) {
        slotSel.addEventListener('change', function () {
            vdWrap.hidden = (slotSel.value !== 'dated');
        });
    }

    // Smart branching sub-fields in questionnaires
    document.querySelectorAll('[data-toggle-sub]').forEach(function (sw) {
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

    // Clinical rule citation modal (Track A2)
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
