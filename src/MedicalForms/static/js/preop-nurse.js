/* Track B — Nurse Workspace client-side helpers (comma decimals, live vitals guards,
   CEA bilateral lock, 1-tap self-fill link tracking, camera auto-upload). */
(function () {
    function getCsrfToken() {
        var inp = document.querySelector('input[name="csrfmiddlewaretoken"]');
        return inp ? inp.value : '';
    }

    var STRICT_NUM_RE = /^[+-]?(?:\d+(?:[.,]\d+)?|[.,]\d+)$/;
    function parseNum(raw) {
        if (raw === null || raw === undefined) return NaN;
        var s = String(raw).trim();
        if (!s || !STRICT_NUM_RE.test(s)) return NaN;
        var n = Number(s.replace(/,/g, '.'));
        return isFinite(n) ? n : NaN;
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
        var rawSbp = ((document.getElementById('nw-bp-sys') || {}).value || '').trim();
        var rawDbp = ((document.getElementById('nw-bp-dia') || {}).value || '').trim();
        var rawWt = ((document.getElementById('nw-wt') || {}).value || '').trim();
        var rawCreat = ((document.getElementById('nw-creat') || {}).value || '').trim();
        var rawHb = ((document.getElementById('nw-hb') || {}).value || '').trim();
        var sbp = parseNum(rawSbp);
        var dbp = parseNum(rawDbp);
        var wt = parseNum(rawWt);
        var creat = parseNum(rawCreat);
        var cunit = (document.getElementById('nw-creat-unit') || {}).value || '';
        var hb = parseNum(rawHb);

        if ((rawSbp && isNaN(sbp)) || (rawDbp && isNaN(dbp))) {
            msgs.push('შეამოწმეთ წნევა: შეიყვანეთ მხოლოდ რიცხვები!');
        }
        if (!isNaN(sbp) && (sbp < 30 || sbp > 320)) {
            msgs.push('შეამოწმეთ ზედა წნევა (' + sbp + '): დაუჯერებელი მნიშვნელობა (30–320 mmHg)!');
        } else if (!isNaN(sbp) && sbp >= 180) {
            msgs.push('კრიტიკულად მაღალი ზედა წნევა (' + sbp + ' ≥ 180 mmHg) — აცნობეთ ექიმს!');
        } else if (!isNaN(sbp) && sbp < 90) {
            msgs.push('დაბალი ზედა წნევა (' + sbp + ' < 90 mmHg) — აცნობეთ ექიმს!');
        } else if (!isNaN(sbp) && sbp >= 160) {
            msgs.push('მომატებული ზედა წნევა (' + sbp + ' ≥ 160 mmHg).');
        }
        if (!isNaN(dbp) && (dbp < 20 || dbp > 220)) {
            msgs.push('შეამოწმეთ ქვედა წნევა (' + dbp + '): დაუჯერებელი მნიშვნელობა (20–220 mmHg)!');
        } else if (!isNaN(dbp) && dbp >= 110) {
            msgs.push('კრიტიკულად მაღალი ქვედა წნევა (' + dbp + ' ≥ 110 mmHg) — აცნობეთ ექიმს!');
        } else if (!isNaN(dbp) && dbp >= 100) {
            msgs.push('მომატებული ქვედა წნევა (' + dbp + ' ≥ 100 mmHg).');
        }
        if (!isNaN(sbp) && !isNaN(dbp) && sbp <= dbp) {
            msgs.push('შეამოწმეთ წნევა: ზედა წნევა (' + sbp + ') უნდა იყოს ქვედაზე (' + dbp + ') მეტი!');
        } else if (!isNaN(sbp) && !isNaN(dbp) && (sbp - dbp) < 20) {
            msgs.push('ვიწრო პულსური წნევა (' + sbp + '/' + dbp + ' mmHg) — გადაამოწმეთ გაზომვა და აცნობეთ ექიმს!');
        }
        if (rawWt && isNaN(wt)) {
            msgs.push('შეამოწმეთ წონა: შეიყვანეთ მხოლოდ რიცხვი კილოგრამებში.');
        } else if (!isNaN(wt) && (wt < 25 || wt > 260)) {
            msgs.push('შეამოწმეთ წონა (' + wt + '): შეიყვანეთ კილოგრამებში (25–260 კგ).');
        }
        if (rawCreat && isNaN(creat)) {
            msgs.push('შეამოწმეთ კრეატინინი: შეიყვანეთ მხოლოდ რიცხვი!');
        } else if (!isNaN(creat) && (creat < 0.1 || creat > 3000)) {
            msgs.push('დაუჯერებელი კრეატინინი (' + creat + ') — გადაამოწმეთ მნიშვნელობა და ერთეული!');
        } else if (!isNaN(creat) && ((!cunit && creat > 15 && creat < 35) || (cunit === 'umol' && creat < 15) || (cunit === 'mgdl' && creat > 35))) {
            msgs.push('კრეატინინი (' + creat + ') და არჩეული ერთეული არ ემთხვევა — აირჩიეთ სწორი ერთეული (µmol/L ან mg/dL).');
        }
        var rawHr = ((document.getElementById('nw-hr') || {}).value || '').trim();
        var rawSpo2 = ((document.getElementById('nw-spo2') || {}).value || '').trim();
        var rawHt = ((document.getElementById('nw-ht') || {}).value || '').trim();
        var rawPlt = ((document.getElementById('nw-plt') || {}).value || '').trim();
        var rawInr = ((document.getElementById('nw-inr') || {}).value || '').trim();
        var rawK = ((document.getElementById('nw-k') || {}).value || '').trim();
        var rawGl = ((document.getElementById('nw-gl') || {}).value || '').trim();
        var hr = parseNum(rawHr);
        var spo2 = parseNum(rawSpo2);
        var ht = parseNum(rawHt);
        var plt = parseNum(rawPlt);
        var inr = parseNum(rawInr);
        var kVal = parseNum(rawK);
        var gl = parseNum(rawGl);

        if (rawHb && isNaN(hb)) {
            msgs.push('შეამოწმეთ ჰემოგლობინი: შეიყვანეთ მხოლოდ რიცხვი!');
        } else if (!isNaN(hb) && (hb < 3.5 || (hb > 22 && hb < 35) || hb > 260)) {
            msgs.push('შეამოწმეთ ჰემოგლობინი (' + hb + ') — მიუთითეთ გ/ლ (35–260) ან გ/დლ (3,5–22,0).');
        } else if (!isNaN(hb)) {
            var hbGl = hb <= 22 ? Math.round(hb * 100) / 10 : hb;
            if (hbGl < 100) msgs.push('მძიმე ანემია (Hb ' + hbGl + ' გ/ლ < 100) — აცნობეთ ექიმს!');
            else if (hbGl < 115) msgs.push('ანემია (Hb ' + hbGl + ' გ/ლ < 115 — ჩაირთვება რკინის პანელი).');
        }
        if (rawHr && isNaN(hr)) {
            msgs.push('შეამოწმეთ პულსი: შეიყვანეთ მხოლოდ რიცხვი!');
        } else if (!isNaN(hr) && (hr < 15 || hr > 260)) {
            msgs.push('დაუჯერებელი პულსი (' + hr + '/წთ — 15–260)!');
        } else if (!isNaN(hr) && (hr < 45 || hr > 110)) {
            msgs.push('კრიტიკული პულსი (' + hr + '/წთ) — აცნობეთ ექიმს!');
        }
        if (rawSpo2 && isNaN(spo2)) {
            msgs.push('შეამოწმეთ SpO2: შეიყვანეთ მხოლოდ რიცხვი!');
        } else if (!isNaN(spo2) && (spo2 < 30 || spo2 > 100)) {
            msgs.push('დაუჯერებელი სატურაცია (' + spo2 + '% — 30–100%)!');
        } else if (!isNaN(spo2) && spo2 < 90) {
            msgs.push('მძიმე ჰიპოქსემია (SpO2 ' + spo2 + '% < 90%) — სასწრაფოდ აცნობეთ ექიმს!');
        } else if (!isNaN(spo2) && spo2 < 94) {
            msgs.push('დაქვეითებული სატურაცია (SpO2 ' + spo2 + '% < 94%) — გადაამოწმეთ.');
        }
        if (rawHt && isNaN(ht)) {
            msgs.push('შეამოწმეთ სიმაღლე: შეიყვანეთ მხოლოდ რიცხვი სანტიმეტრებში.');
        } else if (!isNaN(ht) && (ht < 100 || ht > 230)) {
            msgs.push('შეამოწმეთ სიმაღლე (' + ht + ' სმ — 100–230 სმ).');
        }
        if (rawPlt && isNaN(plt)) {
            msgs.push('შეამოწმეთ თრომბოციტები (PLT): შეიყვანეთ მხოლოდ რიცხვი!');
        } else if (!isNaN(plt) && (plt < 1 || plt > 2000000)) {
            msgs.push('დაუჯერებელი PLT (' + plt + ') — გადაამოწმეთ მნიშვნელობა!');
        } else if (!isNaN(plt) && plt > 2500 && plt < 3000) {
            msgs.push('დააზუსტეთ PLT ერთეული (' + plt + ' — ×10^9/L თუ /µL)!');
        } else if (!isNaN(plt)) {
            var pltK = plt >= 3000 ? Math.round(plt / 1000) : plt;
            if (pltK < 100) msgs.push('თრომბოციტოპენია (PLT ' + pltK + ' < 100 ×10^9/L) — აცნობეთ ექიმს!');
            else if (pltK < 150) msgs.push('ზომიერი თრომბოციტოპენია (PLT ' + pltK + ' < 150 ×10^9/L).');
        }
        if (rawInr && isNaN(inr)) {
            msgs.push('შეამოწმეთ INR: შეიყვანეთ მხოლოდ რიცხვი!');
        } else if (!isNaN(inr) && (inr < 0.4 || inr > 20.0)) {
            msgs.push('დაუჯერებელი INR (' + inr + ') — გადაამოწმეთ!');
        } else if (!isNaN(inr) && inr > 1.5) {
            msgs.push('მაღალი INR (' + inr + ' > 1.5 — სისხლდენის რისკი) — აცნობეთ ექიმს!');
        } else if (!isNaN(inr) && inr > 1.35) {
            msgs.push('მომატებული INR (' + inr + ' > 1.35 — გადაამოწმეთ ექიმთან).');
        }
        if (rawK && isNaN(kVal)) {
            msgs.push('შეამოწმეთ კალიუმი (K+): შეიყვანეთ მხოლოდ რიცხვი!');
        } else if (!isNaN(kVal) && (kVal < 1.0 || kVal > 12.0)) {
            msgs.push('დაუჯერებელი K+ (' + kVal + ' მმოლ/ლ) — გადაამოწმეთ!');
        } else if (!isNaN(kVal) && (kVal < 3.3 || kVal >= 5.4)) {
            msgs.push('კრიტიკული კალიუმი (K+ ' + kVal + ' მმოლ/ლ — არითმიის რისკი) — აცნობეთ ექიმს!');
        } else if (!isNaN(kVal) && (kVal < 3.5 || kVal > 5.0)) {
            msgs.push('სასაზღვრო K+ (' + kVal + ' მმოლ/ლ — ნორმა 3.5–5.0).');
        }
        if (rawGl && isNaN(gl)) {
            msgs.push('შეამოწმეთ შაქარი (გლუკოზა): შეიყვანეთ მხოლოდ რიცხვი!');
        } else if (!isNaN(gl) && (gl < 0.5 || gl > 1000)) {
            msgs.push('დაუჯერებელი გლუკოზა (' + gl + ') — გადაამოწმეთ!');
        } else if (!isNaN(gl)) {
            var glM = gl > 35 ? Math.round((gl / 18.0) * 100) / 100 : gl;
            if (glM < 3.9 || glM >= 14.0) msgs.push('კრიტიკული გლიკემია (' + glM + ' მმოლ/ლ) — აცნობეთ ექიმს!');
            else if (glM >= 7.0) msgs.push('მომატებული გლუკოზა (' + glM + ' მმოლ/ლ — ჩაირთვება HbA1c).');
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

    var dosForm = document.getElementById('nw-dos-form');
    var dosGuard = document.getElementById('nw-dos-live-guard');
    if (dosForm && !dosGuard) {
        dosGuard = document.createElement('div');
        dosGuard.id = 'nw-dos-live-guard';
        dosGuard.className = 'nw-guard-box';
        dosGuard.hidden = true;
        dosForm.appendChild(dosGuard);
    }
    function runDosGuards() {
        if (!dosForm || !dosGuard) return;
        var msgs = [];
        var rawS = ((document.getElementById('nw-dos-sbp') || dosForm.querySelector('input[name="dos_bp_sys"]') || {}).value || '').trim();
        var rawD = ((document.getElementById('nw-dos-dbp') || dosForm.querySelector('input[name="dos_bp_dia"]') || {}).value || '').trim();
        var rawG = ((document.getElementById('nw-dos-gl') || dosForm.querySelector('input[name="dos_glucose"]') || {}).value || '').trim();
        var rawI = ((document.getElementById('nw-dos-inr') || dosForm.querySelector('input[name="dos_inr"]') || {}).value || '').trim();
        var s = parseNum(rawS), d = parseNum(rawD), g = parseNum(rawG), i = parseNum(rawI);
        if ((rawS && isNaN(s)) || (rawD && isNaN(d))) msgs.push('შეამოწმეთ დილის წნევა: შეიყვანეთ მხოლოდ რიცხვები!');
        if (!isNaN(s) && (s < 40 || s > 300)) msgs.push('დაუჯერებელი დილის ზედა წნევა (' + s + ' mmHg — 40–300)!');
        else if (!isNaN(s) && (s >= 180 || s < 90)) msgs.push('კრიტიკული დილის ზედა წნევა (' + s + ' mmHg) — შეაჩერეთ და აცნობეთ ექიმს!');
        else if (!isNaN(s) && s >= 160) msgs.push('მომატებული დილის ზედა წნევა (' + s + ' ≥ 160 mmHg).');
        if (!isNaN(d) && (d < 20 || d > 200)) msgs.push('დაუჯერებელი დილის ქვედა წნევა (' + d + ' mmHg — 20–200)!');
        else if (!isNaN(d) && d >= 110) msgs.push('კრიტიკული დილის ქვედა წნევა (' + d + ' ≥ 110 mmHg) — აცნობეთ ექიმს!');
        else if (!isNaN(d) && d >= 100) msgs.push('მომატებული დილის ქვედა წნევა (' + d + ' ≥ 100 mmHg).');
        if (!isNaN(s) && !isNaN(d) && s <= d) msgs.push('დილის ზედა წნევა (' + s + ') უნდა იყოს ქვედაზე (' + d + ') მეტი!');
        if (rawG && isNaN(g)) msgs.push('შეამოწმეთ დილის გლუკოზა: შეიყვანეთ მხოლოდ რიცხვი!');
        else if (!isNaN(g) && (g < 0.5 || g > 1000)) msgs.push('დაუჯერებელი დილის გლუკოზა (' + g + ')!');
        else if (!isNaN(g)) {
            var gm = g > 35 ? Math.round((g / 18.0) * 100) / 100 : g;
            if (gm < 3.9 || gm >= 14.0) msgs.push('კრიტიკული დილის გლიკემია (' + gm + ' მმოლ/ლ) — აცნობეთ ექიმს!');
            else if (gm > 10.0) msgs.push('მომატებული დილის გლუკოზა (' + gm + ' > 10.0 მმოლ/ლ — გლიკემიური კონტროლი).');
        }
        if (rawI && isNaN(i)) msgs.push('შეამოწმეთ დილის INR: შეიყვანეთ მხოლოდ რიცხვი!');
        else if (!isNaN(i) && (i < 0.5 || i > 15.0)) msgs.push('დაუჯერებელი დილის INR (' + i + ' — 0.5–15.0)!');
        else if (!isNaN(i) && i > 1.5) msgs.push('მაღალი დილის INR (' + i + ' > 1.5) — სისხლდენის რისკი!');
        else if (!isNaN(i) && i > 1.35) msgs.push('მომატებული დილის INR (' + i + ' > 1.35 — გადაამოწმეთ ASRA ზღვარი).');
        if (msgs.length) {
            dosGuard.hidden = false;
            dosGuard.innerHTML = msgs.map(function (m) { return '<div>' + m + '</div>'; }).join('');
        } else {
            dosGuard.hidden = true;
            dosGuard.innerHTML = '';
        }
    }
    if (dosForm) {
        dosForm.addEventListener('input', runDosGuards);
        dosForm.addEventListener('change', runDosGuards);
        dosForm.addEventListener('submit', function () {
            dosForm.querySelectorAll('.js-comma-num').forEach(function (inp) {
                if (inp.value && inp.value.indexOf(',') >= 0) inp.value = inp.value.trim().replace(/,/g, '.');
            });
        });
        runDosGuards();
    }

    function attachFldGuard(ctrl, evalFn) {
        if (!ctrl || ctrl.closest('#nw-vitals-form') || ctrl.closest('#nw-dos-form')) return;
        var lbl = ctrl.closest('.fld');
        if (!lbl) return;
        var hint = lbl.querySelector('.fld-live-hint');
        if (!hint) {
            hint = document.createElement('div');
            hint.className = 'fld-live-hint';
            hint.hidden = true;
            lbl.appendChild(hint);
        }
        var run = function () {
            ctrl.classList.remove('lv-warn', 'lv-crit');
            var raw = (ctrl.value || '').trim();
            if (!raw) { hint.hidden = true; hint.className = 'fld-live-hint'; hint.textContent = ''; return; }
            var num = parseNum(raw);
            var res = isNaN(num) ? { level: 'crit', msg: 'შეიყვანეთ მხოლოდ რიცხვითი მნიშვნელობა!' } : evalFn(num);
            if (!res) { hint.hidden = true; hint.className = 'fld-live-hint'; hint.textContent = ''; return; }
            ctrl.classList.add(res.level === 'crit' ? 'lv-crit' : 'lv-warn');
            hint.className = 'fld-live-hint ' + res.level;
            hint.textContent = res.msg;
            hint.hidden = false;
        };
        ctrl.addEventListener('input', run);
        ctrl.addEventListener('change', run);
        run();
    }
    document.querySelectorAll('input[name="hb"]').forEach(function (el) {
        attachFldGuard(el, function (v) {
            if (v < 3.5 || v > 260) return { level: 'crit', msg: 'დაუჯერებელი Hb (' + v + ') — გადაამოწმეთ მნიშვნელობა და ერთეული!' };
            if (v > 22.0 && v < 35.0) return { level: 'crit', msg: 'საეჭვო Hb ერთეული (' + v + ') — მიუთითეთ გ/ლ (35–260) ან გ/დლ (3.5–22.0)!' };
            var gl = v <= 22.0 ? Math.round(v * 100) / 10 : v;
            if (gl < 100) return { level: 'crit', msg: 'მძიმე ანემია (' + gl + ' გ/ლ < 100)!' };
            if (gl < 115) return { level: 'warn', msg: 'ანემია (' + gl + ' გ/ლ < 115 — ჩაირთვება ფერიტინი/TSAT)' };
            return null;
        });
    });
    document.querySelectorAll('input[name="k"]').forEach(function (el) {
        attachFldGuard(el, function (v) {
            if (v < 1.0 || v > 12.0) return { level: 'crit', msg: 'დაუჯერებელი K+ (' + v + ' მმოლ/ლ)!' };
            if (v < 3.3 || v >= 5.4) return { level: 'crit', msg: 'კრიტიკული K+ (' + v + ' მმოლ/ლ — არითმიის რისკი)!' };
            if (v < 3.5 || v > 5.0) return { level: 'warn', msg: 'სასაზღვრო K+ (ნორმა 3.5–5.0)' };
            return null;
        });
    });
    document.querySelectorAll('input[name="inr"], input[name="dos_inr"]').forEach(function (el) {
        var isDosInr = el.name === 'dos_inr';
        var inrLo = isDosInr ? 0.5 : 0.4;
        var inrHi = isDosInr ? 15.0 : 20.0;
        attachFldGuard(el, function (v) {
            if (v < inrLo || v > inrHi) return { level: 'crit', msg: 'დაუჯერებელი INR (' + v + ')!' };
            if (v > 1.5) return { level: 'crit', msg: 'მაღალი INR (>1.5 — სისხლდენის რისკი)!' };
            if (v > 1.35) return { level: 'warn', msg: 'მომატებული INR (>1.35 — ASRA ზღვარი ≤ 1.4)' };
            return null;
        });
    });
    document.querySelectorAll('input[name="plt"]').forEach(function (el) {
        attachFldGuard(el, function (v) {
            if (v < 1 || v > 2000000) return { level: 'crit', msg: 'დაუჯერებელი PLT (' + v + ')!' };
            if (v > 2500 && v < 3000) return { level: 'crit', msg: 'დააზუსტეთ PLT ერთეული (×10^9/L თუ /µL)!' };
            var n = v >= 3000 ? Math.round(v / 1000) : v;
            if (n < 100) return { level: 'crit', msg: 'თრომბოციტოპენია (<100 ×10^9/L)!' };
            if (n < 150) return { level: 'warn', msg: 'ზომიერი კლება (<150 ×10^9/L)' };
            return null;
        });
    });
    document.querySelectorAll('input[name="creat"]').forEach(function (el) {
        var unitSel = el.closest('form') ? el.closest('form').querySelector('select[name="creat_unit"]') : null;
        attachFldGuard(el, function (v) {
            var u = unitSel ? unitSel.value : '';
            if (v < 0.1 || v > 3000) return { level: 'crit', msg: 'დაუჯერებელი კრეატინინი (' + v + ')!' };
            if (!u && v > 15 && v < 35) return { level: 'crit', msg: 'აირჩიეთ ერთეული (µmol/L თუ mg/dL)!' };
            if ((u === 'umol' && v < 15) || (u === 'mgdl' && v > 35)) return { level: 'crit', msg: 'კრეატინინის მნიშვნელობა და არჩეული ერთეული არ ემთხვევა!' };
            var mgdl = u === 'mgdl' ? v : (u === 'umol' ? v / 88.4 : (v <= 15 ? v : v / 88.4));
            if (mgdl > 2.0) return { level: 'crit', msg: 'მაღალი კრეატინინი (RCRI +1 / ნეფროლოგი)!' };
            if (mgdl >= 1.4) return { level: 'warn', msg: 'დაქვეითებული თირკმლის ფუნქცია' };
            return null;
        });
        if (unitSel && !el.closest('#nw-vitals-form')) unitSel.addEventListener('change', function () { el.dispatchEvent(new Event('input')); });
    });
    document.querySelectorAll('input[name="glucose"], input[name="dos_glucose"]').forEach(function (el) {
        var isDosGl = el.name === 'dos_glucose';
        attachFldGuard(el, function (v) {
            if (v < 0.5 || v > 1000) return { level: 'crit', msg: 'დაუჯერებელი გლუკოზა (' + v + ')!' };
            var mmol = v > 35 ? Math.round((v / 18.0) * 100) / 100 : v;
            if (mmol >= 14.0 || mmol < 3.9) return { level: 'crit', msg: 'კრიტიკული გლიკემია (' + mmol + ' მმოლ/ლ)!' };
            if (isDosGl) {
                if (mmol > 10.0) return { level: 'warn', msg: 'მომატებული დილის გლუკოზა (>10.0 მმოლ/ლ)' };
            } else if (mmol >= 7.0) {
                return { level: 'warn', msg: 'მომატებული გლუკოზა (ჩაირთვება HbA1c)' };
            }
            return null;
        });
    });
    document.querySelectorAll('input[name="hr"]').forEach(function (el) {
        attachFldGuard(el, function (v) {
            if (v < 15 || v > 260) return { level: 'crit', msg: 'დაუჯერებელი პულსი (' + v + '/წთ — 15–260)!' };
            if (v < 45 || v > 110) return { level: 'crit', msg: 'კრიტიკული პულსი (' + v + '/წთ)!' };
            return null;
        });
    });
    document.querySelectorAll('input[name="spo2"]').forEach(function (el) {
        attachFldGuard(el, function (v) {
            if (v < 30 || v > 100) return { level: 'crit', msg: 'დაუჯერებელი სატურაცია (' + v + '% — 30–100%)!' };
            if (v < 90) return { level: 'crit', msg: 'მძიმე ჰიპოქსემია (SpO2 ' + v + '% < 90%)!' };
            if (v < 94) return { level: 'warn', msg: 'დაქვეითებული სატურაცია (SpO2 ' + v + '% < 94%)' };
            return null;
        });
    });
    document.querySelectorAll('input[name="weight_kg"]').forEach(function (el) {
        attachFldGuard(el, function (v) {
            if (v < 25 || v > 260) return { level: 'crit', msg: 'დაუჯერებელი წონა (' + v + ' კგ — 25–260)!' };
            return null;
        });
    });
    document.querySelectorAll('input[name="height_cm"]').forEach(function (el) {
        attachFldGuard(el, function (v) {
            if (v < 100 || v > 230) return { level: 'crit', msg: 'დაუჯერებელი სიმაღლე (' + v + ' სმ — 100–230)!' };
            return null;
        });
    });
    document.querySelectorAll('input[name="wbc"]').forEach(function (el) {
        attachFldGuard(el, function (v) {
            if (v < 0.1 || v > 150000) return { level: 'crit', msg: 'დაუჯერებელი WBC (' + v + ')!' };
            var n = v >= 200 ? Math.round((v / 1000.0) * 100) / 100 : v;
            if (n < 3.5 || n > 12.0) return { level: 'crit', msg: 'პათოლოგიური WBC (' + n + ' ×10^9/L)!' };
            return null;
        });
    });
    document.querySelectorAll('input[name="albumin"]').forEach(function (el) {
        attachFldGuard(el, function (v) {
            if (v < 0.5 || v > 80) return { level: 'crit', msg: 'დაუჯერებელი ალბუმინი (' + v + ')!' };
            var gl = (v >= 0.5 && v <= 7.5) ? Math.round(v * 100) / 10 : v;
            if (gl < 30) return { level: 'crit', msg: 'მძიმე ჰიპოალბუმინემია (' + gl + ' გ/ლ < 30)!' };
            if (gl < 35) return { level: 'warn', msg: 'დაქვეითებული ალბუმინი (' + gl + ' გ/ლ < 35)' };
            return null;
        });
    });
    var NW_BP_DIA = { bp_sys: 'bp_dia', bp_sys_l: 'bp_dia_l', dos_bp_sys: 'dos_bp_dia' };
    var NW_BP_SYS = { bp_dia: 'bp_sys', bp_dia_l: 'bp_sys_l', dos_bp_dia: 'dos_bp_sys' };
    document.querySelectorAll('input[name="bp_sys"], input[name="bp_sys_l"], input[name="dos_bp_sys"]').forEach(function (el) {
        var diaName = NW_BP_DIA[el.name];
        var diaEl = el.closest('form') && diaName ? el.closest('form').querySelector('input[name="' + diaName + '"]') : null;
        var sbpLo = el.name === 'dos_bp_sys' ? 40 : 30;
        var sbpHi = el.name === 'dos_bp_sys' ? 300 : 320;
        attachFldGuard(el, function (v) {
            if (v < sbpLo || v > sbpHi) return { level: 'crit', msg: 'დაუჯერებელი სისტოლური წნევა (' + v + ' mmHg)!' };
            if (diaEl) {
                var dv = parseNum(diaEl.value);
                if (!isNaN(dv) && v <= dv) return { level: 'crit', msg: 'სისტოლური (' + v + ') უნდა იყოს დიასტოლურზე (' + dv + ') მეტი!' };
            }
            if (v >= 180 || v < 90) return { level: 'crit', msg: v >= 180 ? 'კრიტიკული ჰიპერტენზია (≥180 mmHg)!' : 'ჰიპოტენზია (<90 mmHg)!' };
            if (v >= 160) return { level: 'warn', msg: 'მომატებული წნევა (≥160 mmHg)' };
            return null;
        });
        if (diaEl && !el.closest('#nw-vitals-form') && !el.closest('#nw-dos-form')) diaEl.addEventListener('input', function () { el.dispatchEvent(new Event('change')); });
    });
    document.querySelectorAll('input[name="bp_dia"], input[name="bp_dia_l"], input[name="dos_bp_dia"]').forEach(function (el) {
        var sysName = NW_BP_SYS[el.name];
        var sysEl = el.closest('form') && sysName ? el.closest('form').querySelector('input[name="' + sysName + '"]') : null;
        var dbpHi = el.name === 'dos_bp_dia' ? 200 : 220;
        attachFldGuard(el, function (v) {
            if (v < 20 || v > dbpHi) return { level: 'crit', msg: 'დაუჯერებელი დიასტოლური წნევა (' + v + ' mmHg)!' };
            if (sysEl) {
                var sv = parseNum(sysEl.value);
                if (!isNaN(sv) && sv <= v) return { level: 'crit', msg: 'დიასტოლური (' + v + ') არ შეიძლება აღემატებოდეს სისტოლურს (' + sv + ')!' };
            }
            if (v >= 110) return { level: 'crit', msg: 'კრიტიკული დიასტოლური წნევა (≥110 mmHg)!' };
            if (v >= 100) return { level: 'warn', msg: 'მომატებული დიასტოლური წნევა (≥100 mmHg)' };
            return null;
        });
        if (sysEl && !el.closest('#nw-vitals-form') && !el.closest('#nw-dos-form')) sysEl.addEventListener('input', function () { el.dispatchEvent(new Event('change')); });
    });
    document.querySelectorAll('input[name="fibrinogen"]').forEach(function (el) {
        attachFldGuard(el, function (v) {
            if (v < 0.15 || v > 1500) return { level: 'crit', msg: 'დაუჯერებელი ფიბრინოგენი (' + v + ')!' };
            var gl = v > 15 ? Math.round((v / 100.0) * 100) / 100 : v;
            if (gl < 1.5) return { level: 'crit', msg: 'მძიმე ჰიპოფიბრინოგენემია (' + gl + ' გ/ლ < 1.5)!' };
            if (gl < 1.8) return { level: 'warn', msg: 'დაბალი ფიბრინოგენი (' + gl + ' გ/ლ < 1.8)' };
            return null;
        });
    });
    document.querySelectorAll('input[name="ldl"]').forEach(function (el) {
        attachFldGuard(el, function (v) {
            if (v < 0.2 || v > 500) return { level: 'crit', msg: 'დაუჯერებელი LDL (' + v + ')!' };
            var mmol = v > 20 ? Math.round((v / 38.67) * 100) / 100 : v;
            if (mmol >= 1.4) return { level: 'warn', msg: 'LDL ' + mmol + ' მმოლ/ლ (სამიზნე <1.4)' };
            return null;
        });
    });
    document.querySelectorAll('input[name="abi_r"], input[name="abi_l"]').forEach(function (el) {
        attachFldGuard(el, function (v) {
            if (v < 0 || v > 3.0) return { level: 'crit', msg: 'დაუჯერებელი ABI (' + v + ')!' };
            if (v > 1.40) return { level: 'warn', msg: 'არაკომპრესირებადი (>1.40 — შეიტანეთ TP/TBI)' };
            if (v < 0.40) return { level: 'crit', msg: 'კრიტიკული იშემია (ABI < 0.40)!' };
            if (v < 0.80) return { level: 'warn', msg: 'დაქვეითებული ABI (< 0.80)' };
            return null;
        });
    });

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

