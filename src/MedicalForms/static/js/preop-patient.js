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

    var STRICT_NUM_RE = /^[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][+-]?\d+)?$/;
    function parseStrictNum(raw) {
        if (raw === null || raw === undefined) return NaN;
        var s = String(raw).trim();
        if (!s || !STRICT_NUM_RE.test(s)) return NaN;
        var n = Number(s.replace(/,/g, '.'));
        return isFinite(n) ? n : NaN;
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
            if (!raw) { hint.hidden = true; hint.className = 'fld-live-hint'; hint.textContent = ''; return; }
            var num = parseStrictNum(raw);
            var res = isNaN(num)
                ? { level: 'crit', msg: 'შეიყვანეთ მხოლოდ რიცხვითი მნიშვნელობა!' }
                : evalFn(num);
            if (!res) { hint.hidden = true; hint.className = 'fld-live-hint'; hint.textContent = ''; return; }
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
            if (v > 22.0 && v < 35.0) return { level: 'crit', msg: 'საეჭვო Hb ერთეული (' + v + ') — მიუთითეთ გ/ლ (35–260) ან გ/დლ (3.5–22.0)!' };
            if (v >= 3.5 && v <= 22.0) {
                var gl = Math.round(v * 100000) / 10000;
                return { level: gl < 100 ? 'crit' : (gl < 115 ? 'warn' : 'info'), msg: '≈ ' + gl + ' გ/ლ' + (gl < 100 ? ' (მძიმე ანემია!)' : (gl < 115 ? ' (ანემიის რეფლექსი)' : '')) };
            }
            if (v < 100) return { level: 'crit', msg: 'მძიმე ანემია (<100 გ/ლ)!' };
            if (v < 115) return { level: 'warn', msg: 'ანემია (<115 გ/ლ — ჩაირთვება ფერიტინი/TSAT)' };
            return null;
        });
    });
    document.querySelectorAll('input[name="k"]').forEach(function (el) {
        attachLiveGuard(el, function (v) {
            if (v < 1.0 || v > 12.0) return { level: 'crit', msg: 'დაუჯერებელი K+ (' + v + ' მმოლ/ლ) — გადაამოწმეთ!' };
            if (v < 3.3 || v >= 5.4) return { level: 'crit', msg: 'კრიტიკული K+ (' + v + ' მმოლ/ლ — არითმიის რისკი)!' };
            if (v < 3.5 || v > 5.0) return { level: 'warn', msg: 'სასაზღვრო K+ (ნორმა 3.5–5.0)' };
            return null;
        });
    });
    document.querySelectorAll('input[name="inr"], input[name="dos_inr"]').forEach(function (el) {
        var isDosInr = el.name === 'dos_inr';
        var inrLo = isDosInr ? 0.5 : 0.4;
        var inrHi = isDosInr ? 15.0 : 20.0;
        attachLiveGuard(el, function (v) {
            if (v < inrLo || v > inrHi) return { level: 'crit', msg: 'დაუჯერებელი INR (' + v + ') — გადაამოწმეთ!' };
            if (v > 1.5) return { level: 'crit', msg: 'მაღალი INR (>1.5 — სისხლდენის რისკი)!' };
            if (v > 1.35) return { level: 'warn', msg: 'მომატებული INR (>1.35 — სისხლდენის/ASRA ნეირაქსიული ზღვარი ≤ 1.4)' };
            return null;
        });
    });
    document.querySelectorAll('input[name="plt"]').forEach(function (el) {
        attachLiveGuard(el, function (v) {
            if (v < 1 || v > 2000000) return { level: 'crit', msg: 'დაუჯერებელი PLT (' + v + ') — გადაამოწმეთ მნიშვნელობა!' };
            if (v > 2500 && v < 3000) return { level: 'crit', msg: 'დააზუსტეთ თრომბოციტების ერთეული (×10^9/L თუ /µL)!' };
            var n = v >= 3000 ? Math.floor(v / 100) / 10 : Math.floor(v * 10) / 10;
            if (n < 100) return { level: 'crit', msg: 'თრომბოციტოპენია (<100 ×10^9/L)!' };
            if (n < 150) return { level: 'warn', msg: 'ზომიერი კლება (<150 ×10^9/L)' };
            return null;
        });
    });
    document.querySelectorAll('input[name="creat"]').forEach(function (el) {
        var unitSel = el.closest('form') ? el.closest('form').querySelector('select[name="creat_unit"]') : null;
        attachLiveGuard(el, function (v) {
            var u = unitSel ? unitSel.value : '';
            if (v < 0.1 || v > 3000) return { level: 'crit', msg: 'დაუჯერებელი კრეატინინი (' + v + ') — გადაამოწმეთ მნიშვნელობა და ერთეული!' };
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
    function convGlucoseMmol(v) {
        if (v <= 35) return v;
        var n = Math.round(v * 10000);
        var q = Math.floor(n / 1800), r = n % 1800;
        if (n < 702000) return q / 100;
        if (n > 1260000) return (q + (r ? 1 : 0)) / 100;
        if (r * 2 > 1800) return (q + 1) / 100;
        if (r * 2 < 1800) return q / 100;
        return (q % 2 === 0 ? q : q + 1) / 100;
    }
    document.querySelectorAll('input[name="glucose"], input[name="dos_glucose"]').forEach(function (el) {
        var isDosGl = el.name === 'dos_glucose';
        attachLiveGuard(el, function (v) {
            if (v < 0.5 || v > 1000) return { level: 'crit', msg: 'დაუჯერებელი გლუკოზა (' + v + ') — გადაამოწმეთ!' };
            var mmol = convGlucoseMmol(v);
            if (mmol >= 14.0 || mmol < 3.9) return { level: 'crit', msg: 'კრიტიკული გლიკემია (' + mmol + ' მმოლ/ლ)!' };
            if (isDosGl) {
                if (mmol > 10.0) return { level: 'warn', msg: 'მომატებული დილის გლუკოზა (>10.0 მმოლ/ლ — გლიკემიური კონტროლი)' };
            } else if (mmol >= 7.0) {
                return { level: 'warn', msg: 'მომატებული გლუკოზა (ჩაირთვება HbA1c)' };
            }
            return null;
        });
    });
    document.querySelectorAll('input[name="fibrinogen"]').forEach(function (el) {
        attachLiveGuard(el, function (v) {
            if (v < 0.15 || v > 1500) return { level: 'crit', msg: 'დაუჯერებელი ფიბრინოგენი (' + v + ') — გადაამოწმეთ!' };
            var gl = v > 15 ? Math.round((v / 100.0) * 10000) / 10000 : v;
            if (gl < 1.5) return { level: 'crit', msg: 'მძიმე ჰიპოფიბრინოგენემია (' + gl + ' გ/ლ < 1.5)!' };
            if (gl < 1.8) return { level: 'warn', msg: 'დაბალი ფიბრინოგენი (' + gl + ' გ/ლ < 1.8)' };
            return null;
        });
    });
    document.querySelectorAll('input[name="ldl"]').forEach(function (el) {
        attachLiveGuard(el, function (v) {
            if (v < 0.2 || v > 500) return { level: 'crit', msg: 'დაუჯერებელი LDL (' + v + ') — გადაამოწმეთ!' };
            var mmol = v > 20 ? Math.round((v / 38.67) * 100) / 100 : v;
            if (mmol >= 1.4) return { level: 'warn', msg: 'LDL ' + mmol + ' მმოლ/ლ (სამიზნე <1.4 მმოლ/ლ — ESVS)' };
            return null;
        });
    });
    document.querySelectorAll('input[name="hr"]').forEach(function (el) {
        attachLiveGuard(el, function (v) {
            if (v < 15 || v > 260) return { level: 'crit', msg: 'დაუჯერებელი პულსი (' + v + '/წთ — 15–260)!' };
            if (v < 45 || v > 110) return { level: 'crit', msg: 'კრიტიკული პულსი (' + v + '/წთ)!' };
            if (v < 55) return { level: 'warn', msg: 'ზომიერი ბრადიკარდია (HR ' + v + '/წთ < 55)' };
            return null;
        });
    });
    document.querySelectorAll('input[name="spo2"]').forEach(function (el) {
        attachLiveGuard(el, function (v) {
            if (v < 30 || v > 100) return { level: 'crit', msg: 'დაუჯერებელი სატურაცია (' + v + '% — 30–100%)!' };
            if (v < 90) return { level: 'crit', msg: 'მძიმე ჰიპოქსემია (SpO2 ' + v + '% < 90%)!' };
            if (v < 94) return { level: 'warn', msg: 'დაქვეითებული სატურაცია (SpO2 ' + v + '% < 94%)' };
            return null;
        });
    });
    document.querySelectorAll('input[name="weight_kg"]').forEach(function (el) {
        attachLiveGuard(el, function (v) {
            if (v < 25 || v > 260) return { level: 'crit', msg: 'დაუჯერებელი წონა (' + v + ' კგ — 25–260)!' };
            return null;
        });
    });
    document.querySelectorAll('input[name="height_cm"]').forEach(function (el) {
        attachLiveGuard(el, function (v) {
            if (v < 100 || v > 230) return { level: 'crit', msg: 'დაუჯერებელი სიმაღლე (' + v + ' სმ — 100–230)!' };
            return null;
        });
    });
    document.querySelectorAll('input[name="wbc"]').forEach(function (el) {
        attachLiveGuard(el, function (v) {
            if (v < 0.1 || v > 150000) return { level: 'crit', msg: 'დაუჯერებელი WBC (' + v + ') — გადაამოწმეთ!' };
            var rawW = v >= 200 ? (v / 1000.0) : v;
            var n = rawW < 3.5 ? Math.floor(rawW * 100) / 100 : (rawW > 12.0 ? Math.ceil(rawW * 100) / 100 : Math.round(rawW * 100) / 100);
            if (rawW < 3.5 || rawW > 12.0) return { level: 'crit', msg: 'პათოლოგიური WBC (' + n + ' ×10^9/L — ნორმა 3.5–12.0)!' };
            return null;
        });
    });
    document.querySelectorAll('input[name="albumin"]').forEach(function (el) {
        attachLiveGuard(el, function (v) {
            if (v < 0.5 || v > 80) return { level: 'crit', msg: 'დაუჯერებელი ალბუმინი (' + v + ') — გადაამოწმეთ!' };
            var gl = (v >= 0.5 && v <= 7.5) ? Math.round(v * 100000) / 10000 : v;
            if (gl < 30) return { level: 'crit', msg: 'მძიმე ჰიპოალბუმინემია (' + gl + ' გ/ლ < 30)!' };
            if (gl < 35) return { level: 'warn', msg: 'დაქვეითებული ალბუმინი (' + gl + ' გ/ლ < 35)' };
            return null;
        });
    });
    var BP_DIA_FOR_SYS = { bp_sys: 'bp_dia', bp_sys_l: 'bp_dia_l', dos_bp_sys: 'dos_bp_dia' };
    var BP_SYS_FOR_DIA = { bp_dia: 'bp_sys', bp_dia_l: 'bp_sys_l', dos_bp_dia: 'dos_bp_sys' };
    document.querySelectorAll('input[name="bp_sys"], input[name="bp_sys_l"], input[name="dos_bp_sys"]').forEach(function (el) {
        var diaName = BP_DIA_FOR_SYS[el.name];
        var diaEl = el.closest('form') && diaName ? el.closest('form').querySelector('input[name="' + diaName + '"]') : null;
        var sbpLo = el.name === 'dos_bp_sys' ? 40 : 30;
        var sbpHi = el.name === 'dos_bp_sys' ? 300 : 320;
        var dbpMax = el.name === 'dos_bp_sys' ? 200 : 220;
        attachLiveGuard(el, function (v) {
            if (v < sbpLo || v > sbpHi) return { level: 'crit', msg: 'დაუჯერებელი სისტოლური წნევა (' + v + ' mmHg)!' };
            var dv = diaEl ? parseStrictNum(diaEl.value) : NaN;
            if (!isNaN(dv) && v <= dv) return { level: 'crit', msg: 'სისტოლური (' + v + ') უნდა იყოს დიასტოლურზე (' + dv + ') მეტი!' };
            if (v >= 180 || v < 90) return { level: 'crit', msg: v >= 180 ? 'კრიტიკული ჰიპერტენზია (≥180 mmHg)!' : 'ჰიპოტენზია (<90 mmHg)!' };
            if (v < 100) return { level: 'warn', msg: 'სასაზღვრო ჰიპოტენზია (' + v + ' mmHg — SBP 90–99)' };
            if (v >= 160) return { level: 'warn', msg: 'მომატებული წნევა (≥160 mmHg)' };
            if (!isNaN(dv) && dv >= 20 && dv <= dbpMax && (v - dv) < 20) return { level: 'warn', msg: 'ვიწრო პულსური წნევა (' + (v - dv) + ' mmHg < 20 mmHg)' };
            return null;
        });
        if (diaEl) diaEl.addEventListener('input', function () { el.dispatchEvent(new Event('change')); });
    });
    document.querySelectorAll('input[name="bp_dia"], input[name="bp_dia_l"], input[name="dos_bp_dia"]').forEach(function (el) {
        var sysName = BP_SYS_FOR_DIA[el.name];
        var sysEl = el.closest('form') && sysName ? el.closest('form').querySelector('input[name="' + sysName + '"]') : null;
        var dbpHi = el.name === 'dos_bp_dia' ? 200 : 220;
        var sbpMin = el.name === 'dos_bp_dia' ? 40 : 30;
        var sbpMax = el.name === 'dos_bp_dia' ? 300 : 320;
        attachLiveGuard(el, function (v) {
            if (v < 20 || v > dbpHi) return { level: 'crit', msg: 'დაუჯერებელი დიასტოლური წნევა (' + v + ' mmHg)!' };
            var sv = sysEl ? parseStrictNum(sysEl.value) : NaN;
            if (!isNaN(sv) && sv <= v) return { level: 'crit', msg: 'დიასტოლური (' + v + ') არ შეიძლება აღემატებოდეს სისტოლურს (' + sv + ')!' };
            if (v >= 110) return { level: 'crit', msg: 'კრიტიკული დიასტოლური წნევა (≥110 mmHg)!' };
            if (v >= 100) return { level: 'warn', msg: 'მომატებული დიასტოლური წნევა (≥100 mmHg)' };
            if (!isNaN(sv) && sv >= sbpMin && sv <= sbpMax && (sv - v) < 20) return { level: 'warn', msg: 'ვიწრო პულსური წნევა (' + (sv - v) + ' mmHg < 20 mmHg)' };
            return null;
        });
        if (sysEl) sysEl.addEventListener('input', function () { el.dispatchEvent(new Event('change')); });
    });
    document.querySelectorAll('input[name="abi_r"], input[name="abi_l"]').forEach(function (el) {
        attachLiveGuard(el, function (v) {
            if (v < 0 || v > 3.0) return { level: 'crit', msg: 'დაუჯერებელი ABI (' + v + ') — გადაამოწმეთ!' };
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
            cancelWrap.hidden = (outSel.value !== 'deferred' && outSel.value.indexOf('cancelled_') !== 0);
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
