/* Staging page: mark artery segments, manage structured prior limb interventions,
 * and let the server recompute GLASS / WIfI / PLAN on every change (tables live only in staging.py). */
(function () {
    'use strict';
    var form = document.getElementById('st-form');
    if (!form) { return; }

    function readJson(id, fallback) {
        var el = document.getElementById(id);
        if (!el) { return fallback; }
        try { return JSON.parse(el.textContent) || fallback; } catch (e) { return fallback; }
    }

    var segments = readJson('st-segments', {});
    var interventions = readJson('st-interventions', []);
    var catalog = readJson('st-iv-catalog', null);
    var hiddenSegs = form.querySelector('input[name=segments]');
    var hiddenIvs = form.querySelector('input[name=interventions]');
    var tool = { state: 'oc', flag: null };

    var INLINE_TYPES = ['stent', 'angioplasty', 'endarterectomy', 'thrombectomy'];
    var stateToV2 = { mild: 'mild', st: 'severe', oc: 'occlusion' };

    function sync() {
        if (hiddenSegs) { hiddenSegs.value = JSON.stringify(segments); }
        if (hiddenIvs) { hiddenIvs.value = JSON.stringify(interventions); }
    }

    function stateCode(item) {
        var st = item.stenosis || item.s || 'none';
        if (st === 'occlusion' || st === 'oc') { return 'oc'; }
        if (st === 'severe' || st === 'moderate' || st === 'st') { return 'st'; }
        if (st === 'mild') { return 'mild'; }
        return 'n';
    }

    function segmentPath(proximal, distal) {
        if (!catalog || !catalog.segment_next) { return [distal]; }
        var known = catalog.segment_next;
        if (!Object.prototype.hasOwnProperty.call(known, distal)) { return []; }
        if (!Object.prototype.hasOwnProperty.call(known, proximal)) { return [distal]; }
        if (proximal === distal) { return [proximal]; }
        var queue = [[proximal]];
        var seen = {};
        seen[proximal] = true;
        while (queue.length) {
            var path = queue.shift();
            var nxtList = known[path[path.length - 1]] || [];
            for (var i = 0; i < nxtList.length; i++) {
                var nxt = nxtList[i];
                if (nxt === distal) { return path.concat([nxt]); }
                if (!seen[nxt]) {
                    seen[nxt] = true;
                    queue.push(path.concat([nxt]));
                }
            }
        }
        return [proximal, distal];
    }

    function activeInterventionMarks() {
        var rx = {}, by = {};
        interventions.forEach(function (iv) {
            if (!iv) { return; }
            if (INLINE_TYPES.indexOf(iv.type) !== -1 && Array.isArray(iv.segments)) {
                iv.segments.forEach(function (s) { rx[s] = true; });
            } else if (iv.type === 'bypass') {
                segmentPath(iv.proximal, iv.distal).forEach(function (s) { by[s] = true; });
            }
        });
        return { rx: rx, by: by };
    }

    var formDirty = false;
    var isSubmitting = false;
    var allowLeave = false;
    var unsavedEl = document.getElementById('st-unsaved');
    var inspectedKey = null;
    var moreExpanded = false;
    var L2_FLAGS = ['coral', 'thrombus', 'aneurysm', 'ulcerated', 'small_caliber'];

    function markFormDirty() {
        formDirty = true;
        if (unsavedEl) { unsavedEl.hidden = false; }
    }

    function hasL2Details(item) {
        if (!item) { return false; }
        if (item.morph) { return true; }
        return L2_FLAGS.some(function (f) { return !!item[f]; });
    }

    function hasRichDetails(item) {
        if (!item) { return false; }
        if (item.length_cm != null || item.flush || item.reconstitution || item.trifurcation) { return true; }
        if (item.calc === 1 || item.calc === 2 || item.calc === 4) { return true; }
        return hasL2Details(item);
    }

    function paintAll(markDirty) {
        var marks = activeInterventionMarks();
        form.querySelectorAll('.am-edit [data-seg]').forEach(function (g) {
            var key = g.getAttribute('data-seg');
            var item = segments[key] || {};
            var css = ['sg', 'sg-' + stateCode(item)];
            if ((item.calc || 0) >= 3 || item.coral || item.ca) { css.push('sg-ca'); }
            if (item.rx || marks.rx[key]) { css.push('sg-rx'); }
            if (item.by || marks.by[key]) { css.push('sg-by'); }
            if (inspectedKey && key === inspectedKey) { css.push('sg-selected'); }
            if (markDirty) { css.push('am-dirty'); }
            g.setAttribute('class', css.join(' '));
        });
    }

    function highlightInspected() {
        form.querySelectorAll('.am-edit [data-seg]').forEach(function (g) {
            g.classList.toggle('sg-selected', !!inspectedKey && g.getAttribute('data-seg') === inspectedKey);
        });
    }

    form.querySelectorAll('.st-tool').forEach(function (b) {
        b.addEventListener('click', function () {
            form.querySelectorAll('.st-tool').forEach(function (x) { x.classList.remove('on'); });
            b.classList.add('on');
            tool = {
                state: b.getAttribute('data-state'),
                flag: b.getAttribute('data-flag'),
                inspect: b.hasAttribute('data-inspect')
            };
        });
    });

    // ---- Two-level segment inspector (+ მორფოლოგია) -----------------------

    var inspector = document.getElementById('st-seg-inspector');
    var insTitle = document.getElementById('st-ins-title');
    var insShared = document.getElementById('st-ins-shared');
    var insReset = document.getElementById('st-ins-reset');
    var insClose = document.getElementById('st-ins-close');
    var insStenosis = document.getElementById('st-ins-stenosis');
    var insLenChips = document.getElementById('st-ins-len-chips');
    var insLenInput = document.getElementById('st-ins-len');
    var insPacss = document.getElementById('st-ins-pacss');
    var insCto = document.getElementById('st-ins-cto');
    var insFlush = document.getElementById('st-ins-flush');
    var insReconst = document.getElementById('st-ins-reconst');
    var insTrifWrap = document.getElementById('st-ins-trif-wrap');
    var insTrif = document.getElementById('st-ins-trif');
    var insMoreBtn = document.getElementById('st-ins-more-btn');
    var insMore = document.getElementById('st-ins-more');
    var insMorph = document.getElementById('st-ins-morph');
    var insFlags = document.getElementById('st-ins-flags');

    function canonicalStenosis(item) {
        if (!item) { return 'none'; }
        if (item.stenosis) { return item.stenosis; }
        if (item.s && stateToV2[item.s]) { return stateToV2[item.s]; }
        return 'none';
    }

    function renderInspector() {
        if (!inspector) { return; }
        if (!inspectedKey) {
            inspector.hidden = true;
            highlightInspected();
            return;
        }
        inspector.hidden = false;
        var shortMap = (catalog && catalog.segment_short) || {};
        var nameMap = (catalog && catalog.segment_names) || {};
        var sShort = shortMap[inspectedKey] || '';
        var sName = nameMap[inspectedKey] || inspectedKey;
        if (insTitle) {
            insTitle.textContent = (sShort && sShort !== sName) ? (sShort + ' — ' + sName) : sName;
        }
        if (insShared) { insShared.hidden = inspectedKey !== 'aorta'; }

        var item = segments[inspectedKey] || {};
        var st = canonicalStenosis(item);

        if (insStenosis) {
            insStenosis.querySelectorAll('[data-ins-stenosis]').forEach(function (b) {
                var on = b.getAttribute('data-ins-stenosis') === st;
                b.classList.toggle('on', on);
                b.setAttribute('aria-pressed', on ? 'true' : 'false');
            });
        }

        var lenVal = item.length_cm != null ? Number(item.length_cm) : null;
        if (insLenChips) {
            insLenChips.querySelectorAll('[data-ins-len]').forEach(function (b) {
                var chipVal = Number(b.getAttribute('data-ins-len'));
                var on = lenVal !== null && Math.abs(chipVal - lenVal) < 0.05;
                b.classList.toggle('on', on);
                b.setAttribute('aria-pressed', on ? 'true' : 'false');
            });
        }
        if (insLenInput && document.activeElement !== insLenInput) {
            insLenInput.value = lenVal !== null ? String(lenVal) : '';
        }

        var calcVal = item.calc != null ? Number(item.calc) : (item.ca ? 3 : 0);
        if (insPacss) {
            insPacss.querySelectorAll('[data-ins-calc]').forEach(function (b) {
                var on = Number(b.getAttribute('data-ins-calc')) === calcVal;
                b.classList.toggle('on', on);
                b.setAttribute('aria-pressed', on ? 'true' : 'false');
            });
        }

        if (insCto) { insCto.hidden = st !== 'occlusion'; }
        if (insFlush) { insFlush.checked = !!item.flush; }
        if (insReconst) {
            var rec = item.reconstitution || '';
            insReconst.querySelectorAll('[data-ins-reconst]').forEach(function (b) {
                var on = (b.getAttribute('data-ins-reconst') || '') === rec;
                b.classList.toggle('on', on);
                b.setAttribute('aria-pressed', on ? 'true' : 'false');
            });
        }

        if (insTrifWrap) { insTrifWrap.hidden = inspectedKey !== 'pop3'; }
        if (insTrif) { insTrif.checked = !!item.trifurcation; }

        var l2Count = (item.morph ? 1 : 0) + L2_FLAGS.filter(function (f) { return !!item[f]; }).length;
        if (insMore) { insMore.hidden = !moreExpanded; }
        if (insMoreBtn) {
            insMoreBtn.setAttribute('aria-expanded', moreExpanded ? 'true' : 'false');
            insMoreBtn.textContent = (moreExpanded ? '− მორფოლოგია' : '+ მორფოლოგია') + (l2Count ? ' (' + l2Count + ')' : '');
        }
        if (insMorph) {
            var m = item.morph || '';
            insMorph.querySelectorAll('[data-ins-morph]').forEach(function (b) {
                var on = (b.getAttribute('data-ins-morph') || '') === m;
                b.classList.toggle('on', on);
                b.setAttribute('aria-pressed', on ? 'true' : 'false');
            });
        }
        if (insFlags) {
            insFlags.querySelectorAll('[data-ins-flag]').forEach(function (b) {
                var fk = b.getAttribute('data-ins-flag');
                var on = !!item[fk];
                b.classList.toggle('on', on);
                b.setAttribute('aria-pressed', on ? 'true' : 'false');
            });
        }

        highlightInspected();
    }

    function openInspector(key) {
        if (inspectedKey !== key) {
            inspectedKey = key;
            moreExpanded = hasL2Details(segments[key] || {});
        }
        renderInspector();
    }

    function cleanSegmentItem(item) {
        delete item.s;
        delete item.ca;
        var st = item.stenosis || 'none';
        if (st === 'none') {
            delete item.stenosis;
            delete item.flush;
            delete item.reconstitution;
            delete item.morph;
            if (!item.aneurysm) { delete item.length_cm; }
        } else if (st !== 'occlusion') {
            delete item.flush;
            delete item.reconstitution;
        }
        if (!item.calc) { delete item.calc; }
        if (!item.reconstitution) { delete item.reconstitution; }
        if (!item.morph) { delete item.morph; }
        return item;
    }

    function mutateInspected(mutator) {
        if (!inspectedKey) { return; }
        var item = Object.assign({}, segments[inspectedKey] || {});
        item.stenosis = canonicalStenosis(item);
        if (item.ca && item.calc == null) { item.calc = 3; }
        mutator(item);
        cleanSegmentItem(item);
        if (Object.keys(item).length) { segments[inspectedKey] = item; } else { delete segments[inspectedKey]; }
        markFormDirty();
        paintAll(true);
        renderInspector();
        sync();
        preview(true);
    }

    if (insClose) {
        insClose.addEventListener('click', function () {
            inspectedKey = null;
            renderInspector();
        });
    }
    if (insReset) {
        insReset.addEventListener('click', function () {
            if (!inspectedKey) { return; }
            delete segments[inspectedKey];
            markFormDirty();
            paintAll(true);
            renderInspector();
            sync();
            preview(true);
        });
    }
    if (insStenosis) {
        insStenosis.addEventListener('click', function (e) {
            var b = e.target.closest('[data-ins-stenosis]');
            if (!b) { return; }
            mutateInspected(function (item) {
                item.stenosis = b.getAttribute('data-ins-stenosis') || 'none';
            });
        });
    }
    if (insLenChips) {
        insLenChips.addEventListener('click', function (e) {
            var b = e.target.closest('[data-ins-len]');
            if (!b) { return; }
            var val = Number(b.getAttribute('data-ins-len'));
            mutateInspected(function (item) {
                if ((item.stenosis || 'none') === 'none' && !item.aneurysm) {
                    item.stenosis = 'severe';
                }
                if (item.length_cm != null && Math.abs(Number(item.length_cm) - val) < 0.05) {
                    delete item.length_cm;
                } else {
                    item.length_cm = val;
                }
            });
        });
    }
    if (insLenInput) {
        var applyTypedLen = function () {
            var raw = String(insLenInput.value || '').trim().replace(',', '.');
            var num = raw ? parseFloat(raw) : NaN;
            mutateInspected(function (item) {
                if (!isNaN(num) && num >= 0.1 && num <= 50) {
                    if ((item.stenosis || 'none') === 'none' && !item.aneurysm) {
                        item.stenosis = 'severe';
                    }
                    item.length_cm = Math.round(num * 10) / 10;
                } else if (!raw) {
                    delete item.length_cm;
                }
            });
        };
        insLenInput.addEventListener('input', applyTypedLen);
        insLenInput.addEventListener('change', applyTypedLen);
    }
    if (insPacss) {
        insPacss.addEventListener('click', function (e) {
            var b = e.target.closest('[data-ins-calc]');
            if (!b) { return; }
            var val = Number(b.getAttribute('data-ins-calc')) || 0;
            mutateInspected(function (item) { item.calc = val; });
        });
    }
    if (insFlush) {
        insFlush.addEventListener('change', function () {
            mutateInspected(function (item) {
                if (insFlush.checked) { item.flush = true; } else { delete item.flush; }
            });
        });
    }
    if (insReconst) {
        insReconst.addEventListener('click', function (e) {
            var b = e.target.closest('[data-ins-reconst]');
            if (!b) { return; }
            mutateInspected(function (item) {
                item.reconstitution = b.getAttribute('data-ins-reconst') || '';
            });
        });
    }
    if (insTrif) {
        insTrif.addEventListener('change', function () {
            mutateInspected(function (item) {
                if (insTrif.checked) { item.trifurcation = true; } else { delete item.trifurcation; }
            });
        });
    }
    if (insMoreBtn) {
        insMoreBtn.addEventListener('click', function () {
            moreExpanded = !moreExpanded;
            renderInspector();
        });
    }
    if (insMorph) {
        insMorph.addEventListener('click', function (e) {
            var b = e.target.closest('[data-ins-morph]');
            if (!b) { return; }
            var m = b.getAttribute('data-ins-morph') || '';
            mutateInspected(function (item) {
                if (m && (item.stenosis || 'none') === 'none') { item.stenosis = 'severe'; }
                item.morph = m;
            });
        });
    }
    if (insFlags) {
        insFlags.addEventListener('click', function (e) {
            var b = e.target.closest('[data-ins-flag]');
            if (!b) { return; }
            var fk = b.getAttribute('data-ins-flag');
            mutateInspected(function (item) {
                if (item[fk]) { delete item[fk]; } else { item[fk] = true; }
            });
        });
    }

    function apply(g) {
        var key = g.getAttribute('data-seg');
        if (tool.inspect) {
            openInspector(key);
            return;
        }
        var item = Object.assign({}, segments[key] || {});
        if (tool.state && tool.state !== 'n' && stateCode(item) === tool.state && inspectedKey !== key && hasRichDetails(item)) {
            openInspector(key);
            return;
        }
        delete item.s;
        delete item.ca;
        if (tool.flag === 'ca') {
            if ((item.calc || 0) >= 3) { delete item.calc; } else { item.calc = 3; }
        } else if (tool.flag) {
            item[tool.flag] = !item[tool.flag];
            if (!item[tool.flag]) { delete item[tool.flag]; }
        } else if (tool.state) {
            var cur = stateCode(item);
            if (cur === tool.state || tool.state === 'n') {
                delete item.stenosis;
                delete item.flush;
                delete item.reconstitution;
                delete item.length_cm;
                delete item.morph;
            } else {
                item.stenosis = stateToV2[tool.state];
                if (item.stenosis !== 'occlusion') {
                    delete item.flush;
                    delete item.reconstitution;
                }
            }
        }
        if (Object.keys(item).length) { segments[key] = item; } else { delete segments[key]; }
        markFormDirty();
        openInspector(key);
        paintAll(true);
        sync();
        preview(true);
    }

    var mapCard = form.querySelector('.st-map-card');
    var mapBox = form.querySelector('.st-map');
    var footBox = form.querySelector('.st-foot-map');
    var currentZoom = 'all';
    var currentViewBox = null;

    function setMapView(mode) {
        if (!mapCard) { return; }
        var dsa = mode === 'dsa';
        mapCard.classList.toggle('am-dsa-mode', dsa);
        mapCard.querySelectorAll('[data-map-view]').forEach(function (b) {
            var on = b.getAttribute('data-map-view') === (dsa ? 'dsa' : 'anat');
            b.classList.toggle('on', on);
            b.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
        try { window.localStorage.setItem('cases-map-view', dsa ? 'dsa' : 'anat'); } catch (e) {}
    }

    if (mapCard) {
        mapCard.querySelectorAll('[data-map-view]').forEach(function (b) {
            b.addEventListener('click', function () { setMapView(b.getAttribute('data-map-view')); });
        });
        try {
            if (window.localStorage.getItem('cases-map-view') === 'dsa') { setMapView('dsa'); }
        } catch (e) {}
    }

    function applyZoom() {
        if (!mapBox) { return; }
        var svg = mapBox.querySelector('svg.am');
        if (!svg) { return; }
        if (currentViewBox) { svg.setAttribute('viewBox', currentViewBox); }
        svg.classList.toggle('am-zoomed', currentZoom !== 'all');
    }

    form.querySelectorAll('.st-zoom-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
            form.querySelectorAll('.st-zoom-btn').forEach(function (b) { b.classList.remove('on'); });
            btn.classList.add('on');
            currentZoom = btn.getAttribute('data-zoom') || 'all';
            currentViewBox = btn.getAttribute('data-viewbox');
            applyZoom();
        });
    });

    var segHost = mapCard || mapBox;
    if (segHost) {
        segHost.addEventListener('click', function (e) {
            var g = e.target.closest('.am-edit [data-seg]');
            if (g) { apply(g); }
        });
        segHost.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ') {
                var g = e.target.closest('.am-edit [data-seg]');
                if (g) { e.preventDefault(); apply(g); }
            }
        });
    }

    // ---- Structured prior interventions (6 categories) --------------------

    var addBtn = document.getElementById('st-iv-add');
    var emptyHint = document.getElementById('st-iv-empty');
    var listEl = document.getElementById('st-iv-list');
    var editor = document.getElementById('st-iv-editor');

    var editingId = null;
    var editorDirty = false;
    var currentType = 'bypass';
    var selectedSegs = {};

    function pairMap(pairs) {
        var m = {};
        (pairs || []).forEach(function (p) { m[p[0]] = p[1]; });
        return m;
    }

    function normalizePartialDate(raw) {
        var s = String(raw || '').trim().replace(/\//g, '.');
        if (!s) { return { ok: true, iso: '' }; }
        if (s.indexOf('.') !== -1) {
            var dots = s.split('.');
            if (dots.length === 2 && /^\d{1,2}$/.test(dots[0]) && /^\d{4}$/.test(dots[1])) {
                s = dots[1] + '-' + dots[0].padStart(2, '0');
            } else if (dots.length === 3 && /^\d{1,2}$/.test(dots[0]) && /^\d{1,2}$/.test(dots[1]) && /^\d{4}$/.test(dots[2])) {
                s = dots[2] + '-' + dots[1].padStart(2, '0') + '-' + dots[0].padStart(2, '0');
            } else {
                return { ok: false, iso: '' };
            }
        }
        var now = new Date();
        var cy = now.getFullYear(), cm = now.getMonth() + 1, cd = now.getDate();
        var parts = s.split('-');
        if (parts.length === 1 && /^\d{4}$/.test(parts[0])) {
            var y1 = parseInt(parts[0], 10);
            return (y1 >= 1950 && y1 <= cy) ? { ok: true, iso: parts[0] } : { ok: false, iso: '' };
        }
        if (parts.length === 2 && /^\d{4}$/.test(parts[0]) && /^\d{1,2}$/.test(parts[1])) {
            var y2 = parseInt(parts[0], 10), m2 = parseInt(parts[1], 10);
            if (y2 >= 1950 && m2 >= 1 && m2 <= 12 && (y2 < cy || (y2 === cy && m2 <= cm))) {
                return { ok: true, iso: parts[0] + '-' + String(m2).padStart(2, '0') };
            }
            return { ok: false, iso: '' };
        }
        if (parts.length === 3 && /^\d{4}$/.test(parts[0]) && /^\d{1,2}$/.test(parts[1]) && /^\d{1,2}$/.test(parts[2])) {
            var y3 = parseInt(parts[0], 10), m3 = parseInt(parts[1], 10), d3 = parseInt(parts[2], 10);
            var dt = new Date(y3, m3 - 1, d3);
            if (dt.getFullYear() === y3 && dt.getMonth() === m3 - 1 && dt.getDate() === d3 && y3 >= 1950) {
                var notFuture = y3 < cy || (y3 === cy && (m3 < cm || (m3 === cm && d3 <= cd)));
                if (notFuture) {
                    return { ok: true, iso: parts[0] + '-' + String(m3).padStart(2, '0') + '-' + String(d3).padStart(2, '0') };
                }
            }
            return { ok: false, iso: '' };
        }
        return { ok: false, iso: '' };
    }

    function displayDate(iso) {
        if (!iso) { return ''; }
        var p = String(iso).split('-');
        if (p.length === 3) { return p[2] + '.' + p[1] + '.' + p[0]; }
        if (p.length === 2) { return p[1] + '.' + p[0]; }
        return p[0];
    }

    function describeIntervention(iv) {
        var shortMap = (catalog && catalog.segment_short) || {};
        var typeLabels = pairMap(catalog && catalog.types);
        var details = [];
        var title = '', statusLabel = '', tone = 'ok';

        if (iv.type === 'bypass') {
            var prox = shortMap[iv.proximal] || iv.proximal;
            var dist = shortMap[iv.distal] || iv.distal;
            title = 'შუნტირება: ' + prox + ' → ' + dist;
            var cMap = {};
            if (catalog && catalog.conduits) {
                Object.keys(catalog.conduits).forEach(function (g) {
                    catalog.conduits[g].forEach(function (pair) { cMap[pair[0]] = pair[1]; });
                });
            }
            if (iv.conduit && cMap[iv.conduit]) {
                details.push(cMap[iv.conduit]);
            } else if (iv.conduit_group && iv.conduit_group !== 'unspecified') {
                details.push(pairMap(catalog.conduit_groups)[iv.conduit_group] || iv.conduit_group);
            } else {
                details.push('კონდუიტი დაუზუსტებელია');
            }
            if (iv.cuff === 'miller') { details.push('Miller-ის მანჟეტი'); }
            statusLabel = pairMap(catalog && catalog.bypass_status)[iv.status] || iv.status;
            if (iv.status === 'stenosed' && iv.site) {
                statusLabel += ' (' + (pairMap(catalog && catalog.bypass_sites)[iv.site] || iv.site) + ')';
            }
            tone = iv.status === 'patent' ? 'ok' : (iv.status === 'stenosed' ? 'warn' : 'bad');
        } else if (iv.type === 'amputation') {
            title = pairMap(catalog && catalog.subtypes && catalog.subtypes.amputation)[iv.subtype] || typeLabels.amputation || '';
            statusLabel = pairMap(catalog && catalog.statuses && catalog.statuses.amputation)[iv.status] || iv.status;
            tone = iv.status === 'healed' ? 'ok' : 'bad';
        } else {
            var segNames = (iv.segments || []).map(function (s) { return shortMap[s] || s; }).join(', ');
            var kindShort = iv.type === 'angioplasty' ? 'ანგიოპლასტიკა' :
                (iv.type === 'endarterectomy' ? 'ენდარტერექტომია' : (typeLabels[iv.type] || iv.type));
            title = kindShort + ': ' + segNames;
            var subMap = pairMap(catalog && catalog.subtypes && catalog.subtypes[iv.type]);
            if (iv.subtype && iv.subtype !== 'unspecified' && subMap[iv.subtype]) {
                details.push(subMap[iv.subtype]);
            }
            statusLabel = pairMap(catalog && catalog.statuses && catalog.statuses[iv.type])[iv.status] || iv.status;
            tone = iv.status === 'patent' ? 'ok' : (iv.status === 'occluded' ? 'bad' : 'warn');
        }
        return {
            title: title,
            detail: details.join(' · '),
            date: displayDate(iv.performed_on),
            statusLabel: statusLabel,
            tone: tone,
            note: iv.note || ''
        };
    }

    function syncLumenFromInterventions() {
        var inline = interventions.filter(function (iv) { return iv && INLINE_TYPES.indexOf(iv.type) !== -1; });
        if (!inline.length) { return; }
        var datedSlots = [];
        inline.forEach(function (iv, idx) { if (iv.performed_on) { datedSlots.push(idx); } });
        var sortedDated = datedSlots.map(function (i) { return inline[i]; }).sort(function (a, b) {
            return a.performed_on < b.performed_on ? -1 : (a.performed_on > b.performed_on ? 1 : 0);
        });
        var ordered = inline.slice();
        datedSlots.forEach(function (slot, k) { ordered[slot] = sortedDated[k]; });

        var latest = {};
        ordered.forEach(function (iv) {
            (iv.segments || []).forEach(function (s) { latest[s] = iv; });
        });

        ordered.forEach(function (iv) {
            var active = (iv.segments || []).filter(function (s) { return latest[s] === iv; });
            if (!active.length) { return; }
            if (iv.status === 'patent') {
                active.forEach(function (s) {
                    var cur = segments[s];
                    if (cur && (cur.stenosis === 'moderate' || cur.stenosis === 'severe' || cur.stenosis === 'occlusion')) {
                        delete cur.stenosis;
                        delete cur.flush;
                        delete cur.reconstitution;
                        delete cur.morph;
                        if (!cur.aneurysm) { delete cur.length_cm; }
                        if (!Object.keys(cur).length) { delete segments[s]; }
                    }
                });
            } else if (iv.status === 'isr_1' || iv.status === 'isr_2' || iv.status === 'restenosed') {
                active.forEach(function (s) {
                    if (segments[s] && segments[s].stenosis === 'occlusion') {
                        segments[s].stenosis = 'severe';
                        delete segments[s].flush;
                        delete segments[s].reconstitution;
                    }
                });
                var hasSten = active.some(function (s) {
                    var st = segments[s] && segments[s].stenosis;
                    return st === 'moderate' || st === 'severe';
                });
                if (!hasSten) {
                    active.forEach(function (s) {
                        segments[s] = segments[s] || {};
                        segments[s].stenosis = 'severe';
                    });
                }
            } else if (iv.status === 'occluded') {
                var hasOc = active.some(function (s) { return segments[s] && segments[s].stenosis === 'occlusion'; });
                if (!hasOc) {
                    active.forEach(function (s) {
                        segments[s] = segments[s] || {};
                        segments[s].stenosis = 'occlusion';
                    });
                }
            }
        });
    }

    function renderInterventionsList() {
        if (!listEl || !emptyHint) { return; }
        listEl.textContent = '';
        emptyHint.hidden = interventions.length > 0;
        interventions.forEach(function (iv) {
            var info = describeIntervention(iv);
            var li = document.createElement('li');
            li.className = 'st-iv-item tone-' + info.tone;

            var row = document.createElement('div');
            row.className = 'st-iv-row';

            var main = document.createElement('div');
            main.className = 'st-iv-main';
            var b = document.createElement('b');
            b.textContent = info.title;
            main.appendChild(b);
            if (info.detail) {
                var det = document.createElement('span');
                det.className = 'st-iv-detail';
                det.textContent = ' · ' + info.detail;
                main.appendChild(det);
            }
            if (info.date) {
                var tm = document.createElement('time');
                tm.className = 'st-iv-date';
                tm.textContent = info.date;
                main.appendChild(tm);
            }
            var badge = document.createElement('em');
            badge.className = 'st-iv-badge tone-' + info.tone;
            badge.textContent = info.statusLabel;
            main.appendChild(badge);
            row.appendChild(main);

            var btns = document.createElement('div');
            btns.className = 'st-iv-item-btns';
            var editB = document.createElement('button');
            editB.type = 'button';
            editB.className = 'x-btn sm';
            editB.textContent = 'შეცვლა';
            editB.addEventListener('click', function () { openEditor(iv); });

            var delB = document.createElement('button');
            delB.type = 'button';
            delB.className = 'x-btn sm danger';
            delB.setAttribute('aria-label', 'წაშლა');
            delB.textContent = '×';
            delB.addEventListener('click', function () {
                interventions = interventions.filter(function (x) { return x.id !== iv.id; });
                if (editingId === iv.id) { closeEditor(); }
                markFormDirty();
                renderInterventionsList();
                renderInspector();
                paintAll(true);
                sync();
                preview(true);
            });
            btns.appendChild(editB);
            btns.appendChild(delB);
            row.appendChild(btns);
            li.appendChild(row);

            if (info.note) {
                var noteEl = document.createElement('div');
                noteEl.className = 'st-iv-note';
                noteEl.textContent = info.note;
                li.appendChild(noteEl);
            }
            listEl.appendChild(li);
        });
    }

    function fillSelect(sel, pairs, selected) {
        if (!sel) { return; }
        sel.textContent = '';
        (pairs || []).forEach(function (p) {
            var opt = document.createElement('option');
            opt.value = p[0];
            opt.textContent = p[1];
            if (p[0] === selected) { opt.selected = true; }
            sel.appendChild(opt);
        });
    }

    function fillVesselSelect(sel, includeExtra, selected) {
        if (!sel || !catalog) { return; }
        sel.textContent = '';
        if (includeExtra && catalog.extra_origins && catalog.extra_origins.length) {
            var extraGrp = document.createElement('optgroup');
            extraGrp.label = 'ექსტრაანატომიური სათავე';
            catalog.extra_origins.forEach(function (p) {
                var opt = document.createElement('option');
                opt.value = p[0];
                opt.textContent = p[1];
                if (p[0] === selected) { opt.selected = true; }
                extraGrp.appendChild(opt);
            });
            sel.appendChild(extraGrp);
        }
        (catalog.segment_groups || []).forEach(function (grp) {
            var og = document.createElement('optgroup');
            og.label = grp.label;
            grp.items.forEach(function (it) {
                var opt = document.createElement('option');
                opt.value = it[0];
                opt.textContent = it[1] + ' — ' + it[2];
                if (it[0] === selected) { opt.selected = true; }
                og.appendChild(opt);
            });
            sel.appendChild(og);
        });
    }

    var elTypes = document.getElementById('st-iv-types');
    var elBypassBox = document.getElementById('st-iv-bypass-fields');
    var elConduitGrp = document.getElementById('st-iv-conduit-group');
    var elConduit = document.getElementById('st-iv-conduit');
    var elCuffWrap = document.getElementById('st-iv-cuff-wrap');
    var elCuff = document.getElementById('st-iv-cuff');
    var elProximal = document.getElementById('st-iv-proximal');
    var elDistal = document.getElementById('st-iv-distal');
    var elSubtypeWrap = document.getElementById('st-iv-subtype-wrap');
    var elSubtypeLabel = document.getElementById('st-iv-subtype-label');
    var elSubtype = document.getElementById('st-iv-subtype');
    var elSegWrap = document.getElementById('st-iv-segments-wrap');
    var elSegPicker = document.getElementById('st-iv-seg-picker');
    var elStatus = document.getElementById('st-iv-status');
    var elSiteWrap = document.getElementById('st-iv-site-wrap');
    var elSite = document.getElementById('st-iv-site');
    var elDate = document.getElementById('st-iv-date');
    var elNote = document.getElementById('st-iv-note');
    var elSyncHint = document.getElementById('st-iv-sync-hint');
    var elErr = document.getElementById('st-iv-err');
    var elCommit = document.getElementById('st-iv-commit');
    var elCancel = document.getElementById('st-iv-cancel');

    function renderSegPicker() {
        if (!elSegPicker || !catalog) { return; }
        elSegPicker.textContent = '';
        (catalog.segment_groups || []).forEach(function (grp) {
            var row = document.createElement('div');
            row.className = 'st-iv-seg-group';
            var lbl = document.createElement('small');
            lbl.textContent = grp.label;
            row.appendChild(lbl);
            var chips = document.createElement('div');
            chips.className = 'st-iv-chips';
            grp.items.forEach(function (it) {
                var key = it[0];
                var btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'st-iv-chip' + (selectedSegs[key] ? ' on' : '');
                btn.textContent = it[1];
                btn.title = it[2];
                btn.setAttribute('data-iv-seg', key);
                btn.setAttribute('aria-pressed', selectedSegs[key] ? 'true' : 'false');
                btn.addEventListener('click', function () {
                    editorDirty = true;
                    if (selectedSegs[key]) { delete selectedSegs[key]; } else { selectedSegs[key] = true; }
                    btn.classList.toggle('on', !!selectedSegs[key]);
                    btn.setAttribute('aria-pressed', selectedSegs[key] ? 'true' : 'false');
                    if (elErr) { elErr.hidden = true; }
                });
                chips.appendChild(btn);
            });
            row.appendChild(chips);
            elSegPicker.appendChild(row);
        });
    }

    function updateConduitOptions(selectedConduit) {
        if (!catalog || !elConduitGrp || !elConduit) { return; }
        var grp = elConduitGrp.value || 'vein';
        var opts = catalog.conduits[grp] || [];
        fillSelect(elConduit, opts, selectedConduit || (opts[0] && opts[0][0]) || '');
        if (elCuffWrap) { elCuffWrap.hidden = grp === 'vein'; }
        if (grp === 'vein' && elCuff) { elCuff.checked = false; }
    }

    function updateSiteVisibility() {
        if (!elSiteWrap || !elStatus) { return; }
        elSiteWrap.hidden = !(currentType === 'bypass' && elStatus.value === 'stenosed');
    }

    function switchType(type, preset) {
        if (!catalog) { return; }
        currentType = type;
        if (elErr) { elErr.hidden = true; }
        if (elTypes) {
            elTypes.querySelectorAll('button').forEach(function (b) {
                var active = b.getAttribute('data-iv-type') === type;
                b.classList.toggle('on', active);
                b.setAttribute('aria-checked', active ? 'true' : 'false');
            });
        }
        var isBypass = type === 'bypass';
        var isAmp = type === 'amputation';
        var isInline = INLINE_TYPES.indexOf(type) !== -1;

        if (elBypassBox) { elBypassBox.hidden = !isBypass; }
        if (elSubtypeWrap) { elSubtypeWrap.hidden = isBypass; }
        if (elSegWrap) { elSegWrap.hidden = !isInline; }
        if (elSyncHint) { elSyncHint.hidden = !isInline; }

        if (isBypass) {
            var grp = (preset && preset.conduit_group && preset.conduit_group !== 'unspecified') ? preset.conduit_group : 'vein';
            fillSelect(elConduitGrp, catalog.conduit_groups, grp);
            updateConduitOptions(preset && preset.conduit);
            if (elCuff) { elCuff.checked = !!(preset && preset.cuff === 'miller'); }
            fillVesselSelect(elProximal, true, (preset && preset.proximal) || 'cfa');
            fillVesselSelect(elDistal, false, (preset && preset.distal) || 'pop3');
            fillSelect(elSite, catalog.bypass_sites, (preset && preset.site) || '');
        } else {
            var subTitle = isAmp ? 'ამპუტაციის დონე' : 'მეთოდი / ქვეტიპი';
            if (elSubtypeLabel) { elSubtypeLabel.textContent = subTitle; }
            var subOpts = catalog.subtypes[type] || [];
            var chosenSub = (preset && preset.subtype && preset.subtype !== 'unspecified')
                ? preset.subtype : (subOpts[0] && subOpts[0][0]) || '';
            fillSelect(elSubtype, subOpts, chosenSub);
            if (isInline) {
                selectedSegs = {};
                if (preset && Array.isArray(preset.segments)) {
                    preset.segments.forEach(function (s) { selectedSegs[s] = true; });
                } else if (type === 'endarterectomy') {
                    selectedSegs.cfa = true;
                }
                renderSegPicker();
            }
        }
        var stOpts = catalog.statuses[type] || [];
        fillSelect(elStatus, stOpts, (preset && preset.status) || (stOpts[0] && stOpts[0][0]) || 'patent');
        updateSiteVisibility();
    }

    function nextId() {
        var used = {};
        interventions.forEach(function (iv) { if (iv && iv.id) { used[iv.id] = true; } });
        var n = interventions.length + 1;
        while (used['iv' + n]) { n += 1; }
        return 'iv' + n;
    }

    function openEditor(existing) {
        if (!editor || !catalog) { return; }
        editingId = existing ? existing.id : null;
        editorDirty = !!existing;
        editor.hidden = false;
        if (elCommit) { elCommit.textContent = existing ? 'განახლება' : 'ჩამატება'; }
        if (elDate) { elDate.value = existing ? displayDate(existing.performed_on) : ''; }
        if (elNote) { elNote.value = (existing && existing.note) || ''; }
        switchType(existing ? existing.type : 'bypass', existing || null);
    }

    function closeEditor() {
        if (!editor) { return; }
        editor.hidden = true;
        editingId = null;
        editorDirty = false;
        if (elErr) { elErr.hidden = true; }
    }

    function showError(msg) {
        if (!elErr) { return; }
        elErr.textContent = msg;
        elErr.hidden = false;
    }

    function commitEditor() {
        if (!catalog) { return false; }
        var dtCheck = normalizePartialDate(elDate ? elDate.value : '');
        if (!dtCheck.ok) {
            showError('თარიღი ჩაწერეთ ფორმატით: 2023, 06.2023 ან 15.06.2023 (მომავალი თარიღის გარეშე).');
            return false;
        }
        var id = editingId || nextId();
        var note = String(elNote ? elNote.value : '').replace(/\s+/g, ' ').trim().slice(0, 200);
        var rec;

        if (currentType === 'bypass') {
            var prox = elProximal ? elProximal.value : 'cfa';
            var dist = elDistal ? elDistal.value : 'pop3';
            var st = elStatus ? elStatus.value : 'patent';
            rec = {
                id: id,
                type: 'bypass',
                conduit_group: elConduitGrp ? elConduitGrp.value : 'vein',
                conduit: elConduit ? elConduit.value : '',
                cuff: (elCuff && !elCuffWrap.hidden && elCuff.checked) ? 'miller' : '',
                proximal: prox,
                distal: dist,
                performed_on: dtCheck.iso,
                status: st,
                site: (st === 'stenosed' && elSite) ? elSite.value : '',
                note: note
            };
        } else if (currentType === 'amputation') {
            rec = {
                id: id,
                type: 'amputation',
                subtype: elSubtype ? elSubtype.value : 'toes',
                performed_on: dtCheck.iso,
                status: elStatus ? elStatus.value : 'healed',
                note: note
            };
        } else {
            var order = catalog.segment_order || [];
            var chosen = order.filter(function (k) { return !!selectedSegs[k]; });
            if (!chosen.length) {
                showError('აირჩიეთ მინიმუმ ერთი არტერიული სეგმენტი.');
                return false;
            }
            rec = {
                id: id,
                type: currentType,
                subtype: elSubtype ? elSubtype.value : '',
                segments: chosen,
                performed_on: dtCheck.iso,
                status: elStatus ? elStatus.value : 'patent',
                note: note
            };
        }

        if (editingId) {
            interventions = interventions.map(function (iv) { return iv.id === editingId ? rec : iv; });
        } else {
            if (interventions.length >= 20) {
                showError('ერთ კიდურზე მაქსიმუმ 20 ჩარევა ინახება.');
                return false;
            }
            interventions.push(rec);
        }

        syncLumenFromInterventions();
        closeEditor();
        markFormDirty();
        renderInterventionsList();
        renderInspector();
        paintAll(true);
        sync();
        preview(true);
        return true;
    }

    if (catalog && elTypes) {
        catalog.types.forEach(function (pair) {
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'st-iv-type-btn';
            btn.setAttribute('role', 'radio');
            btn.setAttribute('data-iv-type', pair[0]);
            btn.textContent = pair[1];
            btn.addEventListener('click', function () {
                editorDirty = true;
                switchType(pair[0], null);
            });
            elTypes.appendChild(btn);
        });
    }
    if (addBtn) {
        addBtn.addEventListener('click', function () { openEditor(null); });
    }
    if (elConduitGrp) {
        elConduitGrp.addEventListener('change', function () {
            editorDirty = true;
            updateConduitOptions('');
        });
    }
    if (elStatus) {
        elStatus.addEventListener('change', function () {
            editorDirty = true;
            updateSiteVisibility();
        });
    }
    if (editor) {
        editor.addEventListener('input', function () { editorDirty = true; if (elErr) { elErr.hidden = true; } });
        editor.addEventListener('change', function () { editorDirty = true; if (elErr) { elErr.hidden = true; } });
    }
    if (elCommit) {
        elCommit.addEventListener('click', commitEditor);
    }
    if (elCancel) {
        elCancel.addEventListener('click', closeEditor);
    }

    // ---- 1-click fill from map suggestion (never silent) ------------------

    function setRadio(name, val) {
        var strVal = String(val == null ? '' : val);
        form.querySelectorAll('input[type="radio"][name="' + name + '"]').forEach(function (r) {
            r.checked = (r.value === strVal);
        });
    }

    function setCheck(name, checked) {
        var cb = form.querySelector('input[type="checkbox"][name="' + name + '"]');
        if (cb) { cb.checked = !!checked; }
    }

    form.addEventListener('click', function (e) {
        var btn = e.target.closest && e.target.closest('#st-apply-sug');
        if (!btn) { return; }
        setRadio('tap', btn.getAttribute('data-tap'));
        setRadio('fp', btn.getAttribute('data-fp'));
        setCheck('fp_calc', btn.getAttribute('data-fp-calc') === '1');
        setRadio('ip', btn.getAttribute('data-ip'));
        setCheck('ip_calc', btn.getAttribute('data-ip-calc') === '1');
        setRadio('pedal', btn.getAttribute('data-pedal'));
        var aiSel = form.querySelector('select[name="ai"]');
        if (aiSel) { aiSel.value = btn.getAttribute('data-ai') || ''; }
        setRadio('cfa', btn.getAttribute('data-cfa') || 'A');
        markFormDirty();
        preview(false);
    });

    // ---- Live Angiography Protocol (single / bilateral + 1-click Copy) ----

    var protoTextEl = document.getElementById('st-proto-text');
    var protoScopeWrap = document.getElementById('st-proto-scope-wrap');
    var copyProtoBtn = document.getElementById('st-copy-proto');
    var protoSingle = readJson('st-proto-single-init', protoTextEl ? protoTextEl.value : '');
    var protoBi = readJson('st-proto-bi-init', '');
    var protoScope = 'single';
    var copyTimer = null;

    function renderProtocol() {
        if (!protoTextEl) { return; }
        if (protoScopeWrap) { protoScopeWrap.hidden = !protoBi; }
        if (protoScope === 'bilateral' && !protoBi) { protoScope = 'single'; }
        protoTextEl.value = (protoScope === 'bilateral' && protoBi) ? protoBi : protoSingle;
        if (protoScopeWrap) {
            protoScopeWrap.querySelectorAll('[data-proto-scope]').forEach(function (b) {
                b.classList.toggle('on', b.getAttribute('data-proto-scope') === protoScope);
            });
        }
    }

    if (protoScopeWrap) {
        protoScopeWrap.querySelectorAll('[data-proto-scope]').forEach(function (b) {
            b.addEventListener('click', function () {
                protoScope = b.getAttribute('data-proto-scope') || 'single';
                renderProtocol();
            });
        });
    }

    if (copyProtoBtn && protoTextEl) {
        copyProtoBtn.addEventListener('click', function () {
            var text = protoTextEl.value || '';
            var labelSpan = copyProtoBtn.querySelector('span');
            function markCopied() {
                copyProtoBtn.classList.add('copied');
                if (labelSpan) { labelSpan.textContent = '✓ დაკოპირდა'; }
                clearTimeout(copyTimer);
                copyTimer = setTimeout(function () {
                    copyProtoBtn.classList.remove('copied');
                    if (labelSpan) { labelSpan.textContent = 'პროტოკოლის კოპირება'; }
                }, 2000);
            }
            function fallbackCopy() {
                protoTextEl.focus();
                protoTextEl.select();
                try { document.execCommand('copy'); } catch (e) {}
                markCopied();
            }
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(markCopied).catch(fallbackCopy);
            } else {
                fallbackCopy();
            }
        });
    }

    // ---- Live server preview & form submission ----------------------------

    var timer = null, seq = 0, needMap = false;
    function preview(refreshMap) {
        if (refreshMap) { needMap = true; }
        clearTimeout(timer);
        timer = setTimeout(function () {
            sync();
            var data = new FormData(form);
            data.append('preview', '1');
            var mine = ++seq;
            var swapMap = needMap;
            fetch(form.getAttribute('data-preview'), { method: 'POST', body: data, credentials: 'same-origin',
                headers: { 'X-Requested-With': 'XMLHttpRequest' } })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (r) {
                    if (!r || mine !== seq) { return; }
                    var old = document.getElementById('st-res');
                    var box = document.createElement('div');
                    box.innerHTML = r.html;
                    old.replaceWith(box.firstElementChild);
                    if (typeof r.protocol_text === 'string') {
                        protoSingle = r.protocol_text;
                        protoBi = r.protocol_bilateral_text || '';
                        renderProtocol();
                    }
                    if (swapMap) {
                        needMap = false;
                        var activeEl = document.activeElement;
                        var activeSeg = activeEl && activeEl.getAttribute ? activeEl.getAttribute('data-seg') : null;
                        var focusInFoot = !!(activeSeg && footBox && footBox.contains(activeEl));
                        var focusInMap = !!(activeSeg && mapBox && mapBox.contains(activeEl));
                        if (r.map_html && mapBox) {
                            var mwrap = document.createElement('div');
                            mwrap.innerHTML = r.map_html;
                            var oldSvg = mapBox.querySelector('svg.am');
                            if (oldSvg && mwrap.firstElementChild) {
                                oldSvg.replaceWith(mwrap.firstElementChild);
                                applyZoom();
                                if (focusInMap) {
                                    var nextFocus = mapBox.querySelector('[data-seg="' + activeSeg + '"]');
                                    if (nextFocus) { nextFocus.focus(); }
                                }
                            }
                        }
                        if (r.foot_html && footBox) {
                            var fwrap = document.createElement('div');
                            fwrap.innerHTML = r.foot_html;
                            var oldFoot = footBox.querySelector('svg.am-foot');
                            if (oldFoot && fwrap.firstElementChild) {
                                oldFoot.replaceWith(fwrap.firstElementChild);
                                if (focusInFoot) {
                                    var nextFootFocus = footBox.querySelector('[data-seg="' + activeSeg + '"]');
                                    if (nextFootFocus) { nextFootFocus.focus(); }
                                }
                            }
                        }
                        highlightInspected();
                    }
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

    form.addEventListener('change', function (e) {
        if ((editor && editor.contains(e.target)) || (inspector && inspector.contains(e.target))) { return; }
        markFormDirty();
        preview(Boolean(e.target && e.target.name === 'wound_angiosome'));
    });
    form.addEventListener('input', function (e) {
        if ((editor && editor.contains(e.target)) || (inspector && inspector.contains(e.target))) { return; }
        markFormDirty();
        if (e.target.tagName === 'INPUT' && e.target.type !== 'radio') { preview(false); }
    });
    form.addEventListener('submit', function (e) {
        if (editor && !editor.hidden && editorDirty) {
            if (!commitEditor()) {
                e.preventDefault();
                editor.scrollIntoView({ behavior: 'smooth', block: 'center' });
                return;
            }
        }
        sync();
        isSubmitting = true;
    });

    window.addEventListener('beforeunload', function (e) {
        if ((formDirty || editorDirty) && !isSubmitting && !allowLeave) {
            e.preventDefault();
            e.returnValue = '';
        }
    });
    document.addEventListener('click', function (e) {
        if ((!formDirty && !editorDirty) || isSubmitting || allowLeave) { return; }
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) { return; }
        var link = e.target.closest && e.target.closest('a[href]');
        if (!link || link.hasAttribute('data-allow-leave') || link.target === '_blank' || link.hasAttribute('download')) { return; }
        var href = link.getAttribute('href') || '';
        if (!href || href.charAt(0) === '#' || href.indexOf('javascript:') === 0) { return; }
        if (!window.confirm('ცვლილებები შენახული არ არის. ნამდვილად გსურთ სხვა გვერდზე გადასვლა შენახვის გარეშე?')) {
            e.preventDefault();
        } else {
            allowLeave = true;
        }
    });

    renderInterventionsList();
    renderProtocol();
    paintAll();
    live();
    sync();
}());

